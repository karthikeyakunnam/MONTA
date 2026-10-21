"""Conservative source-window matching for explicitly ordered visual beats.

Descriptions are uncertain model observations. A candidate needs two distinct
timestamped frames supporting the same requested visual concept; filenames,
clip-wide scene labels and model-supplied confidence are deliberately ignored.
"""

import re
import statistics
from dataclasses import dataclass, replace
from collections.abc import Mapping, Sequence
from itertools import combinations

from shared.contracts.clip import ClipIntelligence
from shared.contracts.intent import SceneBeatRequirement


CONCEPT_CUES = {
    "signage": ("sign", "signage", "banner", "poster", "billboard", "placard"),
    "venue": ("venue", "hall", "building", "room", "tent", "canopy", "seats", "chairs"),
    "arrival": ("arrival", "arrive", "enter", "entrance", "walking", "walk", "approach"),
    "greeting": ("greet", "greeting", "handshake", "namaste"),
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
    is_anchor: bool = True


def _usable_spans(clip: ClipIntelligence) -> tuple[tuple[float, float], ...]:
    """Measured shots minus brief, unusually soft source frames.

    Blur is a quality exclusion, never semantic evidence or a claimed scene
    boundary. Older projects without per-frame measurements keep their old
    behavior until their local signals are refreshed.
    """
    bounds = (0.0, *sorted(cut for cut in clip.scene_cut_times_s
                           if 0 < cut < clip.duration_s), clip.duration_s)
    spans = [(start, end) for start, end in zip(bounds, bounds[1:])]
    samples = clip.sharpness_samples
    if len(samples) < 3:
        return tuple(spans)
    median = statistics.median(value for _, value in samples)
    if median < 0.01:
        return tuple(spans)
    threshold = min(0.006, median * 0.28)
    step = statistics.median(b[0] - a[0] for a, b in zip(samples, samples[1:]))
    margin = max(0.12, step * 0.55)
    for at, sharpness in samples:
        if sharpness >= threshold:
            continue
        left, right = at - margin, at + margin
        spans = [(a, b) for start, end in spans
                 for a, b in ((start, min(end, left)), (max(start, right), end))
                 if b - a >= 0.7]
    return tuple(spans)


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", value.casefold()))


def _concepts(value: str) -> set[str]:
    words = _words(value)
    def matches(word: str, cue: str) -> bool:
        forms = {cue, cue + "s", cue + "ed", cue + "ing"}
        if cue.endswith("e"):
            forms.add(cue[:-1] + "ing")
        return word in forms
    return {concept for concept, cues in CONCEPT_CUES.items()
            if any(any(matches(word, cue) for word in words) for cue in cues)}


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
            "palms together", "folded hands", "hands together", "prayer position",
        )
    ) or ("praying" in words and bool(words & {"person", "people", "man", "woman"})) or (
          "bouquet" in words and "smiling" in words and
          bool(words & {"another", "other", "two"}))


def _interpersonal_action(value: str) -> bool:
    description = value.casefold()
    return any(phrase in description for phrase in (
        "conversation", "talking to each other", "looking at each other",
        "smiling at each other", "face to face", "engaged with each other",
        "smiling at them", "smiling at him", "smiling at her",
        "interacting with him", "interacting with her", "interacting with them",
        "arms around", "posing for a photo together", "looking at him",
        "looking at her", "looking at them",
    ))


def _social_proximity(value: str) -> bool:
    """Corroborating people in one shot; never a greeting anchor on its own."""
    description = value.casefold()
    return any(phrase in description for phrase in (
        "next to another person", "next to each other", "beside another person",
        "beside him", "beside her", "facing each other",
    ))


def _stage_interaction(value: str) -> bool:
    """A screen, audience or empty platform alone cannot prove interaction."""
    words = _words(value)
    if "podium" in words and not words & {"together", "exchange", "presenting", "handing"}:
        return False
    raised_hand = any(phrase in value.casefold() for phrase in (
        "hand raised", "raised hand", "hand is raised", "hand held up"))
    return bool(words & {"stage", "platform"}) and bool(
        words & {"person", "people", "man", "woman", "men", "women", "speaker", "performer"}
    ) and bool(words & {
        "interact", "interacting", "talk", "talking", "speak", "speaking",
        "presenting", "receive", "receiving", "perform", "performing",
        "greet", "greeting", "handshake", "handing", "addressing",
        "speech",
    }) or (raised_hand and bool(words & {"stage", "platform"}) and
           bool(words & {"person", "man", "woman", "speaker"}))


def _shared_visible_attire(first: str, second: str) -> bool:
    """Conservative continuity cue, never independent proof of an action."""
    person = {"person", "people", "man", "woman", "men", "women"}
    excluded = {"statue", "idol", "painting", "screen", "poster"}
    if not (_words(first) & person and _words(second) & person):
        return False
    if _words(first) & excluded or _words(second) & excluded:
        return False
    attire = re.compile(
        r"\b(yellow|orange|blue|red|white|black|green)\b"
        r"(?:\s+[a-z]+){0,2}\s+\b(robe|sash|scarf|jacket|suit)\b",
        re.IGNORECASE,
    )
    signatures = lambda value: {(match.group(1).casefold(), match.group(2).casefold())
                                for match in attire.finditer(value)}
    return bool(signatures(first) & signatures(second))


def _person_on_stage(value: str) -> bool:
    return bool(re.search(
        r"\b(?:man|woman|person|people|men|women|speaker)\s+(?:\w+\s+){0,3}(?:on|at) (?:the )?stage\b",
        value.casefold(),
    ))


def _context_support(description: str, beat: SceneBeatRequirement) -> bool:
    """Supportive B-roll for a beat; never counted as its required anchor."""
    words = _words(description)
    if words & {"statue", "idol", "monument", "sculpture", "painting"}:
        return False
    requested = _requested(beat.description)
    if "signage" in requested:
        # A decorated entrance or event flags can follow a proven sign as
        # establishing context. They never establish signage by themselves.
        return (bool(words & {"sign", "banner", "poster"}) and not
                bool(words & {"stage", "screen", "podium"})) or (
            "archway" in words and bool(words & {"decorated", "flowers", "event"})) or (
            bool(words & {"flags", "flag"}) and bool(words & {"event", "venue", "building"}))
    if "greeting" in requested:
        return bool(words & {"people", "men", "women", "person", "man", "woman"}) and (
            _interpersonal_action(description) or
            _greeting_gesture(description) or
            ("bouquet" in words and "smiling" in words)) and not bool(
            words & {"car", "stage", "chairs", "sitting", "reading",
                     "screen", "podium"})
    if "stage" in requested and "interaction" in _words(beat.description):
        return (bool(words & {"people", "men", "women", "person", "man", "woman", "crowd", "room"})
                and bool(words & {"stage", "platform", "audience", "chairs", "canopy",
                              "banner", "curtain", "curtains", "screen"})
                and "podium" not in words)
    return False


def _context_candidates(index: int, beat: SceneBeatRequirement,
                        clips: Mapping[str, ClipIntelligence], anchors: Sequence[BeatWindow]
                        ) -> list[BeatWindow]:
    """Find measured shots adjacent to a grounded anchor in the same source."""
    found = []
    for clip_id, clip in sorted(clips.items()):
        centers = [(item.source_in_s + item.source_out_s) / 2
                   for item in anchors if item.clip_id == clip_id]
        if not centers:
            continue
        evidence = sorted(clip.frame_evidence, key=lambda item: item.time_s)
        for shot_start, shot_end in _usable_spans(clip):
            if shot_end - shot_start < 0.75 or min(
                    abs((shot_start + shot_end) / 2 - center) for center in centers) > 12:
                continue
            if "greeting" in _requested(beat.description) and (
                    shot_end < min(item.source_in_s for item in anchors if item.clip_id == clip_id) - 1.5 or
                    shot_start > max(item.source_out_s for item in anchors if item.clip_id == clip_id) + 8):
                continue
            shot_evidence = [item for item in evidence if shot_start <= item.time_s < shot_end]
            if "greeting" in _requested(beat.description):
                compatible = [item for item in shot_evidence
                              if bool(_words(item.description) & {
                                  "people", "men", "women", "person", "man", "woman"}) and
                              not bool(_words(item.description) & {
                                  "statue", "idol", "monument", "sculpture",
                                  "painting", "screen", "podium"})]
                support = compatible if any(_context_support(item.description, beat)
                                            for item in compatible) else []
            else:
                support = [item for item in shot_evidence if _context_support(item.description, beat)]
            if len(support) < 2 or len(support) != len(shot_evidence):
                continue
            # A corroborating observation a few milliseconds from a measured
            # cut can lie outside the conservative rendering margin. Keep the
            # earlier pair rather than discarding the whole clean shot.
            safe_support = [item for item in support
                            if shot_start + (0.08 if shot_start else 0) <= item.time_s <=
                            shot_end - (0.08 if shot_end < clip.duration_s else 0)]
            if len(safe_support) < 2:
                continue
            left, right = safe_support[0], safe_support[-1]
            if shot_end - shot_start <= 3:
                source_in = shot_start + (0.08 if shot_start else 0)
                source_out = shot_end - (0.08 if shot_end < clip.duration_s else 0)
            else:
                source_in = max(shot_start + 0.08, left.time_s - 0.25)
                source_out = min(shot_end - 0.08, right.time_s + 0.75)
            if source_out - source_in < 0.7 or not (
                    source_in <= left.time_s < right.time_s < source_out):
                continue
            found.append(BeatWindow(
                beat_index=index, clip_id=clip_id,
                source_in_s=round(source_in, 3), source_out_s=round(source_out, 3),
                evidence_times_s=(left.time_s, right.time_s),
                supported_concepts=("context",),
                unmatched_concepts=tuple(sorted(_requested(beat.description))),
                relevance=1, evidence_basis="adjacent_context_observations",
                is_anchor=False,
            ))
    return found[:16]


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
        if clip.scene_cut_times_s:
            summary += ("; measured hard cuts at " +
                        ", ".join(f"{at:.2f}s" for at in clip.scene_cut_times_s))
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
                duration_s: float | None) -> list[BeatWindow]:
    requested = _requested(beat.description)
    if not requested:
        raise BeatMatchError(f"Scene beat {index + 1} has no supported visual concept: {beat.description}")
    # A generic secondary cue such as "venue" cannot stand in for the
    # distinctive required content of "venue signage". Likewise a speaker
    # without a visible podium does not establish "podium speaker".
    primary_weight = max(CONCEPT_WEIGHT[concept] for concept in requested)
    primary = {concept for concept in requested if CONCEPT_WEIGHT[concept] == primary_weight}
    requires_stage_action = "interaction" in _words(beat.description) and "stage" in primary

    def supports_primary(description: str) -> bool:
        words = _words(description)
        signage_is_background = "signage" in primary and bool(
            words & {"stage", "podium", "audience", "screen"})
        # A local model called source branding a banner on tent curtains in
        # a measured source shot that contained no physical sign. Treat that
        # weak combination as context; other visible banners remain eligible.
        distinct_sign = "signage" not in primary or bool(words & {
            "sign", "signs", "signage", "billboard", "billboards",
            "poster", "posters", "placard", "placards"}) or (
            "banner" in words and not words & {"tent", "curtain", "curtains"})
        return bool(primary & _concepts(description)) and distinct_sign and not signage_is_background and (
            not requires_stage_action or _stage_interaction(description))
    found = []
    for clip_id, clip in sorted(clips.items()):
        if not clip.usable or (duration_s is not None and clip.duration_s + 0.01 < duration_s):
            continue
        evidence = sorted(clip.frame_evidence, key=lambda item: item.time_s)
        bounds = _usable_spans(clip)
        for left_index, left in enumerate(evidence):
            for right in evidence[left_index + 1:]:
                if right.time_s - left.time_s > max(5.5, duration_s or 0):
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
                    elif ((_greeting_gesture(left.description) and
                           _social_proximity(right.description)) or
                          (_greeting_gesture(right.description) and
                           _social_proximity(left.description))):
                        support.add("greeting")
                        evidence_basis = "gesture_and_social_context"
                stage_pair = requires_stage_action and (
                    (_stage_interaction(left.description) and _stage_interaction(right.description)) or
                    (_stage_interaction(left.description) and _person_on_stage(right.description)) or
                    (_stage_interaction(right.description) and _person_on_stage(left.description)))
                stage_continuity = requires_stage_action and (
                    right.time_s - left.time_s <= 1.0 and
                    (_stage_interaction(left.description) or
                     _stage_interaction(right.description)) and
                    _shared_visible_attire(left.description, right.description))
                if stage_continuity and not stage_pair:
                    support.add("stage")
                    evidence_basis = "stage_action_and_subject_continuity"
                if stage_pair and not (
                        _stage_interaction(left.description) and _stage_interaction(right.description)):
                    evidence_basis = "stage_person_and_action"
                if not (support & primary) or (
                        evidence_basis not in {"gesture_and_interaction", "gesture_and_social_context",
                                               "stage_person_and_action", "stage_action_and_subject_continuity"} and not (
                            supports_primary(left.description) and supports_primary(right.description))):
                    continue
                if requires_stage_action and not (stage_pair or stage_continuity):
                    continue
                # Anchor the first supporting observation near the beginning of
                # the requested beat. A centred window can open on unrelated
                # material for more than a second before the evidence appears.
                if duration_s is None:
                    shot = next(((start, end) for start, end in bounds
                                 if start <= left.time_s < end and start <= right.time_s < end), None)
                    if shot is None:
                        continue
                    # In a short measured shot, corroborated interior frames
                    # support the whole bounded take. Longer uninterrupted
                    # spans may still change subject without a hard cut, so
                    # keep those close to the supporting observations.
                    if shot[1] - shot[0] <= 3.0:
                        source_in = shot[0] + (0.08 if shot[0] else 0)
                        source_out = shot[1] - (0.08 if shot[1] < clip.duration_s else 0)
                    else:
                        source_in = max(shot[0] + (0.08 if shot[0] else 0), left.time_s - 0.25)
                        source_out = min(shot[1] - (0.08 if shot[1] < clip.duration_s else 0),
                                         right.time_s + 0.75)
                    if source_out - source_in < 0.7:
                        continue
                else:
                    source_in = min(max(0.0, left.time_s - 0.25,
                                        right.time_s - duration_s + 0.01),
                                    clip.duration_s - duration_s)
                    source_out = source_in + duration_s
                if not (source_in <= left.time_s < source_out and source_in <= right.time_s < source_out):
                    continue
                same_span = next(((start, end) for start, end in bounds
                                  if start <= source_in and source_out <= end), None)
                if same_span is None:
                    continue
                # A source file may already contain a montage. Two matching
                # descriptions cannot establish continuous action across a
                # measured hard cut, even if sparse vision sampling missed the
                # unrelated shot between them.
                if any(source_in + 0.01 < cut < source_out - 0.01
                       for cut in clip.scene_cut_times_s):
                    continue
                # Source files can already be montages. Two matching samples on
                # opposite sides of an unrelated sample do not establish a
                # continuous shot of the requested scene.
                between = [item for item in evidence
                           if source_in <= item.time_s < source_out]
                if any(not (supports_primary(item.description) or
                            (requires_stage_action and (_person_on_stage(item.description) or
                             (stage_continuity and item in (left, right)))) or
                            ("greeting" in requested and
                             (_greeting_gesture(item.description) or
                              _interpersonal_action(item.description) or
                              (evidence_basis == "gesture_and_social_context" and
                               _social_proximity(item.description)))))
                       for item in between):
                    continue
                # Sparse samples can put the next different scene a few
                # hundredths of a second beyond the proposed out point. The
                # observation time is not an exact scene boundary, so leave a
                # conservative margin instead of claiming that edge as clean.
                if any(source_in - 0.25 <= item.time_s < source_in or
                       source_out <= item.time_s < source_out + 0.25
                       for item in evidence if same_span[0] <= item.time_s < same_span[1] and not (
                           supports_primary(item.description) or
                           ("greeting" in requested and
                            (_greeting_gesture(item.description) or
                             _interpersonal_action(item.description) or
                             (evidence_basis == "gesture_and_social_context" and
                              _social_proximity(item.description)))))):
                    continue
                found.append(BeatWindow(
                    beat_index=index, clip_id=clip_id,
                    source_in_s=round(source_in, 3), source_out_s=round(source_out, 3),
                    evidence_times_s=(left.time_s, right.time_s),
                    supported_concepts=tuple(sorted(support)),
                    unmatched_concepts=tuple(sorted(requested - support)),
                    relevance=sum(CONCEPT_WEIGHT[concept] for concept in support) - (
                        1 if evidence_basis in {"gesture_and_interaction",
                                                "gesture_and_social_context",
                                                "stage_action_and_subject_continuity"} else 0),
                    evidence_basis=evidence_basis,
                ))
    if duration_s is None:
        # One proposal per measured source shot. Multiple observation pairs in
        # the same shot should not create duplicate footage in the plan.
        unique: dict[tuple[str, int], BeatWindow] = {}
        for item in found:
            key = (item.clip_id, next((index for index, (start, end) in
                   enumerate(_usable_spans(clips[item.clip_id]))
                   if start <= item.source_in_s < end), -1))
            previous = unique.get(key)
            if previous is None or (item.relevance, item.source_out_s - item.source_in_s) > (
                    previous.relevance, previous.source_out_s - previous.source_in_s):
                unique[key] = item
        found = list(unique.values())
    found.sort(key=lambda item: (-item.relevance, len(item.unmatched_concepts),
                                 item.clip_id, item.source_in_s))
    return found[:24]


def _nonoverlapping(windows: Sequence[BeatWindow]) -> bool:
    for index, item in enumerate(windows):
        if any(item.clip_id == prior.clip_id and
               item.source_in_s < prior.source_out_s - 0.01 and
               prior.source_in_s < item.source_out_s - 0.01
               for prior in windows[:index]):
            return False
    return True


def _variable_scene_beats(beats: Sequence[SceneBeatRequirement],
                          clips: Mapping[str, ClipIntelligence], target_duration_s: float
                          ) -> tuple[BeatWindow, ...]:
    """Assemble shorter verified shots; never stretch or repeat a source range."""
    anchors = [_candidates(index, beat, clips, None) for index, beat in enumerate(beats)]
    choices = [items[:8] + _context_candidates(index, beat, clips, items)[:8]
               for index, (beat, items) in enumerate(zip(beats, anchors))]
    for index, options in enumerate(choices):
        if not anchors[index]:
            raise BeatMatchError(
                f"Scene beat {index + 1}: {beats[index].description} has no short source shot "
                "supported by two timestamped observations within one measured scene. "
                f"Evidence checked: {_missing_evidence_detail(_requested(beats[index].description), clips, 0)}")
    grouped: list[list[tuple[BeatWindow, ...]]] = []
    preferred_minimum_s = min(3.0, max(1.0, target_duration_s / len(beats) * 0.5))
    minimum_by_beat_s = []
    for options in choices:
        # A short but genuine event must not be rejected solely because a
        # uniform beat allotment is longer than all distinct available shots.
        # The total target remains mandatory; other beats must supply time.
        unique_ranges = {(w.clip_id, w.source_in_s, w.source_out_s) for w in options}
        available_s = sum(end - start for _, start, end in unique_ranges)
        minimum_beat_s = min(preferred_minimum_s, available_s)
        minimum_by_beat_s.append(minimum_beat_s)
        groups = []
        for count in range(1, min(7, len(options)) + 1):
            for subset in combinations(options, count):
                ordered = tuple(sorted(subset, key=lambda w: (w.clip_id, w.source_in_s)))
                if (_nonoverlapping(ordered) and any(w.is_anchor for w in ordered) and
                        sum(w.source_out_s - w.source_in_s for w in ordered) >= minimum_beat_s):
                    groups.append(ordered)
        groups.sort(key=lambda group: (-sum(w.source_out_s - w.source_in_s for w in group),
                                       -sum(w.relevance for w in group), len(group)))
        grouped.append(groups[:160])
    beam: list[tuple[BeatWindow, ...]] = [()]
    for groups in grouped:
        expanded = []
        for prior in beam:
            for group in groups:
                if not _nonoverlapping((*prior, *group)):
                    continue
                if any(w.clip_id == earlier.clip_id and w.source_in_s < earlier.source_out_s - 0.01
                       for w in group for earlier in prior):
                    continue
                combined = (*prior, *group)
                capacity = sum(w.source_out_s - w.source_in_s for w in combined)
                if capacity <= target_duration_s + 3.0:
                    expanded.append(combined)
        expanded.sort(key=lambda group: (-min(target_duration_s,
                                             sum(w.source_out_s - w.source_in_s for w in group)),
                                         len(group), -sum(w.relevance for w in group)))
        # A capacity-only beam can fill with late source windows and discard
        # every early state that leaves room for a later beat from that source.
        # Retain both high-capacity and early-ending states; all still obey the
        # same semantic anchors and source-order constraints.
        early = sorted(expanded, key=lambda group: (
            sum(max(w.source_out_s for w in group if w.clip_id == clip_id)
                for clip_id in {w.clip_id for w in group}),
            -sum(w.source_out_s - w.source_in_s for w in group), len(group)))
        beam = list(dict.fromkeys((*expanded[:60], *early[:40])))[:100]
        if not beam:
            raise BeatMatchError("Required beats have no nonoverlapping, source-ordered short shots")
    feasible = []
    for group in beam:
        capacity = sum(w.source_out_s - w.source_in_s for w in group)
        minimum = sum(w.evidence_times_s[1] - w.evidence_times_s[0] + 0.16 for w in group)
        if minimum - 0.001 <= target_duration_s <= capacity + 0.001:
            feasible.append(group)
    if not feasible:
        maximum = max(sum(w.source_out_s - w.source_in_s for w in group) for group in beam)
        raise BeatMatchError(
            f"Ordered, nonrepeated evidence-backed shots: bounded search found {maximum:.2f}s "
            f"toward the requested {target_duration_s:.2f}s; no footage was stretched or repeated")
    chosen = min(feasible, key=lambda group: (len(group),
                 sum(w.source_out_s - w.source_in_s for w in group) - target_duration_s,
                 -sum(w.relevance for w in group)))
    excess_ms = round((sum(w.source_out_s - w.source_in_s for w in chosen) - target_duration_s) * 1000)
    trimmed = list(chosen)
    beat_duration_ms = {index: round(sum(w.source_out_s - w.source_in_s for w in chosen
                                         if w.beat_index == index) * 1000)
                        for index in range(len(beats))}
    minimum_beat_ms = {index: round(value * 1000)
                       for index, value in enumerate(minimum_by_beat_s)}
    for index in range(len(trimmed) - 1, -1, -1):
        item = trimmed[index]
        if excess_ms <= 0:
            break
        removable_end = max(0, round((item.source_out_s - item.evidence_times_s[1] - 0.08) * 1000))
        remove = min(excess_ms, removable_end,
                     max(0, beat_duration_ms[item.beat_index] - minimum_beat_ms[item.beat_index]))
        item = replace(item, source_out_s=round(item.source_out_s - remove / 1000, 3))
        excess_ms -= remove
        beat_duration_ms[item.beat_index] -= remove
        removable_start = max(0, round((item.evidence_times_s[0] - item.source_in_s - 0.08) * 1000))
        remove = min(excess_ms, removable_start,
                     max(0, beat_duration_ms[item.beat_index] - minimum_beat_ms[item.beat_index]))
        item = replace(item, source_in_s=round(item.source_in_s + remove / 1000, 3))
        excess_ms -= remove
        beat_duration_ms[item.beat_index] -= remove
        trimmed[index] = item
    if excess_ms > 0:
        raise BeatMatchError("Evidence margins prevent trimming short shots to the exact target duration")
    return tuple(trimmed)


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
            try:
                return _variable_scene_beats(beats, clips, target_duration_s)
            except BeatMatchError as variable_error:
                detail = str(variable_error)
            raise BeatMatchError(
                f"No two timestamped source observations support scene beat {index + 1}: {beat.description} "
                f"within a continuous {duration:.2f}s source window. "
                f"Evidence checked: {_missing_evidence_detail(_requested(beat.description), clips, duration)}. "
                "Review the named source interval and measured cuts, provide a longer continuous take, or shorten the "
                f"requested duration; no scene was invented. Short-shot feasibility: {detail}")
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
        try:
            return _variable_scene_beats(beats, clips, target_duration_s)
        except BeatMatchError as error:
            raise BeatMatchError(
                "Required scene beats have no nonoverlapping source windows at the requested duration. "
                f"Short-shot feasibility: {error}") from error
    return best[1]
