"""
Tests for deterministic FFmpegCompiler.
"""

from pathlib import Path
from services.edit_executor.compiler import FFmpegCompiler
from shared.contracts.timeline import (
    AspectRatio,
    ColorAdjustment,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)


def test_compiler_generates_valid_argv(tmp_path: Path):
    seg1 = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path="/media/clip1.mp4",
        source_in_ms=1000,
        source_out_ms=4000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
        color=ColorAdjustment(brightness=0.1, contrast=1.2),
        fade_in_ms=500,
    )
    seg2 = TimelineSegmentIR(
        segment_id="seg_02",
        clip_id="clip_02",
        source_path="/media/clip2.mp4",
        source_in_ms=500,
        source_out_ms=3500,
        timeline_start_ms=3000,
        timeline_end_ms=6000,
        speed=1.5,
    )

    timeline = TimelineIR(
        project_id="proj_001",
        width=1920,
        height=1080,
        fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg1, seg2),
        total_duration_ms=6000,
    )

    compiler = FFmpegCompiler(ffmpeg_bin="ffmpeg")
    cmd = compiler.compile(timeline, output_path=tmp_path / "out.mp4", force_cpu=True)

    argv = cmd.to_list()
    assert argv[0] == "ffmpeg"
    assert "-filter_complex" in argv
    assert "-c:v" in argv
    assert "libx264" in argv
    assert "-c:a" in argv
    assert "aac" in argv
    assert len(cmd.input_files) == 2
    assert "trim=start=1.0000:end=4.0000" in cmd.filter_graph
    assert "eq=brightness=0.100:contrast=1.200" in cmd.filter_graph
    assert "fade=t=in:st=0:d=0.500" in cmd.filter_graph
    assert "atempo=1.5000" in cmd.filter_graph
