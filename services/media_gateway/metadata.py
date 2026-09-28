"""
MONTA — Metadata Extraction (Layer 2)
=======================================
ffprobe only — no guessing from file extensions, no imaging libraries.

One ffprobe call per file returns container, duration, fps, codec, bitrate,
resolution, rotation and every audio stream. ffprobe is also the content
check: a renamed executable or archive has no decodable video stream, so it
fails here rather than deeper in the pipeline.
"""

import asyncio
import json
import os
import shutil
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from shared.exceptions import UploadError

PROBE_TIMEOUT_S = 30.0

#: Ceiling on ffprobe processes in flight at once, per process.
#:
#: Async concurrency does not help here: each probe is a real OS process competing for CPU
#: and file descriptors. Without a cap, fifty simultaneous uploads fork fifty ffprobes, every
#: one of them gets slower, and the slowest start tripping ``PROBE_TIMEOUT_S`` — turning a
#: load spike into *rejected uploads* rather than merely slow ones. Queueing instead keeps
#: each probe fast and makes the wait visible as latency, which is the honest failure mode.
DEFAULT_PROBE_CONCURRENCY = max(2, min(8, (os.cpu_count() or 4)))


class MediaProbeError(UploadError):
    """ffprobe could not read the file, or the file carries no usable video."""


class AudioStreamInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    codec: str
    channels: int | None = None
    sample_rate: int | None = None
    bitrate: int | None = None
    language: str | None = None


class MediaMetadata(BaseModel):
    """Everything ffprobe can prove about an uploaded file."""

    model_config = ConfigDict(frozen=True)

    container: str = Field(..., description="ffprobe format_name, e.g. 'mov,mp4,m4a,3gp,3g2,mj2'")
    duration_s: float = Field(..., ge=0)
    fps: float = Field(..., ge=0)
    video_codec: str
    width: int = Field(..., ge=0)
    height: int = Field(..., ge=0)
    bitrate: int | None = Field(None, ge=0, description="Container bitrate in bits/s")
    video_bitrate: int | None = Field(None, ge=0)
    rotation: int = 0
    file_size_bytes: int = Field(..., ge=0)
    audio_streams: tuple[AudioStreamInfo, ...] = ()

    @property
    def containers(self) -> frozenset[str]:
        return frozenset(part.strip().lower() for part in self.container.split(",") if part.strip())

    @property
    def display_size(self) -> tuple[int, int]:
        """Width and height after rotation metadata is applied."""
        return (self.height, self.width) if self.rotation in (90, 270) else (self.width, self.height)

    @property
    def resolution(self) -> str:
        w, h = self.display_size
        return f"{w}x{h}"

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_streams)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict) -> int:
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        parsed = _int(float(tag)) if str(tag).replace("-", "").replace(".", "").isdigit() else None
        if parsed is not None:
            return parsed % 360
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            parsed = _int(float(side["rotation"]))
            if parsed is not None:
                return parsed % 360
    return 0


def parse_probe_output(payload: dict, file_size_bytes: int) -> MediaMetadata:
    """Turn ffprobe JSON into ``MediaMetadata``. Raises ``MediaProbeError`` for non-video files."""
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png", "gif", "bmp")), None)
    if video is None:
        raise MediaProbeError("no decodable video stream found")

    duration = None
    for candidate in (video.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue
    if duration is None or duration <= 0:
        raise MediaProbeError("file has no measurable duration")

    fps = 0.0
    for rate in (video.get("avg_frame_rate"), video.get("r_frame_rate")):
        if not rate or rate in ("0/0", "0/1"):
            continue
        try:
            fps = float(Fraction(rate))
        except (ValueError, ZeroDivisionError):
            continue
        if fps > 0:
            break
    if fps <= 0:
        raise MediaProbeError("file has no measurable frame rate")

    width, height = _int(video.get("width")) or 0, _int(video.get("height")) or 0
    if width <= 0 or height <= 0:
        raise MediaProbeError("video stream has no frame size")

    audio = tuple(
        AudioStreamInfo(
            index=_int(s.get("index")) or 0, codec=s.get("codec_name", "unknown"),
            channels=_int(s.get("channels")), sample_rate=_int(s.get("sample_rate")),
            bitrate=_int(s.get("bit_rate")), language=(s.get("tags") or {}).get("language"),
        )
        for s in streams if s.get("codec_type") == "audio"
    )
    return MediaMetadata(
        container=fmt.get("format_name", "unknown"), duration_s=duration, fps=fps,
        video_codec=video.get("codec_name", "unknown"), width=width, height=height,
        bitrate=_int(fmt.get("bit_rate")), video_bitrate=_int(video.get("bit_rate")),
        rotation=_rotation(video), file_size_bytes=file_size_bytes, audio_streams=audio,
    )


class MetadataExtractor:
    """Runs ffprobe out of process, with a timeout and a killed process group on expiry."""

    def __init__(self, ffprobe: str = "ffprobe", timeout_s: float = PROBE_TIMEOUT_S,
                 max_concurrency: int = DEFAULT_PROBE_CONCURRENCY):
        self.ffprobe = ffprobe
        self.timeout_s = timeout_s
        self.max_concurrency = max(1, max_concurrency)
        # One semaphore per event loop. The extractor is a process-wide singleton, while tests
        # (and any embedded use) run several loops; a semaphore holds futures belonging to the
        # loop that awaited it, so sharing one across loops is a latent hang.
        self._gates: dict[object, asyncio.Semaphore] = {}

    def _gate(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        gate = self._gates.get(loop)
        if gate is None:
            gate = asyncio.Semaphore(self.max_concurrency)
            self._gates[loop] = gate
        return gate

    def available(self) -> bool:
        return shutil.which(self.ffprobe) is not None or os.path.isfile(self.ffprobe)

    async def extract(self, path: str | Path) -> MediaMetadata:
        """Probe one file. Waits for a probe slot when ``max_concurrency`` are already running."""
        async with self._gate():
            return await self._extract(path)

    async def _extract(self, path: str | Path) -> MediaMetadata:
        file_path = Path(path)
        if not file_path.is_file():
            raise MediaProbeError(f"file not found: {file_path.name}")
        size = file_path.stat().st_size
        if size == 0:
            raise MediaProbeError("file is empty")
        cmd = [
            self.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-print_format", "json", "-show_format", "-show_streams", str(file_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
            )
        except FileNotFoundError as e:
            raise MediaProbeError(f"{self.ffprobe} is not installed on the server") from e
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout_s)
        except asyncio.TimeoutError:
            await _kill(proc)
            raise MediaProbeError(f"ffprobe timed out after {self.timeout_s:.0f}s") from None
        except asyncio.CancelledError:
            # Reap the child, then let the cancellation through. Converting it into a domain
            # error would swallow the cancellation: the caller that asked us to stop (a client
            # disconnect, a shutdown, a task group unwinding) would instead see a probe
            # failure and could keep waiting on work that is already being torn down.
            await _kill(proc)
            raise
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip().splitlines()
            raise MediaProbeError(detail[-1][:200] if detail else "ffprobe could not read this file")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise MediaProbeError("ffprobe returned unreadable output") from e
        return parse_probe_output(payload, size)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    import signal

    if proc.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            await asyncio.shield(asyncio.wait_for(proc.wait(), 2.0))
            return
        except asyncio.TimeoutError:
            continue
