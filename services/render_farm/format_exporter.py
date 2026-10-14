"""
MONTA — Platform Format Exporter
===================================
Exports rendered video into platform-specific formats (Instagram Reels,
YouTube Shorts, TikTok, general 16:9) with correct aspect ratio,
duration limits, and encoding requirements.

Uses FFmpeg to crop/pad, trim, and re-encode to each platform's constraints.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.exceptions import RenderError
from shared.hardware.detector import HardwareDetector
from shared.hardware.profile import HardwareProfile

logger = logging.getLogger("monta.render.format_exporter")


@dataclass(frozen=True)
class PlatformSpec:
    """Specification for a target platform."""
    name: str
    aspect_w: int
    aspect_h: int
    max_duration_s: int
    width: int
    height: int
    max_bitrate: str
    audio_bitrate: str = "128k"
    fps: float = 30.0
    container: str = "mp4"


@dataclass(frozen=True)
class ExportResult:
    """Result of a single platform export."""
    platform: str
    output_path: str
    width: int
    height: int
    duration_s: float
    file_size_bytes: int
    success: bool
    error_message: str = ""
    was_trimmed: bool = False


# Platform presets with real specifications
PLATFORM_SPECS: dict[str, PlatformSpec] = {
    "instagram_reels": PlatformSpec(
        name="Instagram Reels", aspect_w=9, aspect_h=16,
        max_duration_s=90, width=1080, height=1920,
        max_bitrate="8000k", audio_bitrate="128k", fps=30.0,
    ),
    "youtube_shorts": PlatformSpec(
        name="YouTube Shorts", aspect_w=9, aspect_h=16,
        max_duration_s=60, width=1080, height=1920,
        max_bitrate="10000k", audio_bitrate="192k", fps=30.0,
    ),
    "tiktok": PlatformSpec(
        name="TikTok", aspect_w=9, aspect_h=16,
        max_duration_s=180, width=1080, height=1920,
        max_bitrate="6000k", audio_bitrate="128k", fps=30.0,
    ),
    "youtube_landscape": PlatformSpec(
        name="YouTube Landscape", aspect_w=16, aspect_h=9,
        max_duration_s=43200, width=1920, height=1080,
        max_bitrate="12000k", audio_bitrate="192k", fps=30.0,
    ),
    "twitter": PlatformSpec(
        name="Twitter/X", aspect_w=16, aspect_h=9,
        max_duration_s=140, width=1920, height=1080,
        max_bitrate="6000k", audio_bitrate="128k", fps=30.0,
    ),
    "square": PlatformSpec(
        name="Square (1:1)", aspect_w=1, aspect_h=1,
        max_duration_s=600, width=1080, height=1080,
        max_bitrate="8000k", audio_bitrate="192k", fps=30.0,
    ),
    "general": PlatformSpec(
        name="General", aspect_w=16, aspect_h=9,
        max_duration_s=7200, width=1920, height=1080,
        max_bitrate="12000k", audio_bitrate="192k", fps=30.0,
    ),
}


class FormatExporter:
    """Export rendered video in platform-specific formats.

    Handles aspect ratio transformation (crop-to-fill for vertical,
    pad-with-bars for landscape), duration trimming to platform limits,
    and platform-optimized re-encoding.
    """

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        hardware_profile: Optional[HardwareProfile] = None,
        timeout_s: float = 600.0,
    ):
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.hardware = hardware_profile or HardwareDetector.detect()
        self.timeout_s = timeout_s

    @staticmethod
    def available_platforms() -> list[str]:
        """Return list of available platform labels."""
        return list(PLATFORM_SPECS.keys())

    @classmethod
    def normalize_platform(cls, platform: str) -> str:
        p = str(platform).strip().lower().replace("-", "_")
        aliases = {
            "instagram": "instagram_reels",
            "insta": "instagram_reels",
            "reels": "instagram_reels",
            "youtube_short": "youtube_shorts",
            "shorts": "youtube_shorts",
            "yt_shorts": "youtube_shorts",
            "youtube": "youtube_landscape",
            "landscape": "youtube_landscape",
            "x": "twitter",
            "general": "youtube_landscape",
        }
        return aliases.get(p, p)

    async def export_for_platform(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        platform: str = "general",
        resolution: str = "1080p",
    ) -> str:
        """Convenience method returning output_path string."""
        norm_p = self.normalize_platform(platform)
        out_dir = Path(output_path).parent if output_path else None
        res = await self.export(input_path, norm_p, output_dir=out_dir)
        if not res.success:
            raise RenderError(res.error_message)
        if output_path and res.output_path != str(output_path):
            import shutil
            shutil.move(res.output_path, str(output_path))
            return str(output_path)
        return res.output_path

    async def export(
        self,
        source_path: str | Path,
        platform: str,
        output_dir: str | Path | None = None,
    ) -> ExportResult:
        """Export video for a specific platform.

        Args:
            source_path: Path to the master rendered MP4.
            platform: Platform label (e.g. "instagram_reels", "tiktok").
            output_dir: Directory for output file. Defaults to same as source.

        Returns:
            ExportResult with path and metadata of the exported file.
        """
        src = Path(source_path).resolve()
        if not src.exists():
            raise RenderError(f"Source file does not exist: {src}")

        norm_p = self.normalize_platform(platform)
        spec = PLATFORM_SPECS.get(norm_p)
        if spec is None:
            return ExportResult(
                platform=platform, output_path="", width=0, height=0,
                duration_s=0.0, file_size_bytes=0, success=False,
                error_message=f"Unknown platform: {platform}. Available: {', '.join(PLATFORM_SPECS.keys())}",
            )

        out_dir = Path(output_dir).resolve() if output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{src.stem}_{platform}.{spec.container}"

        # Probe source dimensions and duration
        src_w, src_h, src_dur = await self._probe_source(src)

        # Determine if we need to trim
        was_trimmed = src_dur > spec.max_duration_s
        trim_dur = min(src_dur, spec.max_duration_s)

        # Build the FFmpeg command
        argv = self._build_export_argv(src, out_path, spec, src_w, src_h, trim_dur, was_trimmed)

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
                # Retry with CPU if HW encoder fails
                encoder = self._resolve_encoder()
                if encoder != "libx264":
                    logger.warning(
                        "HW encoder %s failed for %s export, retrying with CPU",
                        encoder, platform,
                    )
                    argv = self._build_export_argv(
                        src, out_path, spec, src_w, src_h, trim_dur, was_trimmed,
                        force_cpu=True,
                    )
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
                        return ExportResult(
                            platform=platform, output_path=str(out_path),
                            width=spec.width, height=spec.height,
                            duration_s=0.0, file_size_bytes=0, success=False,
                            error_message=f"CPU fallback also failed: {stderr_bytes.decode('utf-8', errors='replace')[-500:]}",
                        )
                else:
                    return ExportResult(
                        platform=platform, output_path=str(out_path),
                        width=spec.width, height=spec.height,
                        duration_s=0.0, file_size_bytes=0, success=False,
                        error_message=f"Export failed: {stderr_text[-500:]}",
                    )

        except asyncio.TimeoutError:
            return ExportResult(
                platform=platform, output_path=str(out_path),
                width=spec.width, height=spec.height,
                duration_s=0.0, file_size_bytes=0, success=False,
                error_message=f"Export timed out after {self.timeout_s}s",
            )
        except Exception as e:
            return ExportResult(
                platform=platform, output_path=str(out_path),
                width=spec.width, height=spec.height,
                duration_s=0.0, file_size_bytes=0, success=False,
                error_message=str(e),
            )

        file_size = out_path.stat().st_size if out_path.exists() else 0
        return ExportResult(
            platform=platform, output_path=str(out_path),
            width=spec.width, height=spec.height,
            duration_s=trim_dur, file_size_bytes=file_size,
            success=True, was_trimmed=was_trimmed,
        )

    async def export_all(
        self,
        source_path: str | Path,
        platforms: list[str],
        output_dir: str | Path | None = None,
    ) -> dict[str, ExportResult]:
        """Export video for multiple platforms sequentially."""
        results = {}
        for platform in platforms:
            results[platform] = await self.export(source_path, platform, output_dir)
        return results

    def _build_export_argv(
        self,
        source: Path,
        output: Path,
        spec: PlatformSpec,
        src_w: int,
        src_h: int,
        duration: float,
        trim: bool,
        force_cpu: bool = False,
    ) -> list[str]:
        """Build FFmpeg argv for platform export."""
        src_ar = src_w / src_h if src_h > 0 else 16 / 9
        target_ar = spec.aspect_w / spec.aspect_h

        # Determine video filter based on aspect ratio relationship
        if abs(src_ar - target_ar) < 0.01:
            # Same aspect ratio — simple scale
            vf = f"scale={spec.width}:{spec.height},setsar=1"
        elif src_ar > target_ar:
            # Source is wider (e.g. 16:9 → 9:16): crop to center then scale
            crop_w = int(src_h * target_ar)
            crop_x = (src_w - crop_w) // 2
            vf = (
                f"crop={crop_w}:{src_h}:{crop_x}:0,"
                f"scale={spec.width}:{spec.height},setsar=1"
            )
        else:
            # Source is taller: crop vertically to center then scale
            crop_h = int(src_w / target_ar)
            crop_y = (src_h - crop_h) // 2
            vf = (
                f"crop={src_w}:{crop_h}:0:{crop_y},"
                f"scale={spec.width}:{spec.height},setsar=1"
            )

        encoder = "libx264" if force_cpu else self._resolve_encoder()
        argv = [
            self.ffmpeg_bin, "-y", "-v", "error", "-nostdin",
            "-i", str(source),
        ]

        if trim:
            argv.extend(["-t", f"{duration:.3f}"])

        argv.extend([
            "-vf", vf,
            "-r", str(spec.fps),
            "-c:v", encoder,
        ])

        if encoder == "h264_videotoolbox":
            argv.extend(["-b:v", spec.max_bitrate])
        elif encoder == "h264_nvenc":
            argv.extend(["-preset", "p4", "-b:v", spec.max_bitrate])
        else:
            argv.extend(["-preset", "medium", "-b:v", spec.max_bitrate])

        argv.extend([
            "-maxrate", spec.max_bitrate,
            "-bufsize", f"{int(spec.max_bitrate.replace('k', '')) * 2}k",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", spec.audio_bitrate,
            "-ar", "48000",
            "-movflags", "+faststart",
            str(output),
        ])
        return argv

    def _resolve_encoder(self) -> str:
        """Select best encoder from hardware profile."""
        if self.hardware:
            return self.hardware.recommended_encoder.value
        return "libx264"

    async def _probe_source(self, path: Path) -> tuple[int, int, float]:
        """Probe source video dimensions and duration."""
        cmd = [
            self.ffprobe_bin, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            import json
            data = json.loads(stdout)

            streams = data.get("streams", [])
            fmt = data.get("format", {})
            video = next((s for s in streams if s.get("codec_type") == "video"), None) or (streams[0] if streams else {})
            width = int(video.get("width", 1920))
            height = int(video.get("height", 1080))
            duration = float(video.get("duration", 0) or fmt.get("duration", 0) or 0)
            return width, height, duration
        except Exception as e:
            logger.warning("ffprobe failed: %s", e)
            return 1920, 1080, 60.0
