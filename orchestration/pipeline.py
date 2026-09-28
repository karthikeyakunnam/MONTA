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
from collections.abc import Sequence
from dataclasses import dataclass

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
from orchestration.agents.intelligence import FFmpegMediaBackend, LRUIntelligenceCache, MediaBackend, MediaToolError, VideoIntelligenceTeam, VisionAnalyzer
from evaluation.story_judge import HeuristicStoryJudge, StoryJudge
from orchestration.agents.story import DEFAULT_LIBRARY, PatternLibrary, StoryArchitect, StoryRefiner, TimelineValidator
from services.context_composer import ContextComposer
from services.prompt_engine import IntentEngine
from shared.contracts.clip import ClipIntelligence, ClipSource, TechnicalMetadata
from shared.contracts.context import ContextPack
from shared.contracts.director import ExecutionPlan, ExecutionReport, TaskSpec, TaskStatus
from shared.contracts.evaluation import StoryJudgement
from shared.contracts.intent import IntentAnalysis
from shared.contracts.story import StoryPlan, ValidationReport
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
    ):
        self.intent_engine = intent_engine
        self.composer = composer
        self.media = media
        self.team = team
        self.architect = architect
        self.validator = validator
        self.judge = judge or HeuristicStoryJudge()
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
        return await self.architect.design(ctx.pack, avoid_patterns=task.params.get("avoid_patterns", ()))

    async def _validate_story(self, task: TaskSpec, ctx: ExecutionContext) -> ValidationReport:
        return self.validator.validate_plan(ctx.results["design_story"], ctx.pack.clip_intelligence)

    # ------------------------------------------------------------------ stages

    async def interpret(self, prompt: str) -> IntentAnalysis:
        return await self.intent_engine.analyze(prompt)

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

    async def compose(self, *, project_id: str, user_id: str, intent: IntentAnalysis, metadata: Sequence[TechnicalMetadata]) -> ContextPack:
        return await self.composer.compose(project_id=project_id, user_id=user_id, intent=intent, clips=metadata)

    async def execute(self, pack: ContextPack) -> tuple[ExecutionPlan, ExecutionReport, ExecutionContext]:
        plan = self.director.plan(pack)
        report, ctx = await self.director.execute(plan, pack)
        return plan, report, ctx

    async def run(self, *, project_id: str, user_id: str, prompt: str, clips: Sequence[ClipSource]) -> PipelineResult:
        """End-to-end Layers 3–7 under one trace (trace_id / request_id / project_id / hashed user)."""
        with new_trace(project_id=project_id, user_id=user_id), span("pipeline.run", clips=len(clips), prompt_chars=len(prompt)) as sp:
            outcome = "error"
            try:
                result = await self._run(project_id=project_id, user_id=user_id, prompt=prompt, clips=clips)
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

    async def _run(self, *, project_id: str, user_id: str, prompt: str, clips: Sequence[ClipSource]) -> PipelineResult:
        if not clips:
            raise PipelineError("at least one clip is required")
        intent, (metas, failures) = await asyncio.gather(self._stage("interpret", self.interpret(prompt)),
                                                         self._stage("probe", self.probe(clips)))
        if not metas:
            raise PipelineError("no clip could be read: " + "; ".join(f"{f.clip_id}: {f.reason}" for f in failures))
        pack = await self._stage("compose", self.compose(project_id=project_id, user_id=user_id, intent=intent, metadata=metas))
        plan, report, ctx = await self._stage("execute", self.execute(pack))
        ok = lambda tid: ctx.statuses.get(tid) == TaskStatus.SUCCEEDED  # noqa: E731
        return PipelineResult(
            intent=ctx.pack.intent, context_pack=ctx.pack, plan=plan, report=report,
            story=ctx.results.get("design_story") if ok("design_story") else None,
            validation=ctx.results.get("validate_story") if ok("validate_story") else None,
            probe_failures=tuple(failures),
        )

    async def judge_story(self, plan: StoryPlan, pack: ContextPack) -> StoryJudgement:
        """Independent verdict used for best-of selection across retries."""
        with span("story.judge", judge=self.judge.judge_id):
            verdict = await self.judge.judge(plan, pack)
        m.JUDGE_SCORE.observe(verdict.overall_score, judge=self.judge.judge_id)
        return verdict

    async def redesign_story(self, pack: ContextPack, *, avoid_patterns: Sequence[str]) -> tuple[StoryPlan, ValidationReport]:
        """Critic retry path: rebuild the story from the same ContextPack, excluding rejected patterns."""
        plan = await self.architect.design(pack, avoid_patterns=avoid_patterns)
        return plan, self.validator.validate_plan(plan, pack.clip_intelligence)


# ---------------------------------------------------------------------------- wiring


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


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
        intent_engine=IntentEngine(providers.text_for_extraction, calibrator=calibrator),
        composer=ContextComposer(memory=memory, preferences=preferences, learner=NarrativeLearner(memory),
                                 pattern_ids=library.ids, experiment=experiment),
        media=media,
        team=VideoIntelligenceTeam(media, vision=vision, calibrator=calibrator,
                                   cache=LRUIntelligenceCache(int(os.getenv("MONTA_INTEL_CACHE_SIZE", "10000")))),
        architect=StoryArchitect(library=library, validator=validator, refiner=refiner),
        validator=validator,
        judge=judge,
        max_concurrency=int(os.getenv("MONTA_MAX_PARALLEL_CLIPS", "8")),
        on_event=on_event,
    )
