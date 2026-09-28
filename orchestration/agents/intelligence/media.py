"""
MONTA — Media Backend (Layer 6 I/O)
=====================================
All pixel access goes through ``MediaBackend`` so analysis logic never shells
out directly. ``FFmpegMediaBackend`` is the production implementation:

* ``probe``       — ffprobe → ``TechnicalMetadata`` (+ content fingerprint).
* ``sample_luma`` — one decode pass → low-res grayscale frames as a numpy
                    array for signal analysis (bounded frame count).
* ``keyframes``   — JPEG stills at chosen timestamps for vision models.

Hardening:
* Every tool runs in its own process group. On timeout **or** cancellation
  (e.g. a Director deadline) the whole group is SIGTERM'd, escalated to
  SIGKILL, and reaped — no orphans, no zombies.
* Inputs are sandboxed: regular files only (no symlinks, no URLs), optionally
  confined to ``allowed_roots``, and FFmpeg is restricted to the ``file``
  protocol so crafted containers cannot reach the network or other files.
* Duration / resolution limits are enforced at probe time.
"""

import asyncio
import hashlib
import json
import os
import signal
import stat
import time
from fractions import Fraction
from pathlib import Path
from typing import Protocol

import numpy as np

from shared.constants import MAX_CLIP_DURATION_SECONDS
from shared.contracts.clip import ClipSource, TechnicalMetadata
from shared.exceptions import MontaError
from shared.observability import catalog as m
from shared.providers.base import ImagePart

FINGERPRINT_CHUNK = 1 << 20
MAX_PIXELS = 7680 * 4320
PROTOCOL_ARGS = ["-protocol_whitelist", "file,pipe"]


class MediaToolError(MontaError):
    """ffmpeg/ffprobe failure or rejected input. ``retryable`` is True only for timeouts."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class MediaBackend(Protocol):
    async def probe(self, source: ClipSource) -> TechnicalMetadata: ...
    async def sample_luma(self, meta: TechnicalMetadata, *, max_frames: int, width: int) -> tuple[np.ndarray, float]: ...
    async def keyframes(self, meta: TechnicalMetadata, times_s: list[float], *, width: int) -> list[ImagePart]: ...


async def terminate_process_group(proc: asyncio.subprocess.Process, grace_s: float = 2.0) -> None:
    """SIGTERM the process group, escalate to SIGKILL, and reap the leader."""
    if proc.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            await asyncio.shield(asyncio.wait_for(proc.wait(), grace_s))
            return
        except asyncio.TimeoutError:
            continue
    if proc.returncode is None:
        await asyncio.shield(proc.wait())


def fingerprint_file(path: str) -> str:
    """sha256 over size + first and last MiB (cache key). Layer 2 should supply a full-content hash when available."""
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(FINGERPRINT_CHUNK))
        if size > 2 * FINGERPRINT_CHUNK:
            f.seek(-FINGERPRINT_CHUNK, os.SEEK_END)
            h.update(f.read(FINGERPRINT_CHUNK))
    return h.hexdigest()


def _rotation(stream: dict) -> int:
    rotate = (stream.get("tags") or {}).get("rotate")
    if rotate is not None:
        return int(float(rotate)) % 360
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(float(side["rotation"])) % 360
    return 0


def parse_probe(source: ClipSource, data: dict, *, file_size: int, fingerprint: str,
                max_duration_s: float = MAX_CLIP_DURATION_SECONDS) -> TechnicalMetadata:
    """Convert ffprobe JSON into ``TechnicalMetadata``. Raises MediaToolError for unusable or out-of-policy files."""
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaToolError(f"{source.clip_id}: no video stream")
    fmt = data.get("format") or {}
    duration = float(video.get("duration") or fmt.get("duration") or 0)
    if duration <= 0:
        raise MediaToolError(f"{source.clip_id}: could not determine duration")
    if duration > max_duration_s:
        raise MediaToolError(f"{source.clip_id}: duration {duration:.0f}s exceeds limit {max_duration_s:.0f}s")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise MediaToolError(f"{source.clip_id}: unsupported resolution {width}x{height}")
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(rate)) if rate != "0/0" else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if fps <= 0:
        raise MediaToolError(f"{source.clip_id}: invalid frame rate '{rate}'")
    return TechnicalMetadata(
        clip_id=source.clip_id, path=source.path, duration_s=duration, fps=fps,
        width=width, height=height, codec=video.get("codec_name", "unknown"),
        has_audio=any(s.get("codec_type") == "audio" for s in streams), rotation=_rotation(video),
        file_size_bytes=int(fmt.get("size") or file_size), fingerprint=fingerprint,
    )


def display_size(meta: TechnicalMetadata, width: int) -> tuple[int, int]:
    """Scaled (w, h) after ffmpeg autorotation, both even."""
    w, h = (meta.height, meta.width) if meta.rotation in (90, 270) else (meta.width, meta.height)
    out_w = min(width, w) // 2 * 2
    out_h = max(2, round(out_w * h / w / 2) * 2)
    return out_w, out_h


def check_input_path(path: str, allowed_roots: tuple[Path, ...]) -> Path:
    """Reject URLs, symlinks, non-regular files and paths outside ``allowed_roots``."""
    if "://" in path or path.startswith(("-", "concat:", "subfile:", "pipe:")):
        raise MediaToolError(f"refusing non-file media input '{path[:80]}'")
    p = Path(path)
    try:
        st = os.lstat(p)
    except FileNotFoundError as e:
        raise MediaToolError(f"file not found: {path}") from e
    if stat.S_ISLNK(st.st_mode):
        raise MediaToolError(f"refusing symlinked media input: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise MediaToolError(f"not a regular file: {path}")
    resolved = p.resolve()
    if allowed_roots and not any(resolved.is_relative_to(r) for r in allowed_roots):
        raise MediaToolError(f"media input outside allowed storage roots: {path}")
    return resolved


class FFmpegMediaBackend:
    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", timeout_s: float = 120.0,
                 keyframe_concurrency: int = 4, allowed_roots: tuple[str, ...] = (),
                 max_duration_s: float = MAX_CLIP_DURATION_SECONDS):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout_s = timeout_s
        self.allowed_roots = tuple(Path(r).resolve() for r in allowed_roots)
        self.max_duration_s = max_duration_s
        self._kf_sem = asyncio.Semaphore(keyframe_concurrency)

    async def _run(self, cmd: list[str]) -> bytes:
        """Run a media tool in its own process group; the group dies on timeout *or* cancellation."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise MediaToolError(f"{cmd[0]} is not installed") from e
        tool = Path(cmd[0]).name
        t0 = time.perf_counter()
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.timeout_s)
        except asyncio.TimeoutError as e:
            await terminate_process_group(proc)
            m.MEDIA_TOOL.observe(time.perf_counter() - t0, tool=tool, outcome="timeout")
            raise MediaToolError(f"{tool} timed out after {self.timeout_s}s", retryable=True) from e
        except BaseException:  # CancelledError from an outer deadline, KeyboardInterrupt, ...
            await terminate_process_group(proc)
            m.MEDIA_TOOL.observe(time.perf_counter() - t0, tool=tool, outcome="cancelled")
            raise
        m.MEDIA_TOOL.observe(time.perf_counter() - t0, tool=tool, outcome="ok" if proc.returncode == 0 else "error")
        if proc.returncode != 0:
            raise MediaToolError(f"{Path(cmd[0]).name} exited {proc.returncode}: {stderr.decode(errors='replace')[-400:]}")
        return stdout

    async def probe(self, source: ClipSource) -> TechnicalMetadata:
        path = check_input_path(source.path, self.allowed_roots)
        out = await self._run([self.ffprobe, "-v", "error", *PROTOCOL_ARGS, "-print_format", "json",
                               "-show_format", "-show_streams", str(path)])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise MediaToolError(f"{source.clip_id}: unreadable probe output") from e
        fp = await asyncio.to_thread(fingerprint_file, str(path))
        return parse_probe(source, data, file_size=path.stat().st_size, fingerprint=fp, max_duration_s=self.max_duration_s)

    async def sample_luma(self, meta: TechnicalMetadata, *, max_frames: int = 240, width: int = 320) -> tuple[np.ndarray, float]:
        path = check_input_path(meta.path, self.allowed_roots)
        fps = min(4.0, max_frames / meta.duration_s)
        w, h = display_size(meta, width)
        out = await self._run([
            self.ffmpeg, "-v", "error", "-nostdin", *PROTOCOL_ARGS, "-i", str(path), "-an",
            "-vf", f"fps={fps:.6f},scale={w}:{h}:flags=area,format=gray",
            "-frames:v", str(max_frames), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ])
        frame_bytes = w * h
        n = len(out) // frame_bytes
        if n == 0:
            raise MediaToolError(f"{meta.clip_id}: decoded no frames")
        return np.frombuffer(out[: n * frame_bytes], dtype=np.uint8).reshape(n, h, w), fps

    async def _keyframe(self, meta: TechnicalMetadata, path: Path, t: float, width: int) -> ImagePart:
        w, h = display_size(meta, width)
        async with self._kf_sem:
            out = await self._run([
                self.ffmpeg, "-v", "error", "-nostdin", *PROTOCOL_ARGS, "-ss", f"{t:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", f"scale={w}:{h}", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
            ])
        if not out:
            raise MediaToolError(f"{meta.clip_id}: no keyframe at {t:.2f}s")
        return ImagePart(data=out, mime_type="image/jpeg")

    async def keyframes(self, meta: TechnicalMetadata, times_s: list[float], *, width: int = 512) -> list[ImagePart]:
        path = check_input_path(meta.path, self.allowed_roots)
        return list(await asyncio.gather(*(self._keyframe(meta, path, t, width) for t in times_s)))


def keyframe_times(duration_s: float, peak_time_s: float, count: int) -> list[float]:
    """Evenly spaced interior timestamps plus the motion peak, deduplicated, sorted."""
    count = max(1, count)
    times = [duration_s * (i + 0.5) / count for i in range(count)]
    times.append(min(max(0.0, peak_time_s), max(0.0, duration_s - 0.05)))
    times.sort()
    out: list[float] = []
    for t in times:
        if not out or t - out[-1] >= min(0.5, duration_s / (count + 1)):
            out.append(round(t, 3))
    return out
