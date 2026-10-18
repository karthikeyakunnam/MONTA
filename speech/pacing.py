"""Conservative speech-boundary edits and source-backed captions on Timeline IR."""

from dataclasses import dataclass
from typing import Any

from shared.contracts.base import revalidate
from shared.contracts.timeline import CaptionCueIR, TimelineIR


@dataclass(frozen=True)
class SpeechPlanResult:
    timeline: TimelineIR
    status: str
    decisions: tuple[dict[str, Any], ...]


def _near_phrase_boundary(point_ms: int, segments: list[dict], edge: str, max_shift_ms: int) -> int | None:
    candidates = [round(float(item[edge]) * 1000) for item in segments
                  if item.get("text") and item.get(edge) is not None]
    close = [value for value in candidates if abs(value - point_ms) <= max_shift_ms]
    return min(close, key=lambda value: (abs(value - point_ms), value)) if close else None


def _midword_boundary(point_ms: int, segments: list[dict], edge: str,
                      max_shift_ms: int) -> int | None:
    for segment in segments:
        for word in segment.get("words", []):
            start, end = round(float(word["start"]) * 1000), round(float(word["end"]) * 1000)
            if start < point_ms < end:
                safe = start if edge == "start" else end
                return safe if abs(safe - point_ms) <= max_shift_ms else None
    return None


def align_timeline_to_speech(timeline: TimelineIR, transcripts: dict[str, dict],
                             source_durations_ms: dict[str, int],
                             max_shift_ms: int = 400,
                             preserve_cuts: bool = False) -> SpeechPlanResult:
    """Snap cuts only to nearby evidenced phrase/word edges; retain plan duration/order.

    A cut without a nearby verified edge stays unchanged and is reported unresolved.
    Source time is not output time: captions are mapped through each shot's speed
    and real transition overlap.
    """
    adjusted = []
    decisions: list[dict[str, Any]] = []
    for shot in timeline.video_segments:
        if preserve_cuts:
            adjusted.append(shot)
            decisions.append({"segment_id": shot.segment_id, "status": "user_override",
                              "reason": "Manual source window preserved; captions use source timestamps."})
            continue
        transcript = transcripts.get(shot.source_path)
        if not shot.has_audio or not transcript:
            adjusted.append(shot)
            decisions.append({"segment_id": shot.segment_id, "status": "unavailable",
                              "reason": "no source-relative transcript"})
            continue
        segments = transcript.get("segments", [])
        start, end = shot.source_in_ms, shot.source_out_ms
        source_limit = source_durations_ms.get(shot.clip_id, end)
        edges: dict[str, str] = {}
        for edge, point in (("start", start), ("end", end)):
            candidate = _near_phrase_boundary(point, segments, edge, max_shift_ms)
            origin = "phrase"
            if candidate is None:
                candidate = _midword_boundary(point, segments, edge, max_shift_ms)
                origin = "word"
            if candidate is None or candidate < 0 or candidate > source_limit:
                edges[edge] = "unresolved"
                continue
            if edge == "start" and candidate < end - 250:
                start = candidate
                edges[edge] = origin
            elif edge == "end" and candidate > start + 250:
                end = candidate
                edges[edge] = origin
            else:
                edges[edge] = "unresolved"
        # Same timeline interval, new source window: compiler speed is recalculated.
        adjusted.append(revalidate(shot, source_in_ms=start, source_out_ms=end,
                                   speed=round((end - start) / shot.timeline_duration_ms, 4)))
        decisions.append({"segment_id": shot.segment_id,
                          "source_path": shot.source_path,
                          "original_in_ms": shot.source_in_ms, "original_out_ms": shot.source_out_ms,
                          "source_in_ms": start, "source_out_ms": end,
                          "start_boundary": edges.get("start"), "end_boundary": edges.get("end"),
                          "status": "aligned" if all(value != "unresolved" for value in edges.values())
                          else "partial"})
    candidate = revalidate(timeline, video_segments=tuple(adjusted), caption_cues=())
    cues: list[CaptionCueIR] = []
    for shot in candidate.video_segments:
        transcript = transcripts.get(shot.source_path)
        if not transcript or not shot.has_audio:
            continue
        overlap_ms = sum(s.transition_in_ms for s in candidate.video_segments[1:]
                         if s.timeline_start_ms <= shot.timeline_start_ms)
        output_offset = shot.timeline_start_ms - overlap_ms
        ratio = shot.timeline_duration_ms / shot.source_duration_ms
        for segment in transcript.get("segments", []):
            source_start = round(float(segment["start"]) * 1000)
            source_end = round(float(segment["end"]) * 1000)
            if not segment.get("text") or source_end <= source_start:
                continue
            if source_start < shot.source_in_ms or source_end > shot.source_out_ms:
                # A cut may intersect a phrase when no safe nearby boundary
                # exists. Caption only complete timestamped words actually kept.
                words = [word for word in segment.get("words", [])
                         if round(float(word["start"]) * 1000) >= shot.source_in_ms
                         and round(float(word["end"]) * 1000) <= shot.source_out_ms]
                if not words:
                    continue
                source_start = round(float(words[0]["start"]) * 1000)
                source_end = round(float(words[-1]["end"]) * 1000)
                caption_text = " ".join(str(word.get("text", "")).strip() for word in words).strip()
            else:
                caption_text = str(segment["text"]).strip()
            if not caption_text:
                continue
            start_ms = output_offset + round((source_start - shot.source_in_ms) * ratio)
            end_ms = output_offset + round((source_end - shot.source_in_ms) * ratio)
            end_ms = min(end_ms, round(candidate.render_duration_s * 1000))
            if start_ms < end_ms:
                cues.append(CaptionCueIR(start_ms=start_ms, end_ms=end_ms,
                                         text=caption_text, origin="local_asr",
                                         source_path=shot.source_path,
                                         source_start_ms=source_start, source_end_ms=source_end))
    cues.sort(key=lambda item: (item.start_ms, item.end_ms))
    candidate = revalidate(candidate, caption_cues=tuple(cues))
    statuses = {item["status"] for item in decisions}
    status = ("user_override" if statuses == {"user_override"} else
              "aligned" if statuses == {"aligned"} else
              "partial" if "aligned" in statuses or "partial" in statuses else "unavailable")
    return SpeechPlanResult(candidate, status, tuple(decisions))
