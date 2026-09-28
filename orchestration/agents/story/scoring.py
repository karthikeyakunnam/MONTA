"""
MONTA — Story Scoring (Layer 7)
=================================
Three 0–10 scores, each with a confidence derived from the reliability of the
evidence underneath it (vision vs. signal-only analysis), and a reasoning
string the Critic can audit.
"""

from collections.abc import Mapping, Sequence

from orchestration.agents.story.selection import AdaptedPattern, emotion_related, role_match
from shared.contracts.clip import ClipIntelligence
from shared.contracts.explain import Score, clamp
from shared.contracts.story import TimelineSegment, ValidationReport, ValidationRule
from shared.contracts.vocab import Emotion

VISION_EVIDENCE_CONF = 0.85
SIGNAL_EVIDENCE_CONF = 0.45


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx < 1e-9 or sy < 1e-9:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _selected(timeline: Sequence[TimelineSegment], clips: Mapping[str, ClipIntelligence]) -> list[ClipIntelligence]:
    return [clips[c] for c in dict.fromkeys(s.clip_id for s in timeline)]


def story_score(
    adapted: AdaptedPattern, timeline: Sequence[TimelineSegment], clips: Mapping[str, ClipIntelligence],
    fits: Mapping[str, tuple[float, str]], selection_confidence: float, report: ValidationReport,
) -> Score:
    sel = _selected(timeline, clips)
    mean_fit = sum(fits.get(c.clip_id, (0.0, ""))[0] for c in sel) / len(sel)
    by_act: dict[str, list[ClipIntelligence]] = {}
    for s in timeline:
        by_act.setdefault(s.act_id, []).append(clips[s.clip_id])
    covered = sum(
        1 for a in adapted.acts
        if any(role_match(c, a.template.preferred_roles) >= 0.5 for c in by_act.get(a.act_id, []))
    )
    coverage = covered / len(adapted.acts)
    hero = next(s for s in timeline if s.is_hero) if any(s.is_hero for s in timeline) else None
    hero_q = clips[hero.clip_id].quality_score / 10 if hero else 0.0
    failed_rules = len({v.rule for v in report.violations})
    rule_ratio = 1 - failed_rules / len(report.rules_checked)
    value = 10 * (0.3 * mean_fit + 0.2 * selection_confidence + 0.2 * coverage + 0.15 * hero_q + 0.15 * rule_ratio)
    conf = sum(VISION_EVIDENCE_CONF if c.provenance.vision_model else SIGNAL_EVIDENCE_CONF for c in sel) / len(sel)
    return Score(
        value=round(clamp(value, 0, 10), 2), confidence=round(conf, 3),
        reasoning=(f"mean clip fit {mean_fit:.2f}, pattern confidence {selection_confidence:.2f}, "
                   f"{covered}/{len(adapted.acts)} acts filled with role-matched clips, hero quality {hero_q * 10:.1f}, "
                   f"{len(report.rules_checked) - failed_rules}/{len(report.rules_checked)} timeline rules passed"),
    )


def emotion_score(
    adapted: AdaptedPattern, timeline: Sequence[TimelineSegment], clips: Mapping[str, ClipIntelligence],
    intent_emotion: Emotion, report: ValidationReport,
) -> Score:
    sel = _selected(timeline, clips)
    targets = list(dict.fromkeys((*adapted.pattern.required_emotions, intent_emotion)))
    present = {c.emotion for c in sel if c.emotion}
    covered = [t for t in targets if any(emotion_related(t, p) for p in present)]
    coverage = len(covered) / len(targets)
    bad_acts = {a for v in report.violations if v.rule == ValidationRule.ACT_EMOTIONAL_PROGRESSION for a in
                {s.act_id for s in timeline if s.index in v.segment_indices}}
    progression = 1 - len(bad_acts) / len(adapted.acts)
    value = 10 * (0.6 * coverage + 0.4 * progression)
    emo_conf = [c.emotion_confidence for c in sel if c.emotion]
    conf = max(0.1, sum(emo_conf) / len(sel)) if emo_conf else 0.1
    missing = [t.value for t in targets if t not in covered]
    return Score(
        value=round(value, 2), confidence=round(clamp(conf), 3),
        reasoning=(f"footage conveys {len(covered)}/{len(targets)} target emotions"
                   + (f" (missing: {', '.join(missing)})" if missing else "")
                   + f"; {len(adapted.acts) - len(bad_acts)}/{len(adapted.acts)} acts progress as designed"
                   + ("" if emo_conf else "; no vision emotion data, so confidence is minimal")),
    )


def pacing_score(adapted: AdaptedPattern, timeline: Sequence[TimelineSegment], clips: Mapping[str, ClipIntelligence], report: ValidationReport) -> Score:
    act_means, targets, ratios = [], [], []
    for a in adapted.acts:
        segs = [s for s in timeline if s.act_id == a.act_id]
        if not segs:
            continue
        act_means.append(sum(s.energy for s in segs) / len(segs))
        targets.append(sum(a.target_energy) / 2)
        for s in segs:
            expected = a.cut_length_s * (1.5 if s.is_hero else 1.0)
            ratios.append(s.duration / expected)
    r = _pearson(act_means, targets)
    if r is None:
        shape = 1 - clamp(sum(abs(m - t) for m, t in zip(act_means, targets)) / (len(act_means) * 5))
        shape_why = f"mean distance to target energy {sum(abs(m - t) for m, t in zip(act_means, targets)) / len(act_means):.1f}"
    else:
        shape = (r + 1) / 2
        shape_why = f"energy-curve correlation with pattern {r:+.2f}"
    spikes = sum(1 for v in report.violations if v.rule == ValidationRule.NO_ABRUPT_ENERGY_SPIKES)
    # Adherence to the pattern's relative rhythm: global duration scaling is fine, uneven distortion is not.
    # Cuts clamped by short source clips count against adherence.
    ref = sorted(ratios)[len(ratios) // 2]
    adh = 1 - clamp(sum(abs(x / ref - 1) for x in ratios) / len(ratios))
    value = 10 * (0.5 * shape + 0.3 * adh + 0.2 * (1 - clamp(spikes / max(1, len(timeline) - 1))))
    sel = _selected(timeline, clips)
    conf = sum(c.energy_confidence for c in sel) / len(sel)
    return Score(
        value=round(value, 2), confidence=round(conf, 3),
        reasoning=f"{shape_why}; cut-length adherence {adh:.2f}; {spikes} energy spike(s)",
    )
