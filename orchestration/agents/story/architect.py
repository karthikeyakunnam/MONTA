"""
MONTA — Story Architect (Layer 7)
===================================
Designs the narrative; never edits video. Consumes only a ``ContextPack``.

    1. Select a pattern from the Story Pattern Library (intent × footage × memory).
    2. Adapt it (creator arc, footage volume, platform duration).
    3. Assign clips to acts and pick one hero moment.
    4. Build the timeline and repair it against the validator.
    5. Optionally let an LLM editor propose a better assignment (accepted only
       if it validates at least as well and scores at least as high).
    6. Score, compute confidence, and emit a fully explained ``StoryPlan``.

Deterministic for a given ContextPack when no refiner is configured.
"""

import logging
from collections.abc import Sequence

from orchestration.agents.story import assembly
from orchestration.agents.story.patterns import DEFAULT_LIBRARY, PatternLibrary
from orchestration.agents.story.refiner import StoryRefiner
from orchestration.agents.story.scoring import emotion_score, pacing_score, story_score
from orchestration.agents.story.selection import AdaptedPattern, PatternChoice, adapt_pattern, select_pattern
from orchestration.agents.story.validator import TimelineValidator
from shared.contracts.context import ContextPack
from shared.contracts.explain import Evidence, Explained, clamp
from shared.contracts.story import ActRationale, StoryPlan, StoryReasoning
from shared.contracts.vocab import Emotion, Pace
from shared.exceptions import PipelineError
from shared.observability import catalog as m
from shared.observability.tracing import span
from shared.providers.errors import ProviderError

logger = logging.getLogger("monta.story")

ALGORITHM_VERSION = "architect.v2"


def record_story_metrics(plan: StoryPlan) -> None:
    m.PATTERN_SELECTED.inc(pattern=plan.story_pattern)
    m.STORY_VALIDATION.inc(profile=plan.validation.profile, passed=str(plan.validation.passed).lower())
    for v in plan.validation.violations:
        m.STORY_VIOLATIONS.inc(rule=v.rule.value, severity=v.severity)
    m.STORY_DURATION_DEVIATION.observe(abs(plan.total_duration_s - plan.target_duration_s) / plan.target_duration_s)


class StoryDesignError(PipelineError):
    """No valid story can be built from this footage."""


class StoryArchitect:
    def __init__(
        self,
        *,
        library: PatternLibrary = DEFAULT_LIBRARY,
        validator: TimelineValidator | None = None,
        refiner: StoryRefiner | None = None,
    ):
        self.library = library
        self.validator = validator or TimelineValidator()
        self.refiner = refiner

    async def design(self, pack: ContextPack, *, avoid_patterns: Sequence[str] = ()) -> StoryPlan:
        with span("story.design", clips=len(pack.clip_intelligence), avoided=len(avoid_patterns)) as sp:
            plan = await self._design(pack, avoid_patterns=avoid_patterns)
            sp.set(pattern=plan.story_pattern, valid=plan.validation.passed, cuts=len(plan.timeline))
        record_story_metrics(plan)
        return plan

    async def _design(self, pack: ContextPack, *, avoid_patterns: Sequence[str] = ()) -> StoryPlan:
        clips = {cid: c for cid, c in pack.clip_intelligence.items()}
        usable = [c for c in clips.values() if c.usable]
        if not usable:
            raise StoryDesignError("no usable clips: " + "; ".join(
                f"{c.clip_id}: {c.unusable_reason}" for c in clips.values()) or "no clips were analyzed")

        intent = pack.intent
        choice = select_pattern(pack, list(self.library), usable, avoid=avoid_patterns)
        adapted = adapt_pattern(choice.pattern, pack, usable)
        emotion = intent.emotion.value
        pace = intent.pace.value

        try:
            draft = assembly.assign(adapted, clips, emotion)
        except ValueError as e:
            raise StoryDesignError(str(e)) from e
        draft = assembly.fit_to_duration(adapted, draft, clips, emotion)
        best, timeline, report, repairs = assembly.repair(adapted, draft, clips, self.validator, pace, emotion)

        refinement_note = None
        if self.refiner is not None:
            best, timeline, report, refinement_note = await self._refine(pack, adapted, choice, best, timeline, report, clips, pace, emotion)

        s_story = story_score(adapted, timeline, clips, best.fits, choice.confidence, report)
        s_emotion = emotion_score(adapted, timeline, clips, emotion, report)
        s_pacing = pacing_score(adapted, timeline, clips, report)

        analysis_conf = sum(c.quality_confidence + c.energy_confidence for c in usable) / (2 * len(usable))
        hard_conflicts = sum(1 for c in intent.conflicts if c.severity == "hard")
        confidence = clamp(
            (0.4 * choice.confidence + 0.3 * analysis_conf + 0.3 * (1.0 if report.passed else 0.4)) * (1 - 0.1 * hard_conflicts)
        )

        return StoryPlan(
            story_pattern=adapted.pattern.pattern_id,
            pattern_version=adapted.pattern.version,
            story_confidence=round(confidence, 3),
            act_assignments={a.act_id: tuple(best.acts[a.act_id]) for a in adapted.acts},
            timeline=timeline,
            pace=self._pace_decision(adapted, pace),
            emotion=self._emotion_decision(adapted, emotion, intent.emotion.confidence, intent.emotion.reasoning),
            reasoning=StoryReasoning(
                pattern_selection=choice.reasoning,
                candidates=choice.candidates,
                adaptations=adapted.adaptations + tuple(best.notes),
                pacing=self._pacing_reasoning(adapted),
                emotion=f"Target emotion '{emotion.value}' ({intent.emotion.reasoning}); pattern requires "
                        f"{', '.join(e.value for e in adapted.pattern.required_emotions)}.",
                clip_decisions=assembly.clip_decisions(best, clips),
                acts=self._act_rationales(adapted, best, timeline),
                repairs=tuple(repairs),
                refinement=refinement_note,
            ),
            story_score=s_story,
            emotion_score=s_emotion,
            pacing_score=s_pacing,
            validation=report,
            total_duration_s=timeline[-1].end,
            target_duration_s=adapted.target_duration_s,
            requested_duration_s=adapted.requested_duration_s,
            hero_clip_id=best.hero,
            algorithm_version=ALGORITHM_VERSION,
        )

    async def _refine(self, pack, adapted: AdaptedPattern, choice: PatternChoice, base, timeline, report, clips, pace, emotion):
        try:
            proposal = await self.refiner.propose(pack, adapted, base.acts, base.hero, clips)
        except ProviderError as e:
            logger.warning("story refinement failed: %s", e)
            return base, timeline, report, f"LLM refinement unavailable ({type(e).__name__}); deterministic plan kept."

        candidate = base.copy()
        candidate.acts = {k: list(v) for k, v in proposal.act_assignments.items()}
        candidate.hero = proposal.hero_clip_id
        chosen = set(candidate.selected)
        for a in adapted.acts:
            for cid in candidate.acts[a.act_id]:
                candidate.fits[cid] = assembly.clip_fit(clips[cid], a, emotion)
                candidate.rejected.pop(cid, None)
        for cid in list(candidate.fits):
            if cid not in chosen:
                candidate.rejected[cid] = (candidate.fits.pop(cid)[0], ["removed by LLM editor refinement"])
        new_tl = assembly.build_timeline(adapted, candidate, clips)
        new_report = self.validator.validate(new_tl, assembly.act_specs(adapted), clips, pace, rejected=set(candidate.rejected),
                                             config=assembly.validation_config(adapted),
                                             target_duration_s=adapted.target_duration_s)

        def combined(tl, rep, fits):
            return (story_score(adapted, tl, clips, fits, choice.confidence, rep).value
                    + emotion_score(adapted, tl, clips, emotion, rep).value
                    + pacing_score(adapted, tl, clips, rep).value)

        old_c, new_c = combined(timeline, report, base.fits), combined(new_tl, new_report, candidate.fits)
        summary = f"{self.refiner.model_id}: {proposal.reasoning.summary}"
        if new_report.error_count <= report.error_count and new_c >= old_c:
            changes = "; ".join(proposal.reasoning.changes) or "no structural changes"
            return candidate, new_tl, new_report, f"Adopted LLM refinement ({summary}; {changes}). Combined score {old_c:.1f} → {new_c:.1f}."
        return base, timeline, report, (
            f"Rejected LLM refinement ({summary}): errors {report.error_count} → {new_report.error_count}, "
            f"combined score {old_c:.1f} → {new_c:.1f}."
        )

    @staticmethod
    def _pace_decision(adapted: AdaptedPattern, pace: Pace) -> Explained[Pace]:
        per_act = ", ".join(f"{a.act_id}={a.pace.value} ({a.cut_length_s:.2f}s cuts)" for a in adapted.acts)
        return Explained[Pace](
            value=pace, confidence=1.0 if all(a.pace == pace for a in adapted.acts) else 0.9,
            reasoning=f"Global pace '{pace.value}' shaped by the '{adapted.pattern.name}' pacing curve: {per_act}.",
            evidence=tuple(Evidence(source="pattern", detail=n) for n in adapted.adaptations if "paced" in n),
        )

    @staticmethod
    def _emotion_decision(adapted: AdaptedPattern, emotion: Emotion, confidence: float, why: str) -> Explained[Emotion]:
        return Explained[Emotion](
            value=emotion, confidence=confidence,
            reasoning=f"From intent: {why} Delivered through the '{adapted.pattern.name}' arc "
                      f"({' → '.join(a.template.name for a in adapted.acts)}).",
        )

    @staticmethod
    def _pacing_reasoning(adapted: AdaptedPattern) -> str:
        return (f"Pattern '{adapted.pattern.pattern_id}' curve {adapted.pattern.pacing_curve} applied to "
                f"'{adapted.global_pace.value}' base pace; target {adapted.target_duration_s:.1f}s; hero at "
                f"{adapted.pattern.hero_moment_position:.0%} of runtime in act '{adapted.acts[adapted.hero_act_index].act_id}'.")

    @staticmethod
    def _act_rationales(adapted: AdaptedPattern, assignment, timeline) -> tuple[ActRationale, ...]:
        out = []
        for a in adapted.acts:
            segs = [s for s in timeline if s.act_id == a.act_id]
            mean = sum(s.energy for s in segs) / len(segs) if segs else 0.0
            out.append(ActRationale(
                act_id=a.act_id, name=a.template.name, purpose=a.template.purpose,
                why_exists=f"'{adapted.pattern.name}' act {adapted.acts.index(a) + 1}/{len(adapted.acts)}: {a.template.purpose} "
                           f"({a.template.share:.0%} of runtime, energy {a.target_energy[0]:.1f}–{a.target_energy[1]:.1f}, {a.template.energy_direction}).",
                clip_ids=tuple(assignment.acts.get(a.act_id, [])), energy_direction=a.template.energy_direction,
                target_energy=a.target_energy, realized_mean_energy=round(mean, 2), cut_length_s=a.cut_length_s,
            ))
        return tuple(out)
