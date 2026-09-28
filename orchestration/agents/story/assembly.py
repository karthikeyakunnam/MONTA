"""
MONTA — Story Assembly (Layer 7, steps 3–4)
=============================================
Assign clips to acts, build the timeline, and repair rule violations.

Assignment
    1. Reject unusable / low-quality clips (relaxed if footage is scarce).
    2. Pick exactly one hero clip (quality + energy + hero-role confidence) and
       place it in the pattern's hero act at the hero position.
    3. Guarantee every act one clip, scarcest act first.
    4. Distribute remaining clips to their best-fitting act within capacity
       (capacity = act share × target duration ÷ cut length).
    5. Order within act by the act's energy direction.

Duration planning (target ±5%, exact when footage allows)
    1. ``fit_to_duration`` admits unused clips when footage is short or cuts
       would drag, and drops the weakest clips when cuts would be crushed.
    2. ``waterfill`` solves cut lengths l_i = clamp(p_i·s, min_i, clip_i) so that
       Σ l_i equals the target (p_i = act rhythm, ×1.5 for the hero).
    3. Allocations longer than 2.2× the rhythm are split into non-overlapping
       windows at each region's energy peak (long single takes become several cuts).
    4. Boundaries are rounded cumulatively to milliseconds, so the total is exact.
    Segment energy is measured on the chosen window, not the whole clip.

Repair
    Greedy local search over drop / swap-in / move edits, accepting an edit only
    if it strictly reduces validation errors (ties broken by total fit).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from orchestration.agents.story.selection import AdaptedAct, AdaptedPattern, emotion_related, role_match
from orchestration.agents.story.validator import ActSpec, TimelineValidator, ValidationConfig
from shared.contracts.clip import ClipIntelligence
from shared.contracts.explain import clamp
from shared.contracts.story import ClipDecision, TimelineSegment, ValidationReport
from shared.contracts.vocab import Emotion, Pace

MIN_QUALITY = 4.0
MIN_FIT = 0.3
MIN_CUT_S = 0.5
HERO_CUT_MULTIPLIER = 1.5
MIN_SCALE = 0.75       # below this, cuts are compressed too far → drop clips
MAX_SCALE = 2.0        # above this, cuts drag → admit clips if any fit
MAX_PIECE_FACTOR = 2.2  # a single cut never exceeds 2.2× its rhythm; longer allocations are split
MAX_REPAIR_ROUNDS = 8


def energy_fit(energy: float, target: tuple[float, float]) -> float:
    lo, hi = target
    dist = lo - energy if energy < lo else energy - hi if energy > hi else 0.0
    return 1 - clamp(dist / 4)


def clip_fit(clip: ClipIntelligence, act: AdaptedAct, intent_emotion: Emotion) -> tuple[float, str]:
    """Fit of a clip to an act in [0, 1] with a human-readable breakdown."""
    role = role_match(clip, act.template.preferred_roles)
    en = energy_fit(clip.energy_score, act.target_energy)
    if clip.emotion is None or clip.emotion == Emotion.NEUTRAL:
        emo = 0.5
    else:
        match = 1.0 if clip.emotion in act.preferred_emotions or emotion_related(clip.emotion, intent_emotion) else 0.2
        emo = 0.5 + (match - 0.5) * clip.emotion_confidence
    quality = clip.quality_score / 10
    score = 0.35 * role + 0.3 * en + 0.15 * emo + 0.2 * quality
    why = (f"role {role:.2f}, energy {clip.energy_score:.1f} vs {act.target_energy[0]:.1f}–{act.target_energy[1]:.1f} "
           f"({en:.2f}), emotion {emo:.2f}, quality {clip.quality_score:.1f}")
    return round(score, 4), why


def hero_fit(clip: ClipIntelligence, hero_roles) -> float:
    role = role_match(clip, hero_roles)  # the pattern's first hero role outranks the rest
    return 0.35 * clip.quality_score / 10 + 0.25 * clip.energy_score / 10 + 0.4 * role


@dataclass
class Assignment:
    acts: dict[str, list[str]]
    hero: str
    fits: dict[str, tuple[float, str]] = field(default_factory=dict)  # clip_id -> (fit, why) in its act
    rejected: dict[str, tuple[float, list[str]]] = field(default_factory=dict)  # clip_id -> (best fit, reasons)
    notes: list[str] = field(default_factory=list)

    def copy(self) -> "Assignment":
        return Assignment({k: list(v) for k, v in self.acts.items()}, self.hero, dict(self.fits),
                          {k: (f, list(r)) for k, (f, r) in self.rejected.items()}, list(self.notes))

    @property
    def selected(self) -> list[str]:
        return [c for ids in self.acts.values() for c in ids]


def _capacity(act: AdaptedAct, target_s: float) -> int:
    return max(1, round(act.template.share * target_s / act.cut_length_s))


def order_act(ids: list[str], act: AdaptedAct, clips: Mapping[str, ClipIntelligence], hero: str | None, hero_rel: float) -> list[str]:
    """Order by energy direction; pin the hero at its relative position within the act."""
    rest = [c for c in ids if c != hero]
    rest.sort(key=lambda c: clips[c].energy_score, reverse=act.template.energy_direction == "fall")
    if hero in ids:
        rest.insert(round(clamp(hero_rel) * len(rest)), hero)
    return rest


def hero_relative_position(adapted: AdaptedPattern) -> float:
    act = adapted.acts[adapted.hero_act_index]
    return (adapted.pattern.hero_moment_position - act.start_fraction) / act.template.share


def assign(adapted: AdaptedPattern, clips: Mapping[str, ClipIntelligence], intent_emotion: Emotion) -> Assignment:
    notes: list[str] = []
    rejected: dict[str, tuple[float, list[str]]] = {}
    candidates: list[ClipIntelligence] = []
    low_quality: list[ClipIntelligence] = []
    for c in clips.values():
        if not c.usable:
            rejected[c.clip_id] = (0.0, [f"unusable: {c.unusable_reason}"])
        elif c.quality_score < MIN_QUALITY:
            low_quality.append(c)
        else:
            candidates.append(c)
    low_quality.sort(key=lambda c: c.quality_score, reverse=True)
    while len(candidates) < len(adapted.acts) and low_quality:
        c = low_quality.pop(0)
        candidates.append(c)
        notes.append(f"admitted {c.clip_id} despite quality {c.quality_score:.1f} < {MIN_QUALITY}: footage is limited")
    for c in low_quality:
        rejected[c.clip_id] = (0.0, [f"quality {c.quality_score:.1f} below minimum {MIN_QUALITY}"])
    if not candidates:
        raise ValueError("no usable clips to build a story from")

    hero_clip = max(candidates, key=lambda c: (hero_fit(c, adapted.pattern.hero_roles), c.quality_score))
    hero_act = adapted.acts[adapted.hero_act_index]
    acts: dict[str, list[str]] = {a.act_id: [] for a in adapted.acts}
    acts[hero_act.act_id].append(hero_clip.clip_id)
    fits: dict[str, tuple[float, str]] = {}
    hf = hero_fit(hero_clip, adapted.pattern.hero_roles)
    fits[hero_clip.clip_id] = (round(clamp(hf), 4), f"hero moment: hero-fit {hf:.2f} (best of {len(candidates)} candidates)")

    pool = [c for c in candidates if c.clip_id != hero_clip.clip_id]
    fit_table = {(c.clip_id, a.act_id): clip_fit(c, a, intent_emotion) for c in pool for a in adapted.acts}

    # Guarantee coverage: scarcest act first.
    empty = [a for a in adapted.acts if not acts[a.act_id]]
    empty.sort(key=lambda a: sum(1 for c in pool if fit_table[(c.clip_id, a.act_id)][0] >= 0.45))
    for a in empty:
        if not pool:
            break
        best = max(pool, key=lambda c: fit_table[(c.clip_id, a.act_id)][0])
        acts[a.act_id].append(best.clip_id)
        fits[best.clip_id] = fit_table[(best.clip_id, a.act_id)]
        pool.remove(best)

    # Distribute the rest by best fit within capacity.
    capacity = {a.act_id: _capacity(a, adapted.target_duration_s) for a in adapted.acts}
    pool.sort(key=lambda c: max(fit_table[(c.clip_id, a.act_id)][0] for a in adapted.acts), reverse=True)
    for c in pool:
        ranked = sorted(adapted.acts, key=lambda a: fit_table[(c.clip_id, a.act_id)][0], reverse=True)
        best_fit = fit_table[(c.clip_id, ranked[0].act_id)][0]
        if best_fit < MIN_FIT:
            rejected[c.clip_id] = (best_fit, [f"weak fit {best_fit:.2f} for every act (best: '{ranked[0].act_id}')"])
            continue
        placed = False
        for a in ranked:
            f = fit_table[(c.clip_id, a.act_id)]
            if f[0] >= MIN_FIT and len(acts[a.act_id]) < capacity[a.act_id]:
                acts[a.act_id].append(c.clip_id)
                fits[c.clip_id] = f
                placed = True
                break
        if not placed:
            rejected[c.clip_id] = (best_fit, [f"fits '{ranked[0].act_id}' ({best_fit:.2f}) but acts are at capacity for the target duration"])

    rel = hero_relative_position(adapted)
    for a in adapted.acts:
        acts[a.act_id] = order_act(acts[a.act_id], a, clips, hero_clip.clip_id, rel)
    return Assignment(acts=acts, hero=hero_clip.clip_id, fits=fits, rejected=rejected, notes=notes)


# ---------------------------------------------------------------------------- duration planning


def _preferred_length(adapted: AdaptedPattern, act: AdaptedAct, clip_id: str, hero: str) -> float:
    return act.cut_length_s * (HERO_CUT_MULTIPLIER if clip_id == hero else 1.0)


def _bounds(adapted: AdaptedPattern, a: Assignment, clips: Mapping[str, ClipIntelligence]):
    """(act, clip, preferred, min, max) for every selected clip, in act order."""
    by_id = {x.act_id: x for x in adapted.acts}
    rows = []
    for act_id, ids in a.acts.items():
        act = by_id[act_id]
        for cid in ids:
            c = clips[cid]
            rows.append((act, c, _preferred_length(adapted, act, cid, a.hero), min(MIN_CUT_S, c.duration_s), c.duration_s))
    return rows


def waterfill(preferred: list[float], lo: list[float], hi: list[float], total: float) -> list[float]:
    """Lengths l_i = clamp(p_i·s, lo_i, hi_i) with Σl_i = total (bisection on s). Saturates when infeasible."""
    if sum(hi) <= total:
        return list(hi)
    if sum(lo) >= total:
        return list(lo)
    s_lo, s_hi = 0.0, max(h / p for h, p in zip(hi, preferred)) + 1.0
    for _ in range(80):
        mid = (s_lo + s_hi) / 2
        if sum(min(max(p * mid, l), h) for p, l, h in zip(preferred, lo, hi)) < total:
            s_lo = mid
        else:
            s_hi = mid
    return [min(max(p * s_hi, l), h) for p, l, h in zip(preferred, lo, hi)]


def _admit_candidates(adapted: AdaptedPattern, a: Assignment, clips, intent_emotion) -> list[tuple[float, str, AdaptedAct, tuple]]:
    out = []
    for cid in a.rejected:
        c = clips[cid]
        if not c.usable:
            continue
        best = max(((clip_fit(c, act, intent_emotion), act) for act in adapted.acts), key=lambda x: x[0][0])
        out.append((best[0][0], cid, best[1], best[0]))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def fit_to_duration(adapted: AdaptedPattern, assignment: Assignment, clips: Mapping[str, ClipIntelligence],
                    intent_emotion: Emotion) -> Assignment:
    """Admit or drop clips until the target is reachable at a natural cut rhythm.

    * Not enough footage for the target → admit the best-fitting unused usable clips.
    * Cuts would have to stretch beyond ``MAX_SCALE``× their rhythm → admit clips if any fit.
    * Cuts would have to shrink below ``MIN_SCALE``× → drop the weakest non-hero clips from crowded acts.
    """
    a = assignment.copy()
    T = adapted.target_duration_s
    rel = hero_relative_position(adapted)
    for _ in range(len(clips) + 1):
        rows = _bounds(adapted, a, clips)
        preferred = sum(r[2] for r in rows)
        capacity = sum(r[4] for r in rows)
        scale = T / preferred if preferred else float("inf")
        if capacity < T or scale > MAX_SCALE:
            candidates = _admit_candidates(adapted, a, clips, intent_emotion)
            needed = capacity < T
            # Low-quality or weak-fit footage is admitted only when the target is otherwise unreachable.
            pick = next((x for x in candidates
                         if needed or (x[0] >= MIN_FIT and clips[x[1]].quality_score >= MIN_QUALITY)), None)
            if pick is not None:
                fit, cid, act, fit_row = pick
                a.rejected.pop(cid)
                a.acts[act.act_id] = order_act(a.acts[act.act_id] + [cid], act, clips, a.hero, rel)
                a.fits[cid] = fit_row
                reason = "not enough footage for the target duration" if needed else "cuts would otherwise run too long"
                a.notes.append(f"admitted {cid} to '{act.act_id}' (fit {fit:.2f}): {reason}")
                continue
        if scale < MIN_SCALE:
            crowded = [cid for ids in a.acts.values() if len(ids) > 1 for cid in ids if cid != a.hero]
            if crowded:
                victim = min(crowded, key=lambda c: (a.fits.get(c, (0, ""))[0], c))
                for ids in a.acts.values():
                    if victim in ids:
                        ids.remove(victim)
                fit = a.fits.pop(victim, (0.0, ""))[0]
                a.rejected[victim] = (fit, [f"trimmed to fit the {T:.1f}s target (lowest fit {fit:.2f} in a crowded act)"])
                continue
        break
    return a


def window_energy(clip: ClipIntelligence, src_in: float, src_out: float) -> float:
    """Energy of the chosen source window, from the clip's measured energy curve."""
    curve = clip.energy_curve
    if not curve:
        return clip.energy_score
    n = len(curve)
    lo = min(n - 1, int(src_in / clip.duration_s * n))
    hi = max(lo + 1, min(n, math.ceil(src_out / clip.duration_s * n)))
    mean_all = sum(curve) / n
    if mean_all <= 1e-6:
        return clip.energy_score
    local = sum(curve[lo:hi]) / (hi - lo)
    return round(clamp(clip.energy_score * (0.5 + 0.5 * local / mean_all), 0, 10), 2)


def _region_peak(clip: ClipIntelligence, start: float, end: float) -> float:
    curve = clip.energy_curve
    if not curve:
        return clip.peak_time_s if start <= clip.peak_time_s <= end else (start + end) / 2
    n = len(curve)
    idx = [i for i in range(n) if start <= (i + 0.5) / n * clip.duration_s <= end]
    if not idx:
        return (start + end) / 2
    best = max(idx, key=lambda i: curve[i])
    return (best + 0.5) / n * clip.duration_s


def _windows(clip: ClipIntelligence, total_len: float, rhythm: float, max_piece: float) -> list[tuple[float, float]]:
    """Split ``total_len`` of a clip into non-overlapping windows sized to the act rhythm, each at its region's peak.

    k pieces ≈ total_len / rhythm (so a fast act gets fast cuts), never so many that a piece drops below
    MIN_CUT_S and never so few that a piece exceeds ``max_piece``.
    """
    k = max(1, round(total_len / rhythm), math.ceil(total_len / max_piece - 1e-9))
    k = max(1, min(k, int(total_len / MIN_CUT_S)))
    piece = total_len / k
    d = clip.duration_s
    if k == 1:
        centre = clip.peak_time_s
        src_in = clamp(centre - piece / 2, 0, max(0.0, d - piece))
        return [(src_in, src_in + piece)]
    out = []
    for i in range(k):
        r0, r1 = d * i / k, d * (i + 1) / k
        centre = _region_peak(clip, r0, r1)
        src_in = clamp(centre - piece / 2, r0, max(r0, r1 - piece))
        out.append((src_in, src_in + piece))
    return out


def _interleave(groups: list[list[tuple]], direction: str) -> list[tuple]:
    """Order an act's pieces so split takes don't sit back-to-back *and* the act keeps its energy direction.

    Greedy: next piece comes from the clip earliest in the act's energy order (``groups`` is already in
    that order) that differs from the previous piece's clip — so a rising act rises with small alternations
    between neighbouring shots instead of a sawtooth. A single-clip act necessarily keeps its pieces adjacent.
    """
    queues = [list(g) for g in groups if g]
    out: list[tuple] = []
    last: str | None = None
    while any(queues):
        live = [q for q in queues if q]
        pick = next((q for q in live if q[0][1].clip_id != last), live[0])
        if direction == "hold" and len(live) > 1:
            # hold acts rotate evenly through their shots
            pick = next((q for q in sorted(live, key=len, reverse=True) if q[0][1].clip_id != last), live[0])
        piece = pick.pop(0)
        out.append(piece)
        last = piece[1].clip_id
    return out


def build_timeline(adapted: AdaptedPattern, assignment: Assignment, clips: Mapping[str, ClipIntelligence]) -> tuple[TimelineSegment, ...]:
    """Water-fill cut lengths to the target, split long takes, place windows, round to milliseconds."""
    rows = _bounds(adapted, assignment, clips)
    if not rows:
        return ()
    lengths = waterfill([r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows], adapted.target_duration_s)

    per_act: dict[str, list[list[tuple]]] = {}
    for (act, c, pref, _, _), total_len in zip(rows, lengths):
        max_piece = max(MIN_CUT_S * 2, pref * MAX_PIECE_FACTOR)
        wins = _windows(c, total_len, pref, max_piece)
        hero_idx = max(range(len(wins)), key=lambda i: window_energy(c, *wins[i])) if c.clip_id == assignment.hero else -1
        per_act.setdefault(act.act_id, []).append([(act, c, a0, a1, i == hero_idx) for i, (a0, a1) in enumerate(wins)])

    pieces = []  # (act, clip, src_in, src_out, is_hero)
    for act in adapted.acts:
        pieces.extend(_interleave(per_act.get(act.act_id, []), act.template.energy_direction))

    total = sum(p[3] - p[2] for p in pieces)
    segments, cursor = [], 0.0
    for i, (act, c, a0, a1, is_hero) in enumerate(pieces):
        start = round(cursor, 3)
        cursor += a1 - a0
        end = round(cursor, 3) if i < len(pieces) - 1 else round(total, 3)
        cut = round(end - start, 3)
        src_in = round(min(a0, max(0.0, c.duration_s - cut)), 3)
        src_out = round(src_in + cut, 3)
        energy = window_energy(c, src_in, src_out)
        fit, why = assignment.fits.get(c.clip_id, (0.0, "assigned"))
        segments.append(TimelineSegment(
            index=i, clip_id=c.clip_id, act_id=act.act_id, start=start, end=end, source_in=src_in, source_out=src_out,
            energy=energy, is_hero=is_hero,
            purpose=f"{act.template.name}: {'hero moment — ' if is_hero else ''}{act.template.purpose}",
            reasoning=f"{why}; {cut:.2f}s cut at {act.pace.value} pace from {src_in:.2f}–{src_out:.2f}s (window energy {energy:.1f})",
        ))
    return tuple(segments)


def act_specs(adapted: AdaptedPattern) -> list[ActSpec]:
    return [ActSpec(a.act_id, a.template.energy_direction) for a in adapted.acts]


def validation_config(adapted: AdaptedPattern) -> ValidationConfig:
    return ValidationConfig.for_pattern(adapted.pattern.pattern_id, adapted.pattern.validation)


def repair(
    adapted: AdaptedPattern,
    assignment: Assignment,
    clips: Mapping[str, ClipIntelligence],
    validator: TimelineValidator,
    pace: Pace,
    intent_emotion: Emotion,
) -> tuple[Assignment, tuple[TimelineSegment, ...], ValidationReport, list[str]]:
    """Greedy local search that strictly reduces validation errors (including the duration rule)."""
    specs = act_specs(adapted)
    config = validation_config(adapted)
    rel = hero_relative_position(adapted)
    log: list[str] = []

    def evaluate(a: Assignment):
        tl = build_timeline(adapted, a, clips)
        rep = validator.validate(tl, specs, clips, pace, rejected=set(a.rejected), config=config,
                                 target_duration_s=adapted.target_duration_s) if tl else None
        errors = rep.error_count if rep else 10**6
        return errors, -sum(a.fits.get(c, (0, ""))[0] for c in a.selected), tl, rep

    current = assignment
    errors, neg_fit, timeline, report = evaluate(current)
    for _ in range(MAX_REPAIR_ROUNDS):
        if errors == 0:
            break
        best: tuple | None = None
        for edit, label in _candidate_edits(adapted, current, clips, intent_emotion, rel):
            e, nf, tl, rep = evaluate(edit)
            if (e, nf) < (errors, neg_fit) and e < errors and (best is None or (e, nf) < best[:2]):
                best = (e, nf, edit, tl, rep, label)
        if best is None:
            break
        errors, neg_fit, current, timeline, report, label = best
        log.append(f"{label} → {errors} error(s) remaining")
    return current, timeline, report, log


def _candidate_edits(adapted: AdaptedPattern, a: Assignment, clips, intent_emotion, rel):
    unused = [
        cid for cid, (_, reasons) in a.rejected.items()
        if clips[cid].usable and clips[cid].quality_score >= MIN_QUALITY
    ]
    by_id = {x.act_id: x for x in adapted.acts}
    act_ids = list(a.acts)
    for i, act_id in enumerate(act_ids):
        ids = a.acts[act_id]
        act = by_id[act_id]
        # drop a non-hero clip
        for cid in ids:
            if cid != a.hero and len(ids) > 1:
                e = a.copy()
                e.acts[act_id].remove(cid)
                e.rejected[cid] = (e.fits.pop(cid, (0.0, ""))[0], [f"removed during repair to satisfy timeline rules (act '{act_id}')"])
                yield e, f"dropped {cid} from '{act_id}'"
        # bring in an unused clip
        for cid in unused:
            fit = clip_fit(clips[cid], act, intent_emotion)
            if fit[0] < MIN_FIT:
                continue
            e = a.copy()
            e.rejected.pop(cid, None)
            e.acts[act_id] = order_act(e.acts[act_id] + [cid], act, clips, e.hero, rel)
            e.fits[cid] = fit
            yield e, f"added {cid} to '{act_id}' (fit {fit[0]:.2f})"
        # move a clip to a neighbouring act
        for j in (i - 1, i + 1):
            if 0 <= j < len(act_ids) and len(ids) > 1:
                target = by_id[act_ids[j]]
                for cid in ids:
                    if cid == a.hero:
                        continue
                    fit = clip_fit(clips[cid], target, intent_emotion)
                    if fit[0] < MIN_FIT:
                        continue
                    e = a.copy()
                    e.acts[act_id].remove(cid)
                    e.acts[target.act_id] = order_act(e.acts[target.act_id] + [cid], target, clips, e.hero, rel)
                    e.fits[cid] = fit
                    yield e, f"moved {cid} '{act_id}' → '{target.act_id}'"


def clip_decisions(assignment: Assignment, clips: Mapping[str, ClipIntelligence]) -> tuple[ClipDecision, ...]:
    out = []
    act_of = {c: act for act, ids in assignment.acts.items() for c in ids}
    for cid in clips:
        if cid in act_of:
            fit, why = assignment.fits.get(cid, (0.0, "assigned"))
            reasons = [why] + (["chosen as the single hero moment"] if cid == assignment.hero else [])
            out.append(ClipDecision(clip_id=cid, selected=True, act_id=act_of[cid], fit_score=clamp(fit), reasons=tuple(reasons)))
        else:
            fit, reasons = assignment.rejected.get(cid, (0.0, ["not assigned"]))
            out.append(ClipDecision(clip_id=cid, selected=False, fit_score=clamp(fit), reasons=tuple(reasons)))
    return tuple(out)


__all__ = [
    "Assignment", "assign", "build_timeline", "fit_to_duration", "waterfill", "window_energy", "validation_config", "repair", "clip_decisions", "clip_fit", "hero_fit",
    "act_specs", "order_act", "hero_relative_position",
]
