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
from orchestration.agents.intelligence.media import FFmpegMediaBackend
from shared.contracts.clip import ClipSource
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
async def test_probe_detects_stable_letterbox_without_treating_dark_scene_edges_as_matte(tmp_path: Path):
    boxed = tmp_path / "letterboxed_portrait.mp4"
    subprocess.check_call([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:size=464x256:rate=24:duration=2", "-vf", "pad=478:850:8:298:black",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(boxed),
    ])
    backend = FFmpegMediaBackend(timeout_s=30, allowed_roots=(str(tmp_path),))
    metadata = await backend.probe(ClipSource(clip_id="boxed", path=str(boxed)))
    assert metadata.content_crop == (464, 256, 8, 298)
    assert metadata.content_crop_analyzed is True


@pytest.mark.asyncio
async def test_probe_marks_no_crop_as_analyzed(tmp_path: Path):
    portrait = tmp_path / "full_portrait.mp4"
    subprocess.check_call([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:size=360x640:rate=24:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", str(portrait),
    ])
    backend = FFmpegMediaBackend(timeout_s=30, allowed_roots=(str(tmp_path),))
    metadata = await backend.probe(ClipSource(clip_id="portrait", path=str(portrait)))
    assert metadata.content_crop is None
    assert metadata.content_crop_analyzed is True


@pytest.mark.asyncio
async def test_detected_portrait_matte_is_removed_in_the_actual_render(tmp_path: Path):
    boxed = tmp_path / "portrait_with_wide_active_picture.mp4"
    subprocess.check_call([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "color=c=red:size=464x256:rate=24:duration=2", "-vf", "pad=478:850:8:298:black",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(boxed),
    ])
    metadata = await FFmpegMediaBackend(timeout_s=30, allowed_roots=(str(tmp_path),)).probe(
        ClipSource(clip_id="boxed", path=str(boxed))
    )
    assert metadata.content_crop == (464, 256, 8, 298)

    def timeline(crop):
        segment = TimelineSegmentIR(
            segment_id="boxed-shot", clip_id="boxed", source_path=str(boxed), source_in_ms=0,
            source_out_ms=1500, timeline_start_ms=0, timeline_end_ms=1500, source_crop=crop, has_audio=False,
        )
        return TimelineIR(
            project_id=f"crop-{bool(crop)}", width=270, height=480, fps=24,
            aspect_ratio=AspectRatio.PORTRAIT_9_16, scale_mode=ScaleMode.FIT,
            video_segments=(segment,), total_duration_ms=1500,
        )

    renderer = MediaRenderer()
    baseline_path, cropped_path = tmp_path / "fit_with_matte.mp4", tmp_path / "crop_to_active_picture.mp4"
    baseline = await renderer.render(timeline(None), output_path=baseline_path)
    cropped = await renderer.render(timeline(metadata.content_crop), output_path=cropped_path)
    assert baseline.success and baseline.validation_passed
    assert cropped.success and cropped.validation_passed

    def black_fraction(path: Path) -> float:
        decoded = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True).stdout
        pixels = [decoded[i:i + 3] for i in range(0, len(decoded), 3)]
        return sum(all(channel < 24 for channel in pixel) for pixel in pixels) / len(pixels)

    assert black_fraction(baseline_path) > 0.60
    assert black_fraction(cropped_path) < 0.02


@pytest.mark.asyncio
async def test_studio_color_controls_render_through_ffmpeg(tmp_path: Path):
    source = _generate_synthetic_clip(tmp_path / "grade-source.mp4", duration_s=2.0, color="gray")
    segment = TimelineSegmentIR(
        segment_id="graded-shot", clip_id="grade", source_path=str(source), source_in_ms=0,
        source_out_ms=1500, timeline_start_ms=0, timeline_end_ms=1500,
        color=ColorAdjustment(brightness=-0.04, contrast=1.2, saturation=0.8,
                              shadow_coolness=0.3, highlight_warmth=0.35, vignette=0.25),
    )
    timeline = TimelineIR(
        project_id="studio-color-controls", width=320, height=180, fps=24,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9, scale_mode=ScaleMode.FILL,
        video_segments=(segment,), total_duration_ms=1500,
    )
    baseline_timeline = TimelineIR.model_validate({
        **timeline.model_dump(mode="python"),
        "video_segments": ({**segment.model_dump(mode="python"), "color": None},),
    })
    baseline = await MediaRenderer().render(baseline_timeline, output_path=tmp_path / "baseline.mp4")
    result = await MediaRenderer().render(timeline, output_path=tmp_path / "graded.mp4")
    assert baseline.success and baseline.validation_passed
    assert result.success and result.validation_passed
    assert result.width == 320 and result.height == 180

    def first_frame(path: Path) -> bytes:
        return subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], check=True, capture_output=True).stdout

    assert first_frame(tmp_path / "baseline.mp4") != first_frame(tmp_path / "graded.mp4")


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
