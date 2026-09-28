"""
MONTA — Pattern Selection & Adaptation (Layer 7, steps 1–2)
=============================================================
Selection scores every library pattern on three independent axes and
explains each:

* intent fit (0.5)   — genre/emotion match weighted by Layer 3 confidence, keyword hits.
* footage fit (0.35) — can the analyzed clips actually fill each act's roles?
* memory (0.15)      — Bayesian prior from narrative memory for this project type.
Creator preferences add a small bonus or exclude a pattern.

Adaptation specializes the chosen pattern: arc instructions ("slow start")
override act pacing and energy, acts merge when there is too little footage,
and a target duration is fixed from intent → platform → available footage.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from shared.contracts.base import revalidate
from shared.contracts.clip import ClipIntelligence
from shared.contracts.context import ContextPack
from shared.contracts.explain import clamp
from shared.contracts.story import ActTemplate, PatternScore, StoryPattern
from shared.contracts.vocab import EMOTION_AFFINITY, PACE_ORDER, Pace
from shared.text import phrases_in, tokenize

BASE_CUT_S: dict[Pace, float] = {Pace.SLOW: 3.2, Pace.MEDIUM: 2.2, Pace.FAST: 1.4, Pace.AGGRESSIVE: 0.9}
ROLE_RANK_DECAY = 0.15
PREFERRED_BONUS = 0.08
FOOTAGE_HEADROOM = 0.97  # never plan the last 3% of all usable footage: leaves room for window placement


def role_match(clip: ClipIntelligence, roles: Sequence) -> float:
    """Best role confidence, discounted by the role's rank in the act's preference list."""
    return max((clip.role_confidence(r) * (1 - ROLE_RANK_DECAY * i) for i, r in enumerate(roles)), default=0.0)


def emotion_related(a, b) -> bool:
    return a == b or any({a, b} <= g for g in EMOTION_AFFINITY)


@dataclass(frozen=True)
class PatternChoice:
    pattern: StoryPattern
    confidence: float
    reasoning: str
    candidates: tuple[PatternScore, ...]


def score_patterns(pack: ContextPack, patterns: Sequence[StoryPattern], clips: Sequence[ClipIntelligence]) -> list[PatternScore]:
    intent = pack.intent
    prompt_tokens = tokenize(intent.normalized_prompt)
    end_beat = intent.arc_for("end")
    preferred = set(pack.user_preferences.preferred_patterns)
    out = []
    for p in patterns:
        g = intent.genre.confidence * (1.0 if p.genres[0] == intent.genre.value else 0.85) if intent.genre.value in p.genres else 0.0
        if intent.emotion.value in p.emotions:
            e = intent.emotion.confidence
        elif any(emotion_related(intent.emotion.value, pe) for pe in p.emotions):
            e = 0.5 * intent.emotion.confidence
        else:
            e = 0.0
        if end_beat and end_beat.emotion and end_beat.emotion in p.emotions:
            e = min(1.0, e + 0.1)
        hits = phrases_in(prompt_tokens, p.keywords)
        kw = min(1.0, 0.5 * len(hits))
        intent_fit = 0.45 * g + 0.3 * e + 0.25 * kw

        if clips:
            per_act = [max(role_match(c, a.preferred_roles) for c in clips) for a in p.acts]
            coverage = sum(min(1.0, b / 0.5) for b in per_act) / len(per_act)
            count_factor = min(1.0, len(clips) / len(p.acts))
            footage_fit = 0.8 * coverage + 0.2 * count_factor
        else:
            coverage, footage_fit = 0.0, 0.0

        prior = pack.prior_for(p.pattern_id)
        memory = (prior.success_mean - 0.5) * prior.confidence if prior else 0.0
        pref = PREFERRED_BONUS if p.pattern_id in preferred else 0.0

        total = 0.5 * intent_fit + 0.35 * footage_fit + 0.15 * (0.5 + memory) + pref
        reasoning = (
            f"intent {intent_fit:.2f} (genre {g:.2f}, emotion {e:.2f}, keywords {hits or 'none'}); "
            f"footage {footage_fit:.2f} (role coverage {coverage:.2f}, {len(clips)} usable clips); "
            f"memory {memory:+.2f} ({prior.reasoning if prior else 'no prior'})"
            + ("; preferred by creator" if pref else "")
        )
        out.append(PatternScore(pattern_id=p.pattern_id, total=round(total, 4), intent_fit=round(intent_fit, 4),
                                footage_fit=round(footage_fit, 4), memory_prior=round(memory, 4),
                                preference_adjustment=pref, reasoning=reasoning))
    out.sort(key=lambda s: s.total, reverse=True)
    return out


def select_pattern(
    pack: ContextPack, patterns: Sequence[StoryPattern], clips: Sequence[ClipIntelligence], *, avoid: Sequence[str] = ()
) -> PatternChoice:
    excluded = set(avoid) | set(pack.user_preferences.disliked_patterns)
    eligible = [p for p in patterns if p.pattern_id not in excluded] or [p for p in patterns if p.pattern_id not in set(avoid)] or list(patterns)
    scores = score_patterns(pack, eligible, clips)
    best, runner = scores[0], scores[1] if len(scores) > 1 else None
    margin = best.total - runner.total if runner else best.total
    confidence = round(clamp(best.total * (0.6 + 0.4 * clamp(margin / 0.1))), 3)
    pattern = next(p for p in eligible if p.pattern_id == best.pattern_id)
    reasoning = f"Selected '{pattern.name}' (score {best.total:.2f}): {best.reasoning}."
    if runner:
        reasoning += f" Runner-up '{runner.pattern_id}' scored {runner.total:.2f} (margin {margin:.2f})."
    if excluded & {p.pattern_id for p in patterns}:
        reasoning += f" Excluded: {', '.join(sorted(excluded))}."
    return PatternChoice(pattern=pattern, confidence=confidence, reasoning=reasoning, candidates=tuple(scores))


# ---------------------------------------------------------------------------- adaptation


@dataclass(frozen=True)
class AdaptedAct:
    template: ActTemplate
    pace: Pace
    cut_length_s: float
    target_energy: tuple[float, float]
    preferred_emotions: tuple
    start_fraction: float

    @property
    def act_id(self) -> str:
        return self.template.act_id


@dataclass(frozen=True)
class AdaptedPattern:
    pattern: StoryPattern
    acts: tuple[AdaptedAct, ...]
    target_duration_s: float
    hero_act_index: int
    adaptations: tuple[str, ...]
    global_pace: Pace
    requested_duration_s: float | None = None

    def act(self, act_id: str) -> AdaptedAct:
        return next(a for a in self.acts if a.act_id == act_id)


def _section_for(index: int, count: int) -> str:
    if index == 0:
        return "start"
    if index == count - 1:
        return "end"
    return "middle"


def _shift(rng: tuple[float, float], delta: float) -> tuple[float, float]:
    lo, hi = rng
    return (clamp(lo + delta, 0, 10), clamp(hi + delta, 0, 10))


def adapt_pattern(pattern: StoryPattern, pack: ContextPack, clips: Sequence[ClipIntelligence]) -> AdaptedPattern:
    intent = pack.intent
    notes: list[str] = []
    pace = intent.pace.value

    # 1. Merge acts when footage is scarce (never drop the hero act).
    acts = list(pattern.acts)
    hero_template = pattern.acts[pattern.act_at(pattern.hero_moment_position)]
    while len(acts) > max(1, len(clips)):
        removable = [a for a in acts if a is not hero_template]
        victim = min(removable, key=lambda a: a.share)
        acts.remove(victim)
        notes.append(f"dropped act '{victim.act_id}' — only {len(clips)} usable clip(s) for {len(pattern.acts)} acts")
    total_share = sum(a.share for a in acts)

    # 2. Target duration: explicit → platform sweet spot, bounded by platform max and usable footage.
    #    The planner then hits this target exactly (see assembly.build_timeline).
    requested = intent.target_duration_s.value if intent.target_duration_s else None
    available = sum(c.duration_s for c in clips) * FOOTAGE_HEADROOM
    if requested:
        target, source = requested, "requested"
    else:
        lo, hi = pack.platform.ideal_duration_s
        target, source = (lo + hi) / 2, f"{pack.platform.platform.value} sweet spot {lo:.0f}–{hi:.0f}s"
    if target > pack.platform.max_duration_s:
        notes.append(f"target capped from {target:.1f}s to the {pack.platform.platform.value} maximum {pack.platform.max_duration_s:.0f}s")
        target = pack.platform.max_duration_s
    if target > available:
        notes.append(f"footage-limited: only {available:.1f}s of usable footage for a {target:.1f}s target")
        target = available
    notes.append(f"target duration {target:.1f}s ({source})")

    # 3. Per-act pace/energy from the creator's arc, else global pace.
    adapted: list[AdaptedAct] = []
    cursor = 0.0
    for i, a in enumerate(acts):
        section = _section_for(i, len(acts))
        beat = intent.arc_for(section)
        act_pace, energy, emotions = pace, a.target_energy, a.preferred_emotions
        if beat and beat.pace:
            act_pace = beat.pace
            delta = (PACE_ORDER[beat.pace] - PACE_ORDER[pace]) * 0.75
            energy = _shift(a.target_energy, delta)
            notes.append(f"act '{a.act_id}' paced {beat.pace.value} and energy shifted {delta:+.2f} per '{beat.span}'")
        if beat and beat.emotion and beat.emotion not in emotions:
            emotions = (beat.emotion, *emotions)
            notes.append(f"act '{a.act_id}' leads with emotion '{beat.emotion.value}' per '{beat.span}'")
        cut = round(BASE_CUT_S[act_pace] * pattern.pacing_curve[pattern.acts.index(a)], 3)
        share = a.share / total_share
        adapted.append(AdaptedAct(template=revalidate(a, **{"share": share}), pace=act_pace, cut_length_s=cut,
                                  target_energy=energy, preferred_emotions=tuple(emotions), start_fraction=cursor))
        cursor += share

    hero_index = next(i for i, a in enumerate(adapted) if a.template.act_id == hero_template.act_id)
    return AdaptedPattern(pattern=pattern, acts=tuple(adapted), target_duration_s=round(target, 3),
                          hero_act_index=hero_index, adaptations=tuple(notes), global_pace=pace,
                          requested_duration_s=requested)
