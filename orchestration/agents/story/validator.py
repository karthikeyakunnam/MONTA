"""
MONTA — Timeline Validator (Layer 7)
======================================
Independent rule checks over a story timeline. Used by the Story Architect
(to drive repair) and again by the Director's ``validate_story`` task.

Rules are **pattern-specific**: thresholds come from the pattern's
``PatternValidation`` profile, so a podcast is not judged by fitness-reel
energy rules.

Errors:
* no_gaps / no_overlaps           — contiguous output timeline from 0.
* no_invalid_clips                — unknown, unusable or rejected clips; unknown acts.
* single_hero_moment              — exactly one hero segment.
* no_abrupt_energy_spikes         — |Δenergy| ≤ pace limit × pattern spike_scale. A *drop* into an act
                                    whose direction is "fall" (the release after a climax) is a deliberate
                                    editing device and gets RELEASE_DROP_FACTOR × the limit.
* max_low_energy_run              — per-pattern threshold and run length, counted in *shots*: consecutive
                                    pieces of one split take are one shot.
* act_emotional_progression       — act follows its energy direction and differs from the
                                    previous act by energy (per-pattern delta) or emotion.
* within_source_duration          — source window inside the clip, length = cut length.
* no_source_overlap               — two cuts from one clip never reuse the same footage.
* no_empty_acts                   — every act has at least one cut.
* target_duration                 — total within the pattern's duration tolerance (±5% default).
Warnings:
* no_jump_cuts                    — consecutive cuts from the same clip.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from shared.contracts.clip import ClipIntelligence
from shared.contracts.story import (
    EnergyDirection,
    PatternValidation,
    StoryPlan,
    TimelineSegment,
    ValidationReport,
    ValidationRule,
    ValidationViolation,
)
from shared.contracts.vocab import Pace

BASE_ENERGY_JUMP: dict[Pace, float] = {Pace.SLOW: 3.0, Pace.MEDIUM: 4.0, Pace.FAST: 4.5, Pace.AGGRESSIVE: 5.5}
RELEASE_DROP_FACTOR = 1.5
EPSILON = 0.011


@dataclass(frozen=True)
class ActSpec:
    act_id: str
    direction: EnergyDirection


@dataclass(frozen=True)
class ValidationConfig:
    """Resolved rule thresholds: global pace limits × a pattern's profile."""

    profile_name: str = "default"
    profile: PatternValidation = field(default_factory=PatternValidation)
    base_energy_jump: Mapping[Pace, float] = field(default_factory=lambda: dict(BASE_ENERGY_JUMP))
    epsilon: float = EPSILON

    @classmethod
    def for_pattern(cls, pattern_id: str, profile: PatternValidation) -> "ValidationConfig":
        return cls(profile_name=pattern_id, profile=profile)

    def energy_jump(self, pace: Pace) -> float:
        return self.base_energy_jump[pace] * self.profile.spike_scale


class TimelineValidator:
    def __init__(self, library=None):
        """``library`` resolves a plan's pattern to its validation profile (defaults to the Story Pattern Library)."""
        if library is None:
            from orchestration.agents.story.patterns import DEFAULT_LIBRARY
            library = DEFAULT_LIBRARY
        self.library = library

    def config_for(self, pattern_id: str | None) -> ValidationConfig:
        if pattern_id is None:
            return ValidationConfig()
        pattern = self.library.get(pattern_id)
        return ValidationConfig.for_pattern(pattern.pattern_id, pattern.validation)

    def validate_plan(self, plan: StoryPlan, clips: Mapping[str, ClipIntelligence]) -> ValidationReport:
        acts = [ActSpec(a.act_id, a.energy_direction) for a in plan.reasoning.acts]
        rejected = {d.clip_id for d in plan.reasoning.clip_decisions if not d.selected}
        return self.validate(plan.timeline, acts, clips, plan.pace.value, rejected=rejected,
                             config=self.config_for(plan.story_pattern), target_duration_s=plan.target_duration_s)

    def validate(
        self,
        timeline: Sequence[TimelineSegment],
        acts: Sequence[ActSpec],
        clips: Mapping[str, ClipIntelligence],
        pace: Pace,
        *,
        rejected: set[str] | frozenset[str] = frozenset(),
        config: ValidationConfig | None = None,
        target_duration_s: float | None = None,
    ) -> ValidationReport:
        c = config or ValidationConfig()
        prof = c.profile
        v: list[ValidationViolation] = []

        def add(rule, msg, idx=(), ids=(), severity="error"):
            v.append(ValidationViolation(rule=rule, severity=severity, message=msg, segment_indices=tuple(idx), clip_ids=tuple(ids)))

        segs = list(timeline)
        if segs and segs[0].start > c.epsilon:
            add(ValidationRule.NO_GAPS, f"timeline starts at {segs[0].start:.3f}s, not 0", [0], [segs[0].clip_id])
        for prev, cur in zip(segs, segs[1:]):
            gap = cur.start - prev.end
            if gap > c.epsilon:
                add(ValidationRule.NO_GAPS, f"{gap:.3f}s gap before segment {cur.index}", [prev.index, cur.index], [prev.clip_id, cur.clip_id])
            elif gap < -c.epsilon:
                add(ValidationRule.NO_OVERLAPS, f"segment {cur.index} overlaps previous by {-gap:.3f}s",
                    [prev.index, cur.index], [prev.clip_id, cur.clip_id])
            if cur.clip_id == prev.clip_id:
                add(ValidationRule.NO_JUMP_CUTS, f"segments {prev.index} and {cur.index} both come from '{cur.clip_id}'",
                    [prev.index, cur.index], [cur.clip_id], severity="warning")

        act_ids = {a.act_id for a in acts}
        windows: dict[str, list[TimelineSegment]] = defaultdict(list)
        for s in segs:
            clip = clips.get(s.clip_id)
            if clip is None:
                add(ValidationRule.NO_INVALID_CLIPS, f"unknown clip '{s.clip_id}'", [s.index], [s.clip_id])
                continue
            windows[s.clip_id].append(s)
            if not clip.usable:
                add(ValidationRule.NO_INVALID_CLIPS, f"clip '{s.clip_id}' is unusable: {clip.unusable_reason}", [s.index], [s.clip_id])
            if s.clip_id in rejected:
                add(ValidationRule.NO_INVALID_CLIPS, f"clip '{s.clip_id}' was rejected but is on the timeline", [s.index], [s.clip_id])
            if s.act_id not in act_ids:
                add(ValidationRule.NO_INVALID_CLIPS, f"segment {s.index} references unknown act '{s.act_id}'", [s.index], [s.clip_id])
            if s.source_out > clip.duration_s + c.epsilon or s.duration > clip.duration_s + c.epsilon:
                add(ValidationRule.WITHIN_SOURCE_DURATION,
                    f"segment {s.index} needs {s.source_in:.2f}–{s.source_out:.2f}s of a {clip.duration_s:.2f}s clip",
                    [s.index], [s.clip_id])
            elif abs((s.source_out - s.source_in) - s.duration) > c.epsilon:
                add(ValidationRule.WITHIN_SOURCE_DURATION,
                    f"segment {s.index} source window {s.source_out - s.source_in:.3f}s ≠ cut length {s.duration:.3f}s",
                    [s.index], [s.clip_id])
        for clip_id, ws in windows.items():
            ordered = sorted(ws, key=lambda s: s.source_in)
            for a, b in zip(ordered, ordered[1:]):
                if b.source_in < a.source_out - c.epsilon:
                    add(ValidationRule.NO_SOURCE_OVERLAP,
                        f"segments {a.index} and {b.index} reuse {clip_id} footage {b.source_in:.2f}–{min(a.source_out, b.source_out):.2f}s",
                        [a.index, b.index], [clip_id])

        heroes = [s for s in segs if s.is_hero]
        if len(heroes) != 1:
            add(ValidationRule.SINGLE_HERO_MOMENT, f"expected exactly 1 hero moment, found {len(heroes)}",
                [s.index for s in heroes], [s.clip_id for s in heroes])

        base_limit = c.energy_jump(pace)
        direction_of = {a.act_id: a.direction for a in acts}
        for prev, cur in zip(segs, segs[1:]):
            jump = abs(cur.energy - prev.energy)
            release = cur.energy < prev.energy and cur.act_id != prev.act_id and direction_of.get(cur.act_id) == "fall"
            limit = base_limit * (RELEASE_DROP_FACTOR if release else 1.0)
            if jump > limit + 1e-9:
                add(ValidationRule.NO_ABRUPT_ENERGY_SPIKES,
                    f"energy jumps {prev.energy:.1f} → {cur.energy:.1f} (Δ{jump:.1f} > {limit:.1f} for {pace.value} {c.profile_name})",
                    [prev.index, cur.index], [prev.clip_id, cur.clip_id])

        run: list[TimelineSegment] = []
        for s in segs + [None]:
            if s is not None and s.energy < prof.low_energy_threshold:
                run.append(s)
                continue
            shots = sum(1 for i, r in enumerate(run) if i == 0 or r.clip_id != run[i - 1].clip_id)
            if shots > prof.max_low_energy_run:
                add(ValidationRule.MAX_LOW_ENERGY_RUN,
                    f"{shots} consecutive low-energy shots (< {prof.low_energy_threshold}); max {prof.max_low_energy_run} for {c.profile_name}",
                    [r.index for r in run], [r.clip_id for r in run])
            run = []

        by_act: dict[str, list[TimelineSegment]] = {a.act_id: [] for a in acts}
        for s in segs:
            by_act.setdefault(s.act_id, []).append(s)
        prev_mean: float | None = None
        prev_emotion = None
        for a in acts:
            act_segs = by_act.get(a.act_id, [])
            if not act_segs:
                add(ValidationRule.NO_EMPTY_ACTS, f"act '{a.act_id}' has no clips")
                prev_mean, prev_emotion = None, None
                continue
            energies = [s.energy for s in act_segs]
            ok, why = self._direction_ok(a.direction, energies, prof)
            if not ok:
                add(ValidationRule.ACT_EMOTIONAL_PROGRESSION, f"act '{a.act_id}' should {a.direction}: {why}",
                    [s.index for s in act_segs], [s.clip_id for s in act_segs])
            mean = sum(energies) / len(energies)
            emotion = self._dominant_emotion(act_segs, clips)
            if prev_mean is not None and abs(mean - prev_mean) < prof.min_act_energy_delta and emotion == prev_emotion:
                add(ValidationRule.ACT_EMOTIONAL_PROGRESSION,
                    f"act '{a.act_id}' repeats the previous act (energy {mean:.1f} vs {prev_mean:.1f}, same emotion)",
                    [s.index for s in act_segs], [s.clip_id for s in act_segs])
            prev_mean, prev_emotion = mean, emotion

        if target_duration_s and segs:
            total = segs[-1].end
            deviation = abs(total - target_duration_s) / target_duration_s
            if deviation > prof.duration_tolerance + 1e-9:
                add(ValidationRule.TARGET_DURATION,
                    f"total {total:.2f}s deviates {deviation:.1%} from target {target_duration_s:.2f}s "
                    f"(tolerance {prof.duration_tolerance:.0%})")

        errors = [x for x in v if x.severity == "error"]
        failed = {x.rule for x in errors}
        rules = tuple(ValidationRule)
        if not v:
            summary = "all rules passed"
        elif not errors:
            summary = f"passed with {len(v)} warning(s): " + ", ".join(sorted({x.rule.value for x in v}))
        else:
            summary = f"{len(errors)} error(s) across {len(failed)} rule(s): " + ", ".join(sorted(r.value for r in failed))
        return ValidationReport(passed=not errors, profile=c.profile_name, rules_checked=rules, violations=tuple(v), summary=summary)

    @staticmethod
    def _direction_ok(direction: EnergyDirection, energies: list[float], prof: PatternValidation) -> tuple[bool, str]:
        if len(energies) < 2:
            return True, ""
        tol = prof.direction_tolerance
        half = len(energies) // 2
        first, second = sum(energies[:half]) / half, sum(energies[-half:]) / half
        if direction == "rise" and (energies[-1] < energies[0] - tol or second < first - tol):
            return False, f"energy falls {energies[0]:.1f} → {energies[-1]:.1f}"
        if direction == "fall" and (energies[-1] > energies[0] + tol or second > first + tol):
            return False, f"energy rises {energies[0]:.1f} → {energies[-1]:.1f}"
        if direction == "hold" and max(energies) - min(energies) > prof.hold_tolerance:
            return False, f"energy range {min(energies):.1f}–{max(energies):.1f} exceeds {prof.hold_tolerance}"
        return True, ""

    @staticmethod
    def _dominant_emotion(segs: list[TimelineSegment], clips: Mapping[str, ClipIntelligence]):
        counts = Counter(clips[s.clip_id].emotion for s in segs if s.clip_id in clips and clips[s.clip_id].emotion)
        return counts.most_common(1)[0][0] if counts else None
