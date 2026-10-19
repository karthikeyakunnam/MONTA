"""
MONTA — Layers 3–7 Pipeline
=============================
Composition root for the intelligence half of MONTA:

    prompt ─► IntentEngine (L3)
    clips  ─► probe ─► ContextComposer (L4) ─► ContextPack
                         DirectorAgent (L5) plans & executes:
                            analyze_clip ×N (L6) → reconcile_intent (L3/L4)
                            → design_story (L7) → validate_story (L7)

``build_pipeline`` wires production dependencies from the environment; tests
and alternative deployments construct ``MontaPipeline`` directly with their
own stores/backends.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from memory.narrative_memory import (
    InMemoryNarrativeStore,
    InMemoryPreferenceStore,
    NarrativeLearner,
    NarrativeMemoryStore,
    PreferenceStore,
    SqlNarrativeStore,
    SqlPreferenceStore,
)
from orchestration.agents.director import AgentRegistry, DirectorAgent, DirectorPlanner, ExecutionContext
from orchestration.agents.director.executor import EventSink
from orchestration.agents.intelligence import FFmpegMediaBackend, FileIntelligenceCache, MediaBackend, MediaToolError, VideoIntelligenceTeam, VisionAnalyzer
from evaluation.story_judge import HeuristicStoryJudge, StoryJudge
from orchestration.agents.story import DEFAULT_LIBRARY, PatternLibrary, StoryArchitect, StoryDesignError, StoryRefiner, TimelineValidator
from services.context_composer import ContextComposer
from services.prompt_engine import IntentEngine
from shared.contracts.clip import ClipIntelligence, ClipSource, TechnicalMetadata
from shared.contracts.base import revalidate
from shared.contracts.context import ContextPack
from shared.contracts.director import ExecutionPlan, ExecutionReport, TaskResult, TaskSpec, TaskStatus
from shared.contracts.evaluation import StoryJudgement
from shared.contracts.intent import IntentAnalysis
from shared.contracts.story import StoryPlan, ValidationReport
from shared.contracts.timeline import AudioSegmentIR, TimelineIR
from shared.exceptions import PipelineError
from shared.observability import catalog as m
from shared.observability.context import bind, new_trace
from shared.observability.tracing import span
from shared.providers.registry import ProviderBundle, build_providers

logger = logging.getLogger("monta.pipeline")


@dataclass(frozen=True)
class ProbeFailure:
    clip_id: str
    reason: str


@dataclass(frozen=True)
class PipelineResult:
    intent: IntentAnalysis
    context_pack: ContextPack
    plan: ExecutionPlan
    report: ExecutionReport
    story: StoryPlan | None
    validation: ValidationReport | None
    probe_failures: tuple[ProbeFailure, ...]
    story_reuse_rejected: str | None = None


class MontaPipeline:
    def __init__(
        self,
        *,
        intent_engine: IntentEngine,
        composer: ContextComposer,
        media: MediaBackend,
        team: VideoIntelligenceTeam,
        architect: StoryArchitect,
        validator: TimelineValidator,
        judge: StoryJudge | None = None,
        max_concurrency: int = 8,
        probe_concurrency: int = 8,
        on_event: EventSink | None = None,
        require_local_ai: bool = False,
    ):
        self.intent_engine = intent_engine
        self.composer = composer
        self.media = media
        self.team = team
        self.architect = architect
        self.validator = validator
        self.judge = judge or HeuristicStoryJudge()
        self.require_local_ai = require_local_ai
        self._probe_sem = asyncio.Semaphore(probe_concurrency)
        planner = DirectorPlanner(vision_enabled=team.vision is not None, refiner_enabled=architect.refiner is not None,
                                  max_parallel_clips=max_concurrency)
        self.director = DirectorAgent(planner, self._registry(), max_concurrency=max_concurrency, on_event=on_event)

    # ------------------------------------------------------------------ agents

    def _registry(self) -> AgentRegistry:
        r = AgentRegistry()
        r.register("video_intelligence", self._analyze_clip)
        r.register("intent_reconciler", self._reconcile_intent)
        r.register("story_architect", self._design_story)
        r.register("timeline_validator", self._validate_story)
        return r

    async def _analyze_clip(self, task: TaskSpec, ctx: ExecutionContext) -> ClipIntelligence:
        meta = ctx.pack.metadata_for(task.params["clip_id"])
        if meta is None:
            raise PipelineError(f"no metadata for clip {task.params['clip_id']}")
        return await self.team.analyze(meta)

    async def _reconcile_intent(self, task: TaskSpec, ctx: ExecutionContext) -> IntentAnalysis:
        analyzed = {**ctx.pack.clip_intelligence, **{c.clip_id: c for c in ctx.dependency_outputs(task).values()}}
        intent = self.intent_engine.reconcile_with_footage(ctx.pack.intent, list(analyzed.values()))
        ctx.pack = await self.composer.enrich_with_footage(ctx.pack, intent, analyzed)
        return intent

    async def _design_story(self, task: TaskSpec, ctx: ExecutionContext) -> StoryPlan:
        if ctx.pack.intent.creative_brief.scene_beats:
            # Older persisted analysis contains a clip-wide cut count but no cut
            # timestamps. Measure boundaries locally before accepting source
            # windows; retain the existing semantic observations and provenance.
            from orchestration.agents.intelligence.signals import SIGNAL_VERSION, compute_signals
            analyzed = dict(ctx.pack.clip_intelligence)
            for meta in ctx.pack.video_metadata:
                clip = analyzed.get(meta.clip_id)
                if clip is None or clip.provenance.signal_version == SIGNAL_VERSION:
                    continue
                frames, fps = await self.media.sample_luma(
                    meta, max_frames=self.team.max_signal_frames, width=self.team.signal_width)
                signals = await asyncio.to_thread(compute_signals, frames, fps)
                analyzed[meta.clip_id] = revalidate(
                    clip, scene_cut_times_s=signals.cut_times_s,
                    sharpness_samples=signals.sharpness_samples,
                    provenance=revalidate(clip.provenance, signal_version=SIGNAL_VERSION),
                )
            ctx.pack = revalidate(ctx.pack, clip_intelligence=analyzed)
            # Enrichment is expensive and model observations can vary across
            # worker restarts. Reuse only evidence for the same source/model,
            # analyzer version, and ordered requested beats.
            if self.team.cache:
                beats_key = "|ordered-beats.v1:" + "|".join(
                    beat.description.casefold().strip()
                    for beat in ctx.pack.intent.creative_brief.scene_beats)
                for meta in ctx.pack.video_metadata:
                    cached = await self.team.cache.get(self.team.cache_key(meta) + beats_key)
                    current = analyzed.get(meta.clip_id)
                    if cached is not None and current is not None:
                        # Keep distinct source frames from both real analyses.
                        # A second model description of the *same* frame does
                        # not become independent corroboration.
                        observations = {item.time_s: item for item in cached.frame_evidence}
                        for item in current.frame_evidence:
                            observations.setdefault(item.time_s, item)
                        if len(observations) > len(current.frame_evidence):
                            analyzed[meta.clip_id] = revalidate(
                                current,
                                frame_evidence=tuple(sorted(
                                    observations.values(), key=lambda item: item.time_s)),
                                provenance=revalidate(current.provenance, cache_hit=True))
                ctx.pack = revalidate(ctx.pack, clip_intelligence=analyzed)
        try:
            return await self.architect.design(ctx.pack, avoid_patterns=task.params.get("avoid_patterns", ()))
        except StoryDesignError as initial_error:
            if (not ctx.pack.intent.creative_brief.scene_beats or
                    getattr(self.team.vision, "describe_frame", None) is None):
                raise
            from orchestration.agents.story.beat_matching import _candidates
            beats = ctx.pack.intent.creative_brief.scene_beats
            capacities = []
            for index, beat in enumerate(beats):
                shots = _candidates(index, beat, ctx.pack.clip_intelligence, None)
                capacities.append((sum(item.source_out_s - item.source_in_s for item in shots),
                                   index, beat))
            # Spend scarce local inference on the least substantiated beats.
            # The other beats retain their existing measured observations.
            least_supported = tuple(item[2] for item in sorted(capacities)[:2])
            # The second pass also revisits adjacent shots for the weak beats:
            # a verified action may last into the next measured short shot.
            # Already supported shots are skipped by the sampler.
            remaining = tuple(beats)
            last_error = initial_error
            for focus in (least_supported, remaining):
                if not focus:
                    continue
                enriched = dict(ctx.pack.clip_intelligence)
                for meta in ctx.pack.video_metadata:
                    clip = enriched.get(meta.clip_id)
                    if clip is not None:
                        enriched[meta.clip_id] = await self.team.enrich_short_scene_evidence(
                            meta, clip, focus)
                if enriched == ctx.pack.clip_intelligence:
                    continue
                ctx.pack = revalidate(ctx.pack, clip_intelligence=enriched)
                if self.team.cache:
                    for meta in ctx.pack.video_metadata:
                        clip = enriched.get(meta.clip_id)
                        if clip is not None:
                            # Cache provenance and uncertainty with the raw
                            # observations even when this plan still fails.
                            # Future runs may add independent source frames.
                            await self.team.cache.put(
                                self.team.cache_key(meta) + beats_key, clip)
                try:
                    story = await self.architect.design(
                        ctx.pack, avoid_patterns=task.params.get("avoid_patterns", ()))
                    return story
                except StoryDesignError as error:
                    last_error = error
            raise last_error

    async def _validate_story(self, task: TaskSpec, ctx: ExecutionContext) -> ValidationReport:
        return self.validator.validate_plan(ctx.results["design_story"], ctx.pack.clip_intelligence)

    # ------------------------------------------------------------------ stages

    async def interpret(self, prompt: str) -> IntentAnalysis:
        intent = await self.intent_engine.analyze(prompt)
        if self.require_local_ai:
            failure = next((reason for reason in intent.degraded if reason.startswith("LLM extraction failed:")), None)
            if failure:
                raise PipelineError(
                    "Configured local AI is required for this offline run but prompt inference failed: " + failure
                )
            if not any(extractor.startswith("llm:") for extractor in intent.extractors):
                raise PipelineError("Configured local AI is required for this offline run but no LLM result was recorded")
        return intent

    async def probe(self, clips: Sequence[ClipSource]) -> tuple[list[TechnicalMetadata], list[ProbeFailure]]:
        ids = [c.clip_id for c in clips]
        if len(set(ids)) != len(ids):
            raise PipelineError("clip_ids must be unique")

        async def one(c: ClipSource):
            async with self._probe_sem:
                try:
                    return await self.media.probe(c)
                except MediaToolError as e:
                    return ProbeFailure(c.clip_id, str(e))

        results = await asyncio.gather(*(one(c) for c in clips))
        metas = [r for r in results if isinstance(r, TechnicalMetadata)]
        failures = [r for r in results if isinstance(r, ProbeFailure)]
        return metas, failures

    async def detect_segment_crops(
        self, story: StoryPlan, pack: ContextPack,
    ) -> dict[int, tuple[int, int, int, int]]:
        """Use source-window evidence so mixed full-frame/matted clips are not cropped globally."""
        detector = getattr(self.media, "detect_content_crop_for_window", None)
        if detector is None:
            return {}
        metadata = {item.clip_id: item for item in pack.video_metadata}
        limit = asyncio.Semaphore(4)

        async def detect(cut):
            meta = metadata.get(cut.clip_id)
            if meta is None or meta.content_crop is not None:
                return cut.index, meta.content_crop if meta else None
            async with limit:
                return cut.index, await detector(meta, cut.source_in, cut.source_out)

        results = await asyncio.gather(*(detect(cut) for cut in story.timeline))
        return {index: crop for index, crop in results if crop is not None}

    async def compose(self, *, project_id: str, user_id: str, intent: IntentAnalysis, metadata: Sequence[TechnicalMetadata]) -> ContextPack:
        return await self.composer.compose(project_id=project_id, user_id=user_id, intent=intent, clips=metadata)

    async def execute(self, pack: ContextPack) -> tuple[ExecutionPlan, ExecutionReport, ExecutionContext]:
        plan = self.director.plan(pack)
        report, ctx = await self.director.execute(plan, pack)
        return plan, report, ctx

    async def run(self, *, project_id: str, user_id: str, prompt: str, clips: Sequence[ClipSource],
                  target_platform: str | None = None, edit_options: dict | None = None,
                  cached_context: ContextPack | None = None,
                  reuse_story_plan: StoryPlan | None = None,
                  revision_base_intent: IntentAnalysis | None = None,
                  manual_timeline_override: bool = False,
                  redesign_invalid_story: bool = False) -> PipelineResult:
        """End-to-end Layers 3–7 under one trace (trace_id / request_id / project_id / hashed user)."""
        with new_trace(project_id=project_id, user_id=user_id), span("pipeline.run", clips=len(clips), prompt_chars=len(prompt)) as sp:
            outcome = "error"
            try:
                result = await self._run(project_id=project_id, user_id=user_id, prompt=prompt, clips=clips,
                                         target_platform=target_platform, edit_options=edit_options or {},
                                         cached_context=cached_context, reuse_story_plan=reuse_story_plan,
                                         revision_base_intent=revision_base_intent,
                                         manual_timeline_override=manual_timeline_override,
                                         redesign_invalid_story=redesign_invalid_story)
                outcome = result.report.status if result.story else "no_story"
                sp.set(outcome=outcome, pattern=result.story.story_pattern if result.story else None)
                return result
            finally:
                m.REQUESTS.inc(outcome=outcome)

    async def _stage(self, name: str, coro):
        with bind(stage=name), span(f"stage.{name}") as sp:
            t0 = time.perf_counter()
            outcome = "ok"
            try:
                return await coro
            except BaseException as e:
                outcome = type(e).__name__
                raise
            finally:
                sp.set(outcome=outcome)
                m.STAGE_DURATION.observe(time.perf_counter() - t0, stage=name, outcome=outcome)

    async def _run(self, *, project_id: str, user_id: str, prompt: str, clips: Sequence[ClipSource],
                   target_platform: str | None = None, edit_options: dict | None = None,
                   cached_context: ContextPack | None = None,
                   reuse_story_plan: StoryPlan | None = None,
                   revision_base_intent: IntentAnalysis | None = None,
                   manual_timeline_override: bool = False,
                   redesign_invalid_story: bool = False) -> PipelineResult:
        if not clips:
            raise PipelineError("at least one clip is required")
        intent = await self._stage("interpret", self.interpret(prompt))
        if revision_base_intent is not None:
            intent = self.intent_engine.merge_revision(revision_base_intent, intent)
        intent = _apply_project_options(intent, target_platform, edit_options or {})
        cached_by_id = {meta.clip_id: meta for meta in cached_context.video_metadata} if cached_context else {}
        if cached_context and set(cached_by_id) == {clip.clip_id for clip in clips}:
            # The worker verified the immutable upload fingerprints before passing this
            # ContextPack. Keep the current stored paths while reusing source analysis.
            metadata = [revalidate(cached_by_id[clip.clip_id], path=clip.path) for clip in clips]
            failures: list[ProbeFailure] = []
            pack = await self._stage(
                "compose", self.compose(project_id=project_id, user_id=user_id, intent=intent, metadata=metadata)
            )
            pack = revalidate(pack, clip_intelligence=cached_context.clip_intelligence)
        else:
            metas, failures = await self._stage("probe", self.probe(clips))
            if not metas:
                raise PipelineError("no clip could be read: " + "; ".join(f"{f.clip_id}: {f.reason}" for f in failures))
            pack = await self._stage(
                "compose", self.compose(project_id=project_id, user_id=user_id, intent=intent, metadata=metas)
            )

        reuse_rejected = None
        if reuse_story_plan is not None and intent.creative_brief.scene_beats and not manual_timeline_override:
            # An older successful story can still contain a mixed-scene source
            # range. Replan ordered beats from cached observations and measured
            # boundaries before another render; explicit user timeline edits
            # continue through the manual-override path below.
            reuse_rejected = "ordered visual beats require source-window revalidation"
        if (reuse_story_plan is not None and pack.clip_intelligence and
                (not intent.creative_brief.scene_beats or manual_timeline_override)):
            analyzed = pack.clip_intelligence
            reconciled = self.intent_engine.reconcile_with_footage(pack.intent, list(analyzed.values()))
            pack = await self.composer.enrich_with_footage(pack, reconciled, analyzed)
            validation = self.validator.validate_plan(reuse_story_plan, pack.clip_intelligence)
            if not validation.passed and not manual_timeline_override:
                details = "; ".join(
                    violation.message for violation in validation.violations if violation.severity == "error"
                )
                reuse_rejected = f"reused story plan failed revalidation: {details or validation.summary}"
                if not redesign_invalid_story:
                    raise PipelineError(reuse_rejected)
                logger.warning("%s; rebuilding story from cached source analysis", reuse_rejected)
            else:
                plan = self.director.plan(pack)
                report = ExecutionReport(
                    plan_id=plan.plan_id,
                    status="degraded",
                    results=tuple(TaskResult(
                        task_id=task.task_id, agent=task.agent, status=TaskStatus.SKIPPED, attempts=0,
                        duration_ms=0.0,
                        reasoning=("Reused prior story only as a metadata base; the user timeline is separately validated."
                                   if manual_timeline_override else
                                   "Reused the last successful, validated story plan; only render-level settings changed."),
                    ) for task in plan.execution_plan),
                    duration_ms=0.0,
                    reasoning=("User shot overrides replace the prior story cuts after IR construction."
                               if manual_timeline_override else
                               "Clip analysis and story selection were reused; timeline, audio, and render are rebuilt."),
                )
                return PipelineResult(
                    intent=pack.intent, context_pack=pack, plan=plan, report=report,
                    story=reuse_story_plan, validation=validation, probe_failures=tuple(failures),
                )

        plan, report, ctx = await self._stage("execute", self.execute(pack))
        ok = lambda tid: ctx.statuses.get(tid) == TaskStatus.SUCCEEDED  # noqa: E731
        return PipelineResult(
            intent=ctx.pack.intent, context_pack=ctx.pack, plan=plan, report=report,
            story=ctx.results.get("design_story") if ok("design_story") else None,
            validation=ctx.results.get("validate_story") if ok("validate_story") else None,
            probe_failures=tuple(failures), story_reuse_rejected=reuse_rejected,
        )
    async def judge_story(self, plan: StoryPlan, pack: ContextPack) -> StoryJudgement:
        """Independent verdict used for best-of selection across retries."""
        with span("story.judge", judge=self.judge.judge_id):
            verdict = await self.judge.judge(plan, pack)
        m.JUDGE_SCORE.observe(verdict.overall_score, judge=self.judge.judge_id)
        return verdict

    def manual_revision_from_cached(self, cached: ContextPack, story: StoryPlan,
                                    clips: Sequence[ClipSource], target_platform: str | None,
                                    edit_options: dict) -> PipelineResult:
        """Reuse immutable upload analysis for a same-prompt manual edit.

        The saved AI story supplies metadata and text requirements; user shots
        replace its cuts after Timeline IR construction and are validated there.
        """
        paths = {clip.clip_id: clip.path for clip in clips}
        if paths.keys() != {meta.clip_id for meta in cached.video_metadata}:
            raise PipelineError("manual edit source clips changed; analyze the project again")
        intent = _apply_project_options(cached.intent, target_platform, edit_options)
        pack = revalidate(cached, intent=intent, video_metadata=tuple(
            revalidate(meta, path=paths[meta.clip_id]) for meta in cached.video_metadata
        ))
        plan = self.director.plan(pack)
        report = ExecutionReport(
            plan_id=plan.plan_id, status="degraded",
            results=tuple(TaskResult(task_id=task.task_id, agent=task.agent,
                                     status=TaskStatus.SKIPPED, attempts=0, duration_ms=0.0,
                                     reasoning="Saved analysis reused for a manual timeline revision.")
                          for task in plan.execution_plan),
            duration_ms=0.0,
            reasoning="Saved clip analysis and brief reused; user shots replace AI cuts and are validated before FFmpeg.",
        )
        return PipelineResult(intent=intent, context_pack=pack, plan=plan, report=report,
                              story=story, validation=story.validation, probe_failures=())

    async def redesign_story(self, pack: ContextPack, *, avoid_patterns: Sequence[str]) -> tuple[StoryPlan, ValidationReport]:
        """Critic retry path: rebuild the story from the same ContextPack, excluding rejected patterns."""
        plan = await self.architect.design(pack, avoid_patterns=avoid_patterns)
        return plan, self.validator.validate_plan(plan, pack.clip_intelligence)

    def generate_timeline_ir(
        self,
        story: StoryPlan,
        pack: ContextPack,
        style_override: dict | None = None,
        audio_segments: Sequence[AudioSegmentIR] | None = None,
        segment_crops: dict[int, tuple[int, int, int, int]] | None = None,
    ):
        """Translate a StoryPlan and reject invalid Timeline IR before rendering."""
        from services.timeline_generator.translator import StoryToTimelineTranslator
        from services.edit_executor.validator import EditPlanValidator

        timeline = StoryToTimelineTranslator.translate(
            story=story,
            pack=pack,
            style_override=style_override,
            audio_segments=audio_segments,
            segment_crops=segment_crops,
        )
        source_durations_ms = {meta.clip_id: round(meta.duration_s * 1000) for meta in pack.video_metadata}
        validation = EditPlanValidator().validate(timeline, source_durations_ms=source_durations_ms)
        validation.raise_if_invalid()
        return timeline

    async def build_soundtrack(
        self,
        timeline: TimelineIR,
        pack: ContextPack,
        style: str | None = None,
        enable_ducking: bool = True,
    ) -> tuple[AudioSegmentIR, ...]:
        """Generate background music and hero sound effects using AudioStudio."""
        from services.audio_studio.studio import AudioStudio
        from services.style_engine.mapper import StyleMapper

        resolved_style = StyleMapper.resolve_style_for_pack(pack)
        genre = style or resolved_style.get("music") or resolved_style.get("music_genre", "cinematic")
        studio = AudioStudio()
        return await studio.create_soundtrack(
            timeline=timeline,
            style=genre,
            enable_ducking=enable_ducking,
        )

    async def evaluate_timeline(
        self,
        timeline: TimelineIR,
        story: StoryPlan,
        pack: ContextPack,
        captions: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Multi-dimensional evaluation of the edit using CriticEvaluator."""
        from orchestration.agents.critic.evaluator import CriticEvaluator

        cuts_dicts = [
            {
                "index": i,
                "clip_id": seg.clip_id,
                "timeline_start_ms": seg.timeline_start_ms,
                "timeline_end_ms": seg.timeline_end_ms,
                "start": seg.timeline_start_ms / 1000.0,
                "end": seg.timeline_end_ms / 1000.0,
                "is_hero": seg.is_hero,
                "color": seg.color.model_dump() if seg.color else None,
            }
            for i, seg in enumerate(timeline.video_segments)
        ]
        audio_dicts = [
            {
                "track_id": trk.track_id,
                "volume": trk.volume,
                "ducking": trk.ducking,
                "loop": trk.loop,
            }
            for trk in timeline.audio_segments
        ]
        has_dialogue = any(seg.has_audio for seg in timeline.video_segments)
        evaluator = CriticEvaluator()
        return await evaluator.evaluate({
            "timeline": cuts_dicts,
            "audio_segments": audio_dicts,
            "captions": captions or [],
            "has_dialogue": has_dialogue,
            "pace": story.pace.value.value if story.pace else "medium",
            "story_plan": story.model_dump(mode="json"),
        })

    async def render_story(
        self,
        story: StoryPlan,
        pack: ContextPack,
        output_path: str,
        style_override: dict | None = None,
        on_progress=None,
        timeline: TimelineIR | None = None,
    ):
        """Compiles and executes FFmpeg render for a designed story plan."""
        from services.render_farm.renderer import MediaRenderer

        timeline = timeline or self.generate_timeline_ir(story, pack, style_override=style_override)
        renderer = MediaRenderer()
        return await renderer.render(timeline, output_path=output_path, on_progress=on_progress)



# ---------------------------------------------------------------------------- helpers and wiring


def _apply_project_options(intent: IntentAnalysis, target_platform: str | None, options: dict) -> IntentAnalysis:
    """Apply explicit project form choices after prompt inference, with visible provenance."""
    from shared.contracts.explain import Evidence, Explained
    from shared.contracts.intent import CreativeDirective
    from shared.contracts.vocab import CaptionStyle, ColorGrade, MusicStyle, Pace, Platform

    value_types = {
        "target_platform": Platform,
        "color_grade": ColorGrade,
        "caption_style": CaptionStyle,
        "music_style": MusicStyle,
        "pace": Pace,
    }
    selected = dict(options)
    if target_platform:
        selected["target_platform"] = target_platform
    field_names = {"target_platform", "color_grade", "caption_style", "music_style", "target_duration_s", "pace"}
    payload = intent.model_dump(mode="python")
    categories = {
        "target_platform": "aspect", "aspect_ratio": "aspect", "target_duration_s": "duration",
        "color_grade": "color", "brightness": "color", "contrast": "color",
        "saturation": "color", "gamma": "color", "shadow_coolness": "color",
        "highlight_warmth": "color", "vignette": "color", "scale_mode": "framing",
        "pace": "narrative", "transition": "transition", "transition_duration_ms": "transition",
        "music_style": "audio", "caption_style": "text", "title_position": "text",
        "manual_timeline": "source",
    }
    prior_directives = [item for item in intent.creative_brief.directives
                        if item.origin != "explicit_setting"]
    explicit_directives = [CreativeDirective(
        category=categories[name], priority="required",
        value="manual shot order and ranges" if name == "manual_timeline" else f"{name}={raw_value}",
        source_span=name, origin="explicit_setting",
    ) for name, raw_value in selected.items()
        if name in categories and raw_value is not None]
    payload["creative_brief"]["directives"] = tuple(prior_directives + explicit_directives)
    for name, raw_value in selected.items():
        if name not in field_names or raw_value is None:
            continue
        value = value_types[name](raw_value) if name in value_types else float(raw_value)
        payload[name] = Explained(
            value=value,
            confidence=1.0,
            reasoning="Selected explicitly in the project settings.",
            evidence=(Evidence(source="project_settings", detail=f"{name}={value}"),),
        ).model_dump(mode="python")

    overridden = set(selected) & field_names
    payload["ambiguities"] = tuple(a for a in intent.ambiguities if a.field not in overridden)
    payload["conflicts"] = tuple(c for c in intent.conflicts if not (set(c.fields) & overridden))
    payload["missing"] = tuple(m for m in intent.missing if m.field not in overridden)
    payload["overall_confidence"] = round(
        sum(payload[k]["confidence"] for k in ("genre", "emotion", "pace", "target_platform")) / 4, 3
    )
    payload["revisions"] = tuple(intent.revisions) + ("Explicit project settings applied after prompt inference.",)
    return IntentAnalysis.model_validate(payload)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def build_pipeline(
    *,
    providers: ProviderBundle | None = None,
    memory: NarrativeMemoryStore | None = None,
    preferences: PreferenceStore | None = None,
    library: PatternLibrary = DEFAULT_LIBRARY,
    on_event: EventSink | None = None,
) -> MontaPipeline:
    """Production wiring from environment variables (see docs/layers-3-7.md#configuration)."""
    providers = providers or build_providers()
    dsn = os.getenv("MONTA_MEMORY_DSN", "")
    if memory is None or preferences is None:
        if dsn:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(dsn, pool_size=int(os.getenv("MONTA_MEMORY_POOL_SIZE", "10")), pool_pre_ping=True)
            memory = memory or SqlNarrativeStore(engine)
            preferences = preferences or SqlPreferenceStore(engine)
        else:
            logger.warning("MONTA_MEMORY_DSN not set: narrative memory is process-local and will not persist")
            memory = memory or InMemoryNarrativeStore()
            preferences = preferences or InMemoryPreferenceStore()

    roots = tuple(r for r in os.getenv("MONTA_MEDIA_ROOTS", "").split(",") if r.strip())
    media = FFmpegMediaBackend(
        ffmpeg=os.getenv("FFMPEG_PATH", "ffmpeg"), ffprobe=os.getenv("FFPROBE_PATH", "ffprobe"),
        timeout_s=float(os.getenv("MONTA_MEDIA_TIMEOUT_S", "120")), allowed_roots=roots,
    )
    if not roots:
        logger.warning("MONTA_MEDIA_ROOTS not set: media inputs are not confined to storage directories")

    calibrator = None
    cal_path = os.getenv("MONTA_CALIBRATION_PATH", "")
    if cal_path:
        from shared.calibration import Calibrator

        calibrator = Calibrator.load(cal_path)

    vision_src = providers.vision_for_analysis
    vision = VisionAnalyzer(vision_src) if vision_src else None
    local_text_provider = None
    local_vision_enabled = False

    # Auto-detect local vision when no cloud vision provider is configured
    if vision is None and _env_bool("MONTA_LOCAL_VISION", default=True):
        try:
            from orchestration.agents.intelligence.local_vision import LocalVisionPipeline
            from shared.local_runtime.adapters.ollama import OllamaRuntime
            from shared.local_runtime.provider import LocalAIProvider
            from shared.local_runtime.registry import ModelRegistry
            from shared.hardware.detector import HardwareDetector

            ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            runtime = OllamaRuntime(base_url=ollama_url)
            import asyncio
            async def discover_local_models():
                try:
                    available = await runtime.is_available()
                    return available, await runtime.list_models() if available else []
                finally:
                    await runtime.aclose()

            def discover_in_private_loop():
                # The temporary discovery client must be created, used, and closed on
                # one loop. Splitting is_available/list_models across asyncio.run calls
                # binds httpx's pool to a closed loop and silently disables local vision.
                return asyncio.run(discover_local_models())

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Inside an already-running event loop (e.g. Celery worker)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    is_up, models = pool.submit(discover_in_private_loop).result(timeout=20)
            else:
                is_up, models = discover_in_private_loop()
            if is_up:
                installed_names = [m.name for m in models]
                hw = HardwareDetector.detect()

                # Find a vision model
                vision_models = [
                    n for n in installed_names
                    if ModelRegistry.get_spec(n).is_vision
                ]
                # Find a text model
                text_models = [
                    n for n in installed_names
                    if not ModelRegistry.get_spec(n).is_vision
                ]

                if vision_models and text_models:
                    try:
                        vlm_name = ModelRegistry.select_best_model("vision", hw, installed_names)
                    except ValueError:
                        vlm_name = vision_models[0]
                    try:
                        text_name = ModelRegistry.select_best_model("text", hw, installed_names)
                    except ValueError:
                        text_name = text_models[0]

                    vlm_runtime = OllamaRuntime(base_url=ollama_url)
                    text_runtime = OllamaRuntime(base_url=ollama_url)
                    vlm_provider = LocalAIProvider(runtime=vlm_runtime, model=vlm_name, vision=True)
                    text_provider = LocalAIProvider(runtime=text_runtime, model=text_name, vision=False)

                    vision = LocalVisionPipeline(vlm_provider, text_provider, max_frames=2)
                    local_text_provider = text_provider
                    local_vision_enabled = True
                    logger.info(
                        "Local vision pipeline enabled: VLM=%s, text=%s",
                        vlm_name, text_name,
                    )
                elif vision_models:
                    logger.info(
                        "Local vision model(s) found (%s) but no text model for structuring; vision disabled",
                        ", ".join(vision_models),
                    )
                else:
                    logger.debug("No local vision models installed; vision disabled")
            else:
                logger.debug("Ollama not available; local vision disabled")
        except Exception as e:
            logger.debug("Local vision auto-detection failed: %s", e)

    refiner = StoryRefiner(providers.text) if providers.text and _env_bool("MONTA_STORY_REFINER") else None

    judge: StoryJudge = HeuristicStoryJudge()
    if providers.judge is not None:
        from evaluation.story_judge import CompositeStoryJudge, LLMStoryJudge

        forbidden = [refiner.model_id] if refiner else []
        weight = float(os.getenv("MONTA_LLM_JUDGE_WEIGHT", "0.5"))
        judge = CompositeStoryJudge([(HeuristicStoryJudge(), 1 - weight),
                                     (LLMStoryJudge(providers.judge, forbidden_model_ids=forbidden), weight)])

    experiment = None
    control_share = float(os.getenv("MONTA_MEMORY_CONTROL_SHARE", "0"))
    if control_share > 0:
        from evaluation.memory_experiment import MemoryExperiment

        experiment = MemoryExperiment(control_share=control_share, salt=os.getenv("MONTA_MEMORY_EXPERIMENT_SALT", "narrative-memory-v1"))

    validator = TimelineValidator(library)
    return MontaPipeline(
        intent_engine=IntentEngine(providers.text_for_extraction or local_text_provider, calibrator=calibrator),
        composer=ContextComposer(memory=memory, preferences=preferences, learner=NarrativeLearner(memory),
                                 pattern_ids=library.ids, experiment=experiment),
        media=media,
        team=VideoIntelligenceTeam(media, vision=vision, calibrator=calibrator,
                                   keyframe_count=2 if local_vision_enabled else 4,
                                   keyframe_width=384 if local_vision_enabled else 512,
                                   max_frame_evidence=16 if local_vision_enabled else 12,
                                   frame_evidence_interval_s=1.5 if local_vision_enabled else 3.0,
                                   cache=FileIntelligenceCache(Path(os.getenv(
                                       "MONTA_INTEL_CACHE_DIR",
                                       str(Path(__file__).resolve().parents[1] / "storage" / "cache" / "intelligence"))))),
        architect=StoryArchitect(library=library, validator=validator, refiner=refiner),
        validator=validator,
        judge=judge,
        max_concurrency=int(os.getenv("MONTA_MAX_PARALLEL_CLIPS", "8")),
        on_event=on_event,
        require_local_ai=providers.is_offline and providers.text is not None,
    )
