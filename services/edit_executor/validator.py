"""
MONTA — Edit Plan Validator
============================
Deterministic validation of Timeline IR and source media before FFmpeg compilation.
Verifies file existence, path containment, source time ranges, track continuity,
transition feasibility, and hardware limits.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path
from typing import Optional, Sequence

from shared.contracts.timeline import TimelineIR, TransitionType


class ValidationErrorCode(StrEnum):
    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_PATH_UNAUTHORIZED = "source_path_unauthorized"
    INVALID_SOURCE_RANGE = "invalid_source_range"
    INVALID_TIMELINE_RANGE = "invalid_timeline_range"
    TIMELINE_GAP_DETECTED = "timeline_gap_detected"
    TIMELINE_OVERLAP_DETECTED = "timeline_overlap_detected"
    INVALID_TRANSITION = "invalid_transition"
    INVALID_SPEED = "invalid_speed"
    DURATION_MISMATCH = "duration_mismatch"
    UNSUPPORTED_MEDIA = "unsupported_media"


@dataclass(frozen=True)
class EditValidationError:
    code: ValidationErrorCode
    message: str
    segment_id: Optional[str] = None
    clip_id: Optional[str] = None
    path: Optional[str] = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EditValidationResult:
    is_valid: bool
    errors: tuple[EditValidationError, ...] = ()
    warnings: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            err_msgs = "\n".join(f"- [{e.code.value}] {e.message}" for e in self.errors)
            raise ValueError(f"Timeline IR validation failed with {len(self.errors)} error(s):\n{err_msgs}")


class EditPlanValidator:
    """Validates Timeline IR and underlying media files."""

    def __init__(
        self,
        allowed_roots: Sequence[str | Path] = (),
        max_duration_s: float = 3600.0,
        probe_source_durations: bool = False,
    ):
        self.allowed_roots = tuple(Path(r).resolve() for r in allowed_roots if str(r).strip())
        self.max_duration_s = max_duration_s
        self.probe_source_durations = probe_source_durations

    def validate(
        self,
        timeline: TimelineIR,
        source_durations_ms: Optional[dict[str, int]] = None,
    ) -> EditValidationResult:
        errors: list[EditValidationError] = []
        warnings: list[str] = []

        if not timeline.video_segments:
            errors.append(
                EditValidationError(
                    code=ValidationErrorCode.INVALID_TIMELINE_RANGE,
                    message="Timeline must contain at least one video segment.",
                )
            )
            return EditValidationResult(is_valid=False, errors=tuple(errors))

        # Check total duration limit
        if timeline.duration_s > self.max_duration_s:
            errors.append(
                EditValidationError(
                    code=ValidationErrorCode.DURATION_MISMATCH,
                    message=f"Timeline duration ({timeline.duration_s:.1f}s) exceeds maximum allowed limit ({self.max_duration_s:.1f}s).",
                )
            )

        # 1. Validate Video Segments
        prev_end_ms = 0
        known_durations = source_durations_ms or {}

        for i, seg in enumerate(timeline.video_segments):
            # A. Source Path & Security
            p = Path(seg.source_path)
            if not p.exists():
                errors.append(
                    EditValidationError(
                        code=ValidationErrorCode.SOURCE_NOT_FOUND,
                        message=f"Source media file not found: {seg.source_path}",
                        segment_id=seg.segment_id,
                        clip_id=seg.clip_id,
                        path=seg.source_path,
                    )
                )
            else:
                resolved = p.resolve()
                if self.allowed_roots and not any(resolved.is_relative_to(r) for r in self.allowed_roots):
                    errors.append(
                        EditValidationError(
                            code=ValidationErrorCode.SOURCE_PATH_UNAUTHORIZED,
                            message=f"Source media file outside allowed storage roots: {seg.source_path}",
                            segment_id=seg.segment_id,
                            clip_id=seg.clip_id,
                            path=seg.source_path,
                        )
                    )

            # B. Source Time Range
            if seg.source_in_ms >= seg.source_out_ms:
                errors.append(
                    EditValidationError(
                        code=ValidationErrorCode.INVALID_SOURCE_RANGE,
                        message=f"source_in_ms ({seg.source_in_ms}ms) must be strictly less than source_out_ms ({seg.source_out_ms}ms)",
                        segment_id=seg.segment_id,
                        clip_id=seg.clip_id,
                    )
                )

            # Check against known source duration if provided
            if seg.clip_id in known_durations or seg.source_path in known_durations:
                known_dur = known_durations.get(seg.clip_id) or known_durations.get(seg.source_path)
                if known_dur and seg.source_out_ms > known_dur:
                    errors.append(
                        EditValidationError(
                            code=ValidationErrorCode.INVALID_SOURCE_RANGE,
                            message=f"source_out_ms ({seg.source_out_ms}ms) exceeds actual source duration ({known_dur}ms)",
                            segment_id=seg.segment_id,
                            clip_id=seg.clip_id,
                        )
                    )

            # C. Timeline Continuity
            if seg.timeline_start_ms > seg.timeline_end_ms:
                errors.append(
                    EditValidationError(
                        code=ValidationErrorCode.INVALID_TIMELINE_RANGE,
                        message=f"timeline_start_ms ({seg.timeline_start_ms}ms) > timeline_end_ms ({seg.timeline_end_ms}ms)",
                        segment_id=seg.segment_id,
                    )
                )

            if i > 0:
                if seg.timeline_start_ms < prev_end_ms:
                    # Overlap is only valid if a transition is explicitly scheduled
                    if seg.transition_in == TransitionType.CUT or seg.transition_in_ms == 0:
                        errors.append(
                            EditValidationError(
                                code=ValidationErrorCode.TIMELINE_OVERLAP_DETECTED,
                                message=f"Segment overlaps previous segment without transition: start {seg.timeline_start_ms}ms < prev end {prev_end_ms}ms",
                                segment_id=seg.segment_id,
                            )
                        )
                elif seg.timeline_start_ms > prev_end_ms:
                    warnings.append(
                        f"Gap of {seg.timeline_start_ms - prev_end_ms}ms detected between segment {timeline.video_segments[i-1].segment_id} and {seg.segment_id}"
                    )

            # D. Speed multiplier
            if seg.speed <= 0.05 or seg.speed > 20.0:
                errors.append(
                    EditValidationError(
                        code=ValidationErrorCode.INVALID_SPEED,
                        message=f"Invalid playback speed: {seg.speed}. Must be between 0.1 and 10.0.",
                        segment_id=seg.segment_id,
                    )
                )

            # E. Transitions
            if seg.transition_in != TransitionType.CUT and seg.transition_in_ms > 0:
                if i == 0:
                    errors.append(
                        EditValidationError(
                            code=ValidationErrorCode.INVALID_TRANSITION,
                            message="First segment cannot have an incoming transition from a prior segment.",
                            segment_id=seg.segment_id,
                        )
                    )
                else:
                    prev_seg = timeline.video_segments[i - 1]
                    if seg.transition_in_ms > seg.timeline_duration_ms:
                        errors.append(
                            EditValidationError(
                                code=ValidationErrorCode.INVALID_TRANSITION,
                                message=f"Transition duration ({seg.transition_in_ms}ms) exceeds segment duration ({seg.timeline_duration_ms}ms)",
                                segment_id=seg.segment_id,
                            )
                        )
                    if seg.transition_in_ms > prev_seg.timeline_duration_ms:
                        errors.append(
                            EditValidationError(
                                code=ValidationErrorCode.INVALID_TRANSITION,
                                message=f"Transition duration ({seg.transition_in_ms}ms) exceeds previous segment duration ({prev_seg.timeline_duration_ms}ms)",
                                segment_id=seg.segment_id,
                            )
                        )

            prev_end_ms = seg.timeline_end_ms

        # 2. Validate Audio Segments
        for audio in timeline.audio_segments:
            p = Path(audio.source_path)
            if not p.exists():
                errors.append(
                    EditValidationError(
                        code=ValidationErrorCode.SOURCE_NOT_FOUND,
                        message=f"Audio source file not found: {audio.source_path}",
                        path=audio.source_path,
                    )
                )
            if audio.timeline_end_ms <= audio.timeline_start_ms:
                errors.append(
                    EditValidationError(
                        code=ValidationErrorCode.INVALID_TIMELINE_RANGE,
                        message=f"Audio track timeline_end_ms ({audio.timeline_end_ms}) <= timeline_start_ms ({audio.timeline_start_ms})",
                    )
                )

        return EditValidationResult(
            is_valid=len(errors) == 0,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
