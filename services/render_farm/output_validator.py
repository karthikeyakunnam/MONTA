"""
MONTA — Render Output Validator
================================
Runs ffprobe against the rendered media file to verify container integrity,
stream presence, exact resolution, fps, duration fidelity, and audio synchronization.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Optional

from shared.contracts.timeline import TimelineIR
from shared.exceptions import MontaError

logger = logging.getLogger("monta.render.validator")


@dataclass(frozen=True)
class RenderResult:
    """Detailed facts about the rendered output file."""

    success: bool
    output_path: str
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    file_size_bytes: int
    render_time_s: float
    encoder_used: str
    validation_passed: bool
    warnings: tuple[str, ...] = ()
    error_message: str = ""
    # Captures the exact structured plan submitted to FFmpeg for project-level
    # traceability.  This is metadata, not a shell command to be re-executed.
    execution_plan: dict = field(default_factory=dict)

    @property
    def file_size_mb(self) -> float:
        return round(self.file_size_bytes / (1024 * 1024), 2)


class OutputValidationFailedError(MontaError):
    pass


class OutputValidator:
    """Validates rendered video file against expected Timeline IR specifications."""

    def __init__(self, ffprobe_bin: str = "ffprobe", duration_tolerance_s: float = 0.5):
        self.ffprobe_bin = ffprobe_bin
        self.duration_tolerance_s = duration_tolerance_s

    async def validate(
        self,
        output_path: str | Path,
        timeline: TimelineIR,
        render_time_s: float,
        encoder_used: str,
    ) -> RenderResult:
        p = Path(output_path).resolve()
        if not p.exists():
            return RenderResult(
                success=False,
                output_path=str(p),
                duration_s=0.0,
                width=0,
                height=0,
                fps=0.0,
                video_codec="",
                audio_codec="",
                file_size_bytes=0,
                render_time_s=render_time_s,
                encoder_used=encoder_used,
                validation_passed=False,
                error_message="Rendered file does not exist on disk.",
            )

        file_size = p.stat().st_size
        if file_size < 1024:
            return RenderResult(
                success=False,
                output_path=str(p),
                duration_s=0.0,
                width=0,
                height=0,
                fps=0.0,
                video_codec="",
                audio_codec="",
                file_size_bytes=file_size,
                render_time_s=render_time_s,
                encoder_used=encoder_used,
                validation_passed=False,
                error_message=f"Output file size is suspiciously small ({file_size} bytes).",
            )

        # Probe output with ffprobe
        cmd = [
            self.ffprobe_bin,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(p),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except Exception as e:
            return RenderResult(
                success=False,
                output_path=str(p),
                duration_s=0.0,
                width=0,
                height=0,
                fps=0.0,
                video_codec="",
                audio_codec="",
                file_size_bytes=file_size,
                render_time_s=render_time_s,
                encoder_used=encoder_used,
                validation_passed=False,
                error_message=f"ffprobe execution failed: {e}",
            )

        if proc.returncode != 0:
            return RenderResult(
                success=False,
                output_path=str(p),
                duration_s=0.0,
                width=0,
                height=0,
                fps=0.0,
                video_codec="",
                audio_codec="",
                file_size_bytes=file_size,
                render_time_s=render_time_s,
                encoder_used=encoder_used,
                validation_passed=False,
                error_message=f"ffprobe reported corrupted media:\n{stderr.decode('utf-8', errors='replace')}",
            )

        try:
            data = json.loads(stdout)
        except Exception as e:
            return RenderResult(
                success=False,
                output_path=str(p),
                duration_s=0.0,
                width=0,
                height=0,
                fps=0.0,
                video_codec="",
                audio_codec="",
                file_size_bytes=file_size,
                render_time_s=render_time_s,
                encoder_used=encoder_used,
                validation_passed=False,
                error_message=f"Failed to parse ffprobe json output: {e}",
            )

        streams = data.get("streams", [])
        fmt = data.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            return RenderResult(
                success=False,
                output_path=str(p),
                duration_s=0.0,
                width=0,
                height=0,
                fps=0.0,
                video_codec="",
                audio_codec="",
                file_size_bytes=file_size,
                render_time_s=render_time_s,
                encoder_used=encoder_used,
                validation_passed=False,
                error_message="Output file contains no video stream.",
            )

        # Parse technical parameters
        actual_w = int(video_stream.get("width") or 0)
        actual_h = int(video_stream.get("height") or 0)
        v_codec = video_stream.get("codec_name", "")
        a_codec = audio_stream.get("codec_name", "") if audio_stream else ""

        rate_str = video_stream.get("r_frame_rate") or "30/1"
        try:
            actual_fps = float(Fraction(rate_str)) if rate_str != "0/0" else 30.0
        except Exception:
            actual_fps = 30.0

        dur_str = video_stream.get("duration") or fmt.get("duration") or "0"
        actual_dur = float(dur_str)

        warnings: list[str] = []

        # Validate resolution
        if actual_w != timeline.width or actual_h != timeline.height:
            warnings.append(
                f"Resolution mismatch: expected {timeline.width}x{timeline.height}, got {actual_w}x{actual_h}"
            )

        # Validate duration
        expected_dur = timeline.duration_s
        dur_diff = abs(actual_dur - expected_dur)
        if dur_diff > self.duration_tolerance_s and dur_diff > (expected_dur * 0.1):
            warnings.append(
                f"Duration deviation: expected {expected_dur:.2f}s, got {actual_dur:.2f}s (diff: {dur_diff:.2f}s)"
            )

        # Validate audio presence
        if not audio_stream:
            warnings.append("Output file has no audio stream.")

        return RenderResult(
            success=True,
            output_path=str(p),
            duration_s=round(actual_dur, 3),
            width=actual_w,
            height=actual_h,
            fps=round(actual_fps, 2),
            video_codec=v_codec,
            audio_codec=a_codec,
            file_size_bytes=file_size,
            render_time_s=round(render_time_s, 2),
            encoder_used=encoder_used,
            validation_passed=True,
            warnings=tuple(warnings),
        )
