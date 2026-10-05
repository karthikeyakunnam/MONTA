"""
Tests for EditPlanValidator.
"""

from pathlib import Path
import pytest

from services.edit_executor.validator import (
    EditPlanValidator,
    ValidationErrorCode,
)
from shared.contracts.timeline import (
    AspectRatio,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)


def test_validator_detects_nonexistent_file(tmp_path: Path):
    seg = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path=str(tmp_path / "does_not_exist.mp4"),
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
    )
    timeline = TimelineIR(
        project_id="proj_01",
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    validator = EditPlanValidator()
    result = validator.validate(timeline)
    assert not result.is_valid
    assert any(e.code == ValidationErrorCode.SOURCE_NOT_FOUND for e in result.errors)


def test_validator_detects_source_range_overflow(tmp_path: Path):
    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"dummy video data")

    seg = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path=str(dummy_file),
        source_in_ms=0,
        source_out_ms=15000,
        timeline_start_ms=0,
        timeline_end_ms=15000,
    )
    timeline = TimelineIR(
        project_id="proj_01",
        video_segments=(seg,),
        total_duration_ms=15000,
    )

    validator = EditPlanValidator()
    # Source is only 10s (10000ms), but segment requests 15s
    result = validator.validate(timeline, source_durations_ms={"clip_01": 10000})
    assert not result.is_valid
    assert any(e.code == ValidationErrorCode.INVALID_SOURCE_RANGE for e in result.errors)


def test_validator_detects_invalid_first_segment_transition(tmp_path: Path):
    dummy_file = tmp_path / "valid.mp4"
    dummy_file.write_bytes(b"dummy video data")

    seg = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path=str(dummy_file),
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
        transition_in=TransitionType.CROSSFADE,
        transition_in_ms=1000,
    )
    timeline = TimelineIR(
        project_id="proj_01",
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    validator = EditPlanValidator()
    result = validator.validate(timeline)
    assert not result.is_valid
    assert any(e.code == ValidationErrorCode.INVALID_TRANSITION for e in result.errors)
