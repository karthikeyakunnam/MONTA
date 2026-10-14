"""
MONTA — Multi-Resolution Renderer
====================================
Re-encodes a rendered master file to additional resolutions via FFmpeg.
Produces real, playable MP4s at each target resolution using the same
hardware-aware encoder selection used by the primary render path.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from shared.hardware.detector import HardwareDetector
from shared.hardware.profile import HardwareProfile
from shared.exceptions import RenderError

logger = logging.getLogger("monta.render.multi_res")


@dataclass(frozen=True)
class ResolutionSpec:
    """Target resolution specification."""
    label: str
    width: int
    height: int
    crf: int = 23
    preset: str = "medium"
    video_bitrate: Optional[str] = None  # For HW encoders that need -b:v


@dataclass(frozen=True)
class ResolutionResult:
    """Result of rendering at a single resolution."""
    label: str
    output_path: str
    width: int
    height: int
    file_size_bytes: int
    success: bool
    error_message: str = ""


# Pre-defined resolution presets
RESOLUTION_PRESETS: dict[str, ResolutionSpec] = {
    "360p": ResolutionSpec(label="360p", width=640, height=360, crf=28),
    "480p": ResolutionSpec(label="480p", width=854, height=480, crf=26),
    "720p": ResolutionSpec(label="720p", width=1280, height=720, crf=23),
    "1080p": ResolutionSpec(label="1080p", width=1920, height=1080, crf=22),
    "1440p": ResolutionSpec(label="1440p", width=2560, height=1440, crf=20),
    "4k": ResolutionSpec(label="4k", width=3840, height=2160, crf=18),
}


class MultiResRenderer:
    """Re-encode a master render at multiple resolutions via FFmpeg.

    Skips transcoding for resolutions that match or exceed the source.
    Uses hardware-aware encoder selection with CPU fallback.
    """

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        hardware_profile: Optional[HardwareProfile] = None,
        timeout_s: float = 600.0,
    ):
        self.ffmpeg_bin = ffmpeg_bin
        self.hardware = hardware_profile or HardwareDetector.detect()
        self.timeout_s = timeout_s

    async def render_all(
        self,
        source_path: str | Path,
        resolutions: Sequence[str],
        output_dir: str | Path | None = None,
        on_progress: Optional[Callable[[str, float], None]] = None,
    ) -> dict[str, ResolutionResult]:
        """Render the source video at all requested resolutions.

        Args:
            source_path: Path to the master rendered MP4.
            resolutions: List of resolution labels (e.g. ["720p", "1080p", "4k"]).
            output_dir: Directory for output files. Defaults to same directory as source.
            on_progress: Callback(label, percent) for per-resolution progress.

        Returns:
            Dictionary mapping resolution label to ResolutionResult.
        """
        src = Path(source_path).resolve()
        if not src.exists():
            raise RenderError(f"Source file does not exist: {src}")

        out_dir = Path(output_dir).resolve() if output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        # Probe source dimensions
        src_w, src_h = await self._probe_dimensions(src)

        results: dict[str, ResolutionResult] = {}
        for label in resolutions:
            spec = RESOLUTION_PRESETS.get(label)
            if spec is None:
                results[label] = ResolutionResult(
                    label=label, output_path="", width=0, height=0,
                    file_size_bytes=0, success=False,
                    error_message=f"Unknown resolution preset: {label}",
                )
                continue

            # Skip if source is already at or below this resolution
            if src_w <= spec.width and src_h <= spec.height:
                # Copy source as-is for this resolution
                import shutil
                out_path = out_dir / f"{src.stem}_{label}{src.suffix}"
                shutil.copy2(str(src), str(out_path))
                results[label] = ResolutionResult(
                    label=label, output_path=str(out_path),
                    width=src_w, height=src_h,
                    file_size_bytes=out_path.stat().st_size,
                    success=True,
                )
                logger.info("Resolution %s: source (%dx%d) <= target, copied as-is", label, src_w, src_h)
                if on_progress:
                    on_progress(label, 100.0)
                continue

            out_path = out_dir / f"{src.stem}_{label}{src.suffix}"
            result = await self._transcode(src, out_path, spec, on_progress)
            results[label] = result

        return results

    async def _transcode(
        self,
        source: Path,
        output: Path,
        spec: ResolutionSpec,
        on_progress: Optional[Callable[[str, float], None]] = None,
    ) -> ResolutionResult:
        """Transcode source to a specific resolution."""
        encoder = self._resolve_encoder()
        argv = self._build_transcode_argv(source, output, spec, encoder)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s,
            )

            if proc.returncode != 0:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                # If HW encoder failed, retry with CPU
                if encoder != "libx264":
                    logger.warning(
                        "HW encoder %s failed for %s, retrying with libx264",
                        encoder, spec.label,
                    )
                    argv = self._build_transcode_argv(source, output, spec, "libx264")
                    proc = await asyncio.create_subprocess_exec(
                        *argv,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=self.timeout_s,
                    )
                    if proc.returncode != 0:
                        return ResolutionResult(
                            label=spec.label, output_path=str(output),
                            width=spec.width, height=spec.height,
                            file_size_bytes=0, success=False,
                            error_message=f"CPU fallback also failed: {stderr_bytes.decode('utf-8', errors='replace')[-500:]}",
                        )
                else:
                    return ResolutionResult(
                        label=spec.label, output_path=str(output),
                        width=spec.width, height=spec.height,
                        file_size_bytes=0, success=False,
                        error_message=f"FFmpeg transcode failed: {stderr_text[-500:]}",
                    )

        except asyncio.TimeoutError:
            return ResolutionResult(
                label=spec.label, output_path=str(output),
                width=spec.width, height=spec.height,
                file_size_bytes=0, success=False,
                error_message=f"Transcode timed out after {self.timeout_s}s",
            )
        except Exception as e:
            return ResolutionResult(
                label=spec.label, output_path=str(output),
                width=spec.width, height=spec.height,
                file_size_bytes=0, success=False,
                error_message=str(e),
            )

        if on_progress:
            on_progress(spec.label, 100.0)

        file_size = output.stat().st_size if output.exists() else 0
        return ResolutionResult(
            label=spec.label, output_path=str(output),
            width=spec.width, height=spec.height,
            file_size_bytes=file_size, success=True,
        )

    def _build_transcode_argv(
        self, source: Path, output: Path, spec: ResolutionSpec, encoder: str,
    ) -> list[str]:
        """Build FFmpeg argv for re-encoding at a target resolution."""
        argv = [
            self.ffmpeg_bin, "-y", "-v", "error", "-nostdin",
            "-i", str(source),
            "-vf", f"scale={spec.width}:{spec.height}:force_original_aspect_ratio=decrease,"
                   f"pad={spec.width}:{spec.height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-c:v", encoder,
        ]

        if encoder == "h264_videotoolbox":
            argv.extend(["-b:v", spec.video_bitrate or f"{max(1000, spec.width * spec.height * 30 // 500000)}k"])
        elif encoder == "h264_nvenc":
            argv.extend(["-preset", "p4", "-cq", str(spec.crf)])
        elif encoder == "h264_vaapi":
            argv.extend(["-qp", str(spec.crf)])
        else:
            argv.extend(["-preset", spec.preset, "-crf", str(spec.crf)])

        argv.extend([
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(output),
        ])
        return argv

    def _resolve_encoder(self) -> str:
        """Pick the best encoder from hardware profile."""
        if self.hardware:
            return self.hardware.recommended_encoder.value
        return "libx264"

    async def _probe_dimensions(self, path: Path) -> tuple[int, int]:
        """Probe video dimensions using ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            str(path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            parts = stdout.decode().strip().split("x")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except Exception as e:
            logger.warning("ffprobe dimension probe failed: %s", e)
        return 1920, 1080  # Fallback assumption
