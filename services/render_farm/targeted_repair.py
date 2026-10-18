"""Bounded, timeline-level repairs for confirmed encoded-output defects."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from services.edit_executor.validator import EditPlanValidator
from services.render_farm.output_validator import RenderResult
from shared.contracts.base import revalidate
from shared.contracts.timeline import TimelineIR


@dataclass(frozen=True)
class RepairOutcome:
    timeline: TimelineIR
    output_path: Path
    result: RenderResult
    report: dict
    accepted: bool


async def attempt_black_ending_repair(
    timeline: TimelineIR,
    report: dict,
    current_path: Path,
    current_result: RenderResult,
    candidate_path: Path,
    source_durations_ms: Mapping[str, int],
    render_candidate: Callable[[TimelineIR, Path], Awaitable[RenderResult]],
    inspect_candidate: Callable[[TimelineIR, Path, RenderResult], Awaitable[dict]],
    *,
    manual_override: bool = False,
) -> RepairOutcome:
    """Move only the last source window, once, when a confirmed fade ends the MP4.

    The caller publishes ``output_path`` only if ``accepted`` is true. An
    explicit manual trim is never moved automatically.
    """
    original = RepairOutcome(timeline, current_path, current_result, report, False)
    failed = {check["name"] for check in report["checks"] if check["status"] == "failed"}
    black = next((check for check in report["checks"] if check["name"] == "black_frames"), None)
    if failed != {"black_frames"} or not black or not black["evidence"].get("ending_blackout"):
        return original
    attempts = list(report.get("repair_attempts", []))
    if report.get("repair_attempt"):
        attempts.append(report["repair_attempt"])
    if manual_override:
        report["repair_attempts"] = [*attempts, {
            "cause": "ending_blackout", "status": "unavailable",
            "reason": "Explicit user source trim is preserved; adjust the final shot to avoid its fade",
        }]
        return original

    last = timeline.video_segments[-1]
    shift_ms = min(750, last.source_in_ms)
    earlier_end = max((shot.source_out_ms for shot in timeline.video_segments[:-1]
                       if shot.clip_id == last.clip_id), default=0)
    if shift_ms < 250 or last.source_in_ms - shift_ms < earlier_end:
        report["repair_attempts"] = [*attempts, {
            "cause": "ending_blackout", "status": "unavailable",
            "reason": "The final window cannot move earlier without repeating source frames",
        }]
        return original

    shifted = revalidate(last, source_in_ms=last.source_in_ms - shift_ms,
                         source_out_ms=last.source_out_ms - shift_ms)
    candidate = revalidate(timeline, video_segments=(*timeline.video_segments[:-1], shifted))
    EditPlanValidator().validate(candidate, source_durations_ms=dict(source_durations_ms)).raise_if_invalid()
    attempt = {"cause": "ending_blackout", "timeline_change": f"last source window -{shift_ms}ms",
               "previous_output_path": str(current_path), "previous_check": black}
    rendered = await render_candidate(candidate, candidate_path)
    if not rendered.success or not rendered.validation_passed:
        report["repair_attempts"] = [*attempts, {**attempt, "status": "failed",
            "error": rendered.error_message or "repaired render did not validate"}]
        return original
    checked = await inspect_candidate(candidate, candidate_path, rendered)
    checked["repair_attempts"] = [*attempts, attempt]
    if any(item["status"] == "failed" for item in checked["checks"]):
        report["repair_attempts"] = [*attempts, {**attempt, "status": "failed",
            "error": "Re-encoded candidate did not pass mandatory checks",
            "candidate_report": checked}]
        return original
    return RepairOutcome(candidate, candidate_path, rendered, checked, True)
