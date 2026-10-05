"""
Integration tests executing REAL FFmpeg commands and validating real output media files.
"""

import asyncio
import subprocess
from pathlib import Path
import pytest

from services.edit_executor.compiler import FFmpegCompiler
from services.edit_executor.executor import (
    FFmpegCancelledError,
    FFmpegExecutionError,
    FFmpegExecutor,
    FFmpegTimeoutError,
)
from services.render_farm.output_validator import OutputValidator
from services.render_farm.renderer import MediaRenderer
from shared.contracts.timeline import (
    AspectRatio,
    ColorAdjustment,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)


def _generate_synthetic_clip(path: Path, duration_s: float = 4.0, color: str = "red", freq: int = 440) -> Path:
    """Generates a real valid H.264/AAC MP4 media file with video and audio."""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=640x360:d={duration_s}:r=30",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration_s}:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(path),
    ]
    subprocess.check_call(cmd)
    return path


@pytest.fixture
def synthetic_media(tmp_path: Path):
    clip1 = _generate_synthetic_clip(tmp_path / "clip1.mp4", duration_s=4.0, color="blue", freq=440)
    clip2 = _generate_synthetic_clip(tmp_path / "clip2.mp4", duration_s=4.0, color="green", freq=880)
    return clip1, clip2


@pytest.mark.asyncio
async def test_real_ffmpeg_subclip_and_concat(synthetic_media, tmp_path: Path):
    clip1, clip2 = synthetic_media
    out_file = tmp_path / "rendered_concat.mp4"

    # Multi-subclip from clip1 + one subclip from clip2
    seg1 = TimelineSegmentIR(
        segment_id="s1",
        clip_id="c1",
        source_path=str(clip1),
        source_in_ms=500,
        source_out_ms=2000,
        timeline_start_ms=0,
        timeline_end_ms=1500,
    )
    seg2 = TimelineSegmentIR(
        segment_id="s2",
        clip_id="c1",  # Second subclip from the same source!
        source_path=str(clip1),
        source_in_ms=2500,
        source_out_ms=3500,
        timeline_start_ms=1500,
        timeline_end_ms=2500,
    )
    seg3 = TimelineSegmentIR(
        segment_id="s3",
        clip_id="c2",
        source_path=str(clip2),
        source_in_ms=1000,
        source_out_ms=2500,
        timeline_start_ms=2500,
        timeline_end_ms=4000,
        color=ColorAdjustment(brightness=0.05, contrast=1.1),
    )

    timeline = TimelineIR(
        project_id="test_proj",
        width=640,
        height=360,
        fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg1, seg2, seg3),
        total_duration_ms=4000,
    )

    renderer = MediaRenderer()
    progress_updates = []

    def on_prog(pct, elapsed, stage):
        progress_updates.append((pct, stage))

    result = await renderer.render(timeline, output_path=out_file, on_progress=on_prog)

    assert result.success
    assert result.validation_passed
    assert out_file.exists()
    assert result.file_size_bytes > 5000
    assert result.width == 640
    assert result.height == 360
    assert abs(result.duration_s - 4.0) < 0.5
    assert result.video_codec in ("h264", "avc1")
    assert result.audio_codec == "aac"
    assert len(progress_updates) > 0


@pytest.mark.asyncio
async def test_real_ffmpeg_xfade_transitions(synthetic_media, tmp_path: Path):
    clip1, clip2 = synthetic_media
    out_file = tmp_path / "rendered_xfade.mp4"

    seg1 = TimelineSegmentIR(
        segment_id="s1",
        clip_id="c1",
        source_path=str(clip1),
        source_in_ms=0,
        source_out_ms=2500,
        timeline_start_ms=0,
        timeline_end_ms=2500,
    )
    seg2 = TimelineSegmentIR(
        segment_id="s2",
        clip_id="c2",
        source_path=str(clip2),
        source_in_ms=0,
        source_out_ms=2500,
        timeline_start_ms=2500,
        timeline_end_ms=5000,
        transition_in=TransitionType.CROSSFADE,
        transition_in_ms=500,
    )

    timeline = TimelineIR(
        project_id="test_xfade",
        width=640,
        height=360,
        fps=30.0,
        video_segments=(seg1, seg2),
        total_duration_ms=5000,
    )

    renderer = MediaRenderer()
    result = await renderer.render(timeline, output_path=out_file)

    assert result.success
    assert result.validation_passed
    assert out_file.exists()
    assert abs(result.duration_s - 4.5) < 0.6  # 5.0 - 0.5s xfade overlap


@pytest.mark.asyncio
async def test_real_ffmpeg_speed_change(synthetic_media, tmp_path: Path):
    clip1, _ = synthetic_media
    out_file = tmp_path / "rendered_speed.mp4"

    seg = TimelineSegmentIR(
        segment_id="s1",
        clip_id="c1",
        source_path=str(clip1),
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=2000,
        speed=1.5,
    )

    timeline = TimelineIR(
        project_id="test_speed",
        width=640,
        height=360,
        fps=30.0,
        video_segments=(seg,),
        total_duration_ms=2000,
    )

    renderer = MediaRenderer()
    result = await renderer.render(timeline, output_path=out_file)

    assert result.success
    assert result.validation_passed
    assert out_file.exists()


@pytest.mark.asyncio
async def test_output_validator_detects_corrupted_file(tmp_path: Path):
    bad_file = tmp_path / "corrupted.mp4"
    bad_file.write_bytes(b"not a valid mp4 header garbage data" * 100)

    seg = TimelineSegmentIR(
        segment_id="s1",
        clip_id="c1",
        source_path=str(bad_file),
        source_in_ms=0,
        source_out_ms=2000,
        timeline_start_ms=0,
        timeline_end_ms=2000,
    )
    timeline = TimelineIR(
        project_id="test_bad",
        video_segments=(seg,),
        total_duration_ms=2000,
    )

    validator = OutputValidator()
    result = await validator.validate(bad_file, timeline, render_time_s=0.1, encoder_used="libx264")
    assert not result.success
    assert not result.validation_passed
    assert "ffprobe reported corrupted media" in result.error_message or "video stream" in result.error_message


@pytest.mark.asyncio
async def test_executor_timeout_and_cleanup(synthetic_media, tmp_path: Path):
    clip1, _ = synthetic_media
    out_file = tmp_path / "timed_out.mp4"

    seg = TimelineSegmentIR(
        segment_id="s1",
        clip_id="c1",
        source_path=str(clip1),
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
    )
    timeline = TimelineIR(
        project_id="test_timeout",
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    compiler = FFmpegCompiler()
    cmd = compiler.compile(timeline, output_path=out_file, force_cpu=True)

    # Set executor timeout to 0.001s to force timeout
    executor = FFmpegExecutor(timeout_s=0.001)

    with pytest.raises(FFmpegTimeoutError):
        await executor.execute(cmd)

    # Ensure partial file is cleaned up and not left on disk
    assert not out_file.exists()
