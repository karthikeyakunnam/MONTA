"""
Tests for Timeline IR contracts and temporal representations.
"""

import pytest

from shared.contracts.timeline import (
    AspectRatio,
    AudioSegmentIR,
    ColorAdjustment,
    RenderProfileIR,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)


def test_timeline_segment_duration():
    seg = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path="/path/to/clip.mp4",
        source_in_ms=1000,
        source_out_ms=4500,
        timeline_start_ms=0,
        timeline_end_ms=3500,
        speed=1.0,
    )
    assert seg.source_duration_ms == 3500
    assert seg.timeline_duration_ms == 3500


def test_timeline_segment_rejects_inverted_range():
    with pytest.raises(ValueError):
        TimelineSegmentIR(
            segment_id="seg_bad",
            clip_id="clip_01",
            source_path="/path/to/clip.mp4",
            source_in_ms=5000,
            source_out_ms=2000,
            timeline_start_ms=0,
            timeline_end_ms=3000,
        )


def test_timeline_ir_validation():
    seg1 = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path="/path/to/clip1.mp4",
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
    )
    seg2 = TimelineSegmentIR(
        segment_id="seg_02",
        clip_id="clip_02",
        source_path="/path/to/clip2.mp4",
        source_in_ms=500,
        source_out_ms=2500,
        timeline_start_ms=3000,
        timeline_end_ms=5000,
        transition_in=TransitionType.CROSSFADE,
        transition_in_ms=500,
    )

    timeline = TimelineIR(
        project_id="proj_001",
        width=1920,
        height=1080,
        fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg1, seg2),
        total_duration_ms=5000,
    )

    assert timeline.duration_s == 5.0
    assert len(timeline.video_segments) == 2
