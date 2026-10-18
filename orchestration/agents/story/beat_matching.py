"""Conservative source-window matching for explicitly ordered visual beats.

Descriptions are uncertain model observations. A candidate needs two distinct
timestamped frames supporting the same requested visual concept; filenames,
clip-wide scene labels and model-supplied confidence are deliberately ignored.
"""

import re
from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from shared.contracts.clip import ClipIntelligence
from shared.contracts.intent import SceneBeatRequirement


CONCEPT_CUES = {
    "signage": ("sign", "banner", "poster", "billboard", "placard", "text"),
    "venue": ("venue", "hall", "building", "room", "tent", "canopy", "seats", "chairs"),
    "arrival": ("arrival", "arrive", "enter", "entrance", "walking", "walk", "approach"),
    "greeting": ("greet", "handshake", "namaste"),
    "stage": ("stage", "platform", "podium", "microphone", "audience", "screen"),
    "oath": ("oath", "swear", "swearing", "ceremony"),
    "podium": ("podium", "lectern"),
    "speaker": ("speaker", "speaking", "speech", "addressing", "microphone"),
}
BEAT_ALIASES = {
    "preparation": "venue", "setup": "venue", "setting": "venue",
    "sign": "signage", "signs": "signage", "banner": "signage",
    "arrivals": "arrival", "greetings": "greeting", "greet": "greeting",
    "interaction": "stage", "speech": "speaker", "speaking": "speaker",
}
CONCEPT_WEIGHT = {"podium": 4, "signage": 3, "arrival": 3, "greeting": 3,
                  "oath": 3, "venue": 2, "stage": 2, "speaker": 2}


class BeatMatchError(ValueError):
    """A required scene cannot be grounded in available timestamped observations."""


@dataclass(frozen=True)
class BeatWindow:
    beat_index: int
    clip_id: str
    source_in_s: float
    source_out_s: float
    evidence_times_s: tuple[float, float]
    supported_concepts: tuple[str, ...]
    unmatched_concepts: tuple[str, ...]
    relevance: int  # sum of deterministic cue weights, not a confidence/probability
    evidence_basis: str = "two_direct_observations"


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", value.casefold()))


def _concepts(value: str) -> set[str]:
    words = _words(value)
    return {concept for concept, cues in CONCEPT_CUES.items()
            if any(any(word == cue or word.startswith(cue) for word in words) for cue in cues)}


def _requested(value: str) -> set[str]:
    words = _words(value)
    concepts = _concepts(value)
    concepts.update(BEAT_ALIASES[word] for word in words if word in BEAT_ALIASES)
    return concepts


def _greeting_gesture(value: str) -> bool:
    words = _words(value)
    return bool(words & {"greet", "greeting", "handshake", "namaste"}) or any(
        phrase in value.casefold() for phrase in (
            "shaking hands", "shake hands", "hands clasped", "clasped hands",
            "palms together", "folded hands",
        )
    )


def _interpersonal_action(value: str) -> bool:
    description = value.casefold()
    return any(phrase in description for phrase in (
        "conversation", "talking to each other", "looking at each other",
        "smiling at each other", "face to face", "engaged with each other",
    ))


def _missing_evidence_detail(requested: set[str], clips: Mapping[str, ClipIntelligence],
                             duration_s: float) -> str:
    details = []
    for clip_id, clip in sorted(clips.items()):
        if not clip.usable:
            details.append(f"{clip_id}: unusable ({clip.unusable_reason or 'unknown reason'})")
            continue
        if clip.duration_s + 0.01 < duration_s:
            details.append(f"{clip_id}: {clip.duration_s:.2f}s source is shorter than "
                           f"the required {duration_s:.2f}s window")
            continue
        direct = [item.time_s for item in clip.frame_evidence
                  if requested & _concepts(item.description)]
        summary = (f"{clip_id}: {len(clip.frame_evidence)} timestamped observations; "
                   f"direct concept mentions at {', '.join(f'{at:.2f}s' for at in direct) or 'none'}")
        if "greeting" in requested:
            gestures = [item.time_s for item in clip.frame_evidence
                        if _greeting_gesture(item.description)]
            actions = [item.time_s for item in clip.frame_evidence
                       if _interpersonal_action(item.description)]
            summary += (f"; gesture at {', '.join(f'{at:.2f}s' for at in gestures) or 'none'}"
                        f"; interpersonal action at {', '.join(f'{at:.2f}s' for at in actions) or 'none'}")
        if clip.provenance.degraded:
            summary += f"; analysis reported {len(clip.provenance.degraded)} degraded step(s)"
        details.append(summary)
    return "; ".join(details)


def _candidates(index: int, beat: SceneBeatRequirement, clips: Mapping[str, ClipIntelligence],
                duration_s: float) -> list[BeatWindow]:
    requested = _requested(beat.description)
    if not requested:
        raise BeatMatchError(f"Scene beat {index + 1} has no supported visual concept: {beat.description}")
    found = []
    for clip_id, clip in sorted(clips.items()):
        if not clip.usable or clip.duration_s + 0.01 < duration_s:
            continue
        evidence = sorted(clip.frame_evidence, key=lambda item: item.time_s)
        for left_index, left in enumerate(evidence):
            for right in evidence[left_index + 1:]:
                if right.time_s - left.time_s > max(5.5, duration_s):
                    break
                support = requested & _concepts(left.description) & _concepts(right.description)
                evidence_basis = "two_direct_observations"
                if "greeting" in requested and "greeting" not in support:
                    # A brief gesture may appear in only one sampled frame.
                    # Require a second, different frame with an observable
                    # interpersonal action; generic crowds do not suffice.
                    if ((_greeting_gesture(left.description) and
                         _interpersonal_action(right.description)) or
                        (_greeting_gesture(right.description) and
                         _interpersonal_action(left.description))):
                        support.add("greeting")
                        evidence_basis = "gesture_and_interaction"
                if not support:
                    continue
                centre = (left.time_s + right.time_s) / 2
                source_in = min(max(0.0, centre - duration_s / 2), clip.duration_s - duration_s)
                source_out = source_in + duration_s
                if not (source_in <= left.time_s < source_out and source_in <= right.time_s < source_out):
                    continue
                found.append(BeatWindow(
                    beat_index=index, clip_id=clip_id,
                    source_in_s=round(source_in, 3), source_out_s=round(source_out, 3),
                    evidence_times_s=(left.time_s, right.time_s),
                    supported_concepts=tuple(sorted(support)),
                    unmatched_concepts=tuple(sorted(requested - support)),
                    relevance=sum(CONCEPT_WEIGHT[concept] for concept in support) - (
                        1 if evidence_basis == "gesture_and_interaction" else 0),
                    evidence_basis=evidence_basis,
                ))
    found.sort(key=lambda item: (-item.relevance, len(item.unmatched_concepts),
                                 item.clip_id, item.source_in_s))
    return found[:24]


def match_scene_beats(beats: Sequence[SceneBeatRequirement],
                      clips: Mapping[str, ClipIntelligence], target_duration_s: float) -> tuple[BeatWindow, ...]:
    """Select one nonoverlapping source window per beat in prompt order.

    Missing evidence raises an actionable error. This is a proposal from local
    observations; independent visual review is still required for acceptance.
    """
    if not beats:
        return ()
    if target_duration_s <= 0 or target_duration_s / len(beats) < 0.5:
        raise BeatMatchError("Target duration leaves less than 0.5s per required scene beat")
    duration = target_duration_s / len(beats)
    options = []
    for index, beat in enumerate(beats):
        choices = _candidates(index, beat, clips, duration)
        if not choices:
            raise BeatMatchError(
                f"No two timestamped source observations support scene beat {index + 1}: {beat.description} "
                f"within a {duration:.2f}s source window. "
                f"Evidence checked: {_missing_evidence_detail(_requested(beat.description), clips, duration)}. "
                "Review the named source interval or revise the beat; no scene was invented.")
        options.append(choices)

    best: tuple[float, tuple[BeatWindow, ...]] | None = None

    def visit(index: int, selected: tuple[BeatWindow, ...], score: float) -> None:
        nonlocal best
        if index == len(options):
            if best is None or score > best[0]:
                best = (score, selected)
            return
        for choice in options[index]:
            # When a source recurs after another clip, keep its filming order.
            # Otherwise an early setup can be placed after a late payoff even
            # though the ranges do not overlap.
            if any(choice.clip_id == prior.clip_id and
                   choice.source_in_s < prior.source_out_s - 0.01 for prior in selected):
                continue
            previous = selected[-1] if selected else None
            # Source diversity is a tie breaker; relevance always dominates.
            diversity = 0.1 if previous and choice.clip_id != previous.clip_id else 0.0
            visit(index + 1, (*selected, choice), score + choice.relevance + diversity)

    visit(0, (), 0.0)
    if best is None:
        raise BeatMatchError("Required scene beats have no nonoverlapping source windows at the requested duration")
    return best[1]
