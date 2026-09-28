"""
MONTA — Director Planner (Layer 5)
====================================
Builds the task DAG for Layers 6–7 from a ContextPack:

    analyze_clip:<id>  ×N  (video_intelligence, parallel, non-critical)
            │
    reconcile_intent       (intent_reconciler; runs if ≥ MIN_FOOTAGE_RATIO clips succeeded)
            │
    design_story           (story_architect, critical)
            │
    validate_story         (timeline_validator, critical — independent re-check)

Complexity, latency and model-call estimates are explained so capacity
planning and the Critic can see why a plan is expensive.
"""

import math
import uuid

from shared.contracts.context import ContextPack
from shared.contracts.director import Complexity, Dependency, ExecutionPlan, RetryPolicy, TaskKind, TaskSpec
from shared.contracts.explain import Evidence, Explained, clamp

MIN_FOOTAGE_RATIO = 0.5
ANALYZE_TIMEOUT_S = 180.0
VISION_CLIP_LATENCY_S = 12.0
SIGNAL_CLIP_LATENCY_S = 3.0
STORY_LATENCY_S = 2.0
REFINER_LATENCY_S = 10.0


class DirectorPlanner:
    def __init__(self, *, vision_enabled: bool, refiner_enabled: bool, max_parallel_clips: int = 8):
        self.vision_enabled = vision_enabled
        self.refiner_enabled = refiner_enabled
        self.max_parallel_clips = max_parallel_clips

    def plan(self, pack: ContextPack) -> ExecutionPlan:
        clips = list(pack.video_metadata)
        cached = [m.clip_id for m in clips if m.clip_id in pack.clip_intelligence]
        to_analyze = [m for m in clips if m.clip_id not in pack.clip_intelligence]

        tasks: list[TaskSpec] = []
        analyze_ids = []
        for m in to_analyze:
            tid = f"analyze_clip:{m.clip_id}"
            analyze_ids.append(tid)
            tasks.append(TaskSpec(
                task_id=tid, kind=TaskKind.ANALYZE_CLIP, agent="video_intelligence", params={"clip_id": m.clip_id},
                critical=False, timeout_s=ANALYZE_TIMEOUT_S, retry=RetryPolicy(max_attempts=2, base_delay_s=1.0),
                rationale=f"Understand {m.clip_id} ({m.duration_s:.1f}s, {m.width}x{m.height}); a single bad clip must not sink the edit.",
            ))
        tasks.append(TaskSpec(
            task_id="reconcile_intent", kind=TaskKind.RECONCILE_INTENT, agent="intent_reconciler",
            depends_on=tuple(analyze_ids), critical=True, timeout_s=10.0, retry=RetryPolicy(max_attempts=1),
            min_dependency_success_ratio=MIN_FOOTAGE_RATIO if analyze_ids else 1.0,
            rationale=("Revise low-confidence intent with what the footage shows and refresh genre-dependent context"
                       f" (genre confidence {pack.intent.genre.confidence:.2f})."),
        ))
        tasks.append(TaskSpec(
            task_id="design_story", kind=TaskKind.DESIGN_STORY, agent="story_architect", depends_on=("reconcile_intent",),
            critical=True, timeout_s=60.0, retry=RetryPolicy(max_attempts=2, base_delay_s=0.5),
            rationale="Select, adapt and fill a story pattern — the core creative decision.",
        ))
        tasks.append(TaskSpec(
            task_id="validate_story", kind=TaskKind.VALIDATE_STORY, agent="timeline_validator", depends_on=("design_story",),
            critical=True, timeout_s=10.0, retry=RetryPolicy(max_attempts=1),
            rationale="Independently re-check the timeline so the producer of a plan is never its only judge.",
        ))
        deps = tuple(
            Dependency(upstream=u, downstream=t.task_id, reason=self._reason(u, t.task_id))
            for t in tasks for u in t.depends_on
        )

        complexity = self._complexity(pack, len(to_analyze), len(cached))
        calls = (len(to_analyze) if self.vision_enabled else 0) + (1 if self.refiner_enabled else 0)
        waves = math.ceil(len(to_analyze) / self.max_parallel_clips) if to_analyze else 0
        per_clip = VISION_CLIP_LATENCY_S if self.vision_enabled else SIGNAL_CLIP_LATENCY_S
        latency = waves * per_clip + STORY_LATENCY_S + (REFINER_LATENCY_S if self.refiner_enabled else 0)

        readiness = sum(1 for m in clips if m.duration_s >= 0.5) / len(clips)
        confidence = clamp(0.5 * pack.intent.overall_confidence + 0.35 * readiness + 0.15 * (1 - len(pack.degraded_sources) / 4))
        return ExecutionPlan(
            plan_id=f"plan_{uuid.uuid4().hex[:12]}",
            execution_plan=tuple(tasks),
            dependencies=deps,
            estimated_complexity=complexity,
            confidence=round(confidence, 3),
            confidence_reasoning=(
                f"intent confidence {pack.intent.overall_confidence:.2f}, {readiness:.0%} of clips long enough to use, "
                f"{len(pack.degraded_sources)} degraded context source(s)"
                + (f" ({', '.join(pack.degraded_sources)})" if pack.degraded_sources else "")
            ),
            estimated_model_calls=calls,
            estimated_latency_s=round(latency, 1),
        )

    @staticmethod
    def _reason(upstream: str, downstream: str) -> str:
        if upstream.startswith("analyze_clip:"):
            return "intent reconciliation needs analyzed footage"
        if downstream == "design_story":
            return "story design consumes the reconciled intent and enriched ContextPack"
        return "validation needs the designed story"

    def _complexity(self, pack: ContextPack, n_analyze: int, n_cached: int) -> Explained[Complexity]:
        total_s = sum(m.duration_s for m in pack.video_metadata)
        score = n_analyze / 8 + total_s / 180 + 0.3 * len(pack.intent.ambiguities) + 0.5 * len(pack.intent.conflicts)
        level = (Complexity.LOW if score < 1 else Complexity.MEDIUM if score < 2.5
                 else Complexity.HIGH if score < 5 else Complexity.EXTREME)
        reasoning = (
            f"{n_analyze} clip(s) to analyze ({n_cached} cached), {total_s:.0f}s of footage, "
            f"{len(pack.intent.ambiguities)} ambiguity(ies), {len(pack.intent.conflicts)} conflict(s) → score {score:.2f}"
        )
        return Explained[Complexity](
            value=level, confidence=0.8, reasoning=reasoning,
            evidence=(Evidence(source="context", detail=reasoning, weight=round(score, 3)),),
        )
