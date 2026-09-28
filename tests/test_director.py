"""Layer 5 — plan construction, DAG validation, scheduling, retries, timeouts, failure propagation."""

import asyncio

import pytest
from pydantic import ValidationError

from orchestration.agents.director import AgentRegistry, DirectorPlanner, ExecutionContext, TaskGraphExecutor
from services.context_composer.platform_rules import PLATFORM_SPECS
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from shared.contracts.context import ContextPack, UserPreferences
from shared.contracts.director import Complexity, Dependency, ExecutionPlan, RetryPolicy, TaskKind, TaskSpec, TaskStatus
from shared.contracts.explain import Explained
from shared.contracts.vocab import Platform
from shared.providers.errors import ProviderTimeoutError
from tests.conftest import gym_clips, metadata_for


def pack(n=3, analyzed=0) -> ContextPack:
    clips = gym_clips()[:n]
    return ContextPack(
        project_id="p", user_id="u", user_prompt="gym reel", intent=LexicalIntentExtractor().extract("gym reel"),
        user_preferences=UserPreferences(user_id="u"), platform=PLATFORM_SPECS[Platform.INSTAGRAM],
        video_metadata=tuple(metadata_for(c) for c in clips), clip_intelligence={c.clip_id: c for c in clips[:analyzed]},
    )


def test_planner_builds_expected_dag():
    plan = DirectorPlanner(vision_enabled=True, refiner_enabled=False).plan(pack(3))
    ids = [t.task_id for t in plan.execution_plan]
    assert ids == ["analyze_clip:c1", "analyze_clip:c2", "analyze_clip:c3", "reconcile_intent", "design_story", "validate_story"]
    reconcile = plan.task("reconcile_intent")
    assert reconcile.min_dependency_success_ratio == 0.5 and reconcile.critical
    assert not plan.task("analyze_clip:c1").critical
    assert len(plan.dependencies) == 5 and all(d.reason for d in plan.dependencies)
    assert plan.estimated_model_calls == 3
    assert plan.estimated_complexity.value in set(Complexity) and plan.estimated_complexity.reasoning
    assert 0 < plan.confidence <= 1 and plan.confidence_reasoning


def test_planner_skips_already_analyzed_clips():
    plan = DirectorPlanner(vision_enabled=False, refiner_enabled=False).plan(pack(3, analyzed=2))
    assert [t.task_id for t in plan.execution_plan if t.kind == TaskKind.ANALYZE_CLIP] == ["analyze_clip:c3"]
    assert plan.estimated_model_calls == 0


def _task(tid, deps=(), critical=True, timeout=1.0, attempts=1, ratio=1.0, agent="a"):
    return TaskSpec(task_id=tid, kind=TaskKind.DESIGN_STORY, agent=agent, depends_on=tuple(deps), critical=critical,
                    timeout_s=timeout, retry=RetryPolicy(max_attempts=attempts, base_delay_s=0), min_dependency_success_ratio=ratio,
                    rationale="test")


def _plan(tasks) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="t", execution_plan=tuple(tasks),
        dependencies=tuple(Dependency(upstream=u, downstream=t.task_id, reason="r") for t in tasks for u in t.depends_on),
        estimated_complexity=Explained[Complexity](value=Complexity.LOW, confidence=1, reasoning="r"),
        confidence=1, confidence_reasoning="r", estimated_model_calls=0, estimated_latency_s=0,
    )


def test_plan_rejects_cycles_unknown_deps_and_mismatched_edges():
    with pytest.raises(ValidationError, match="cycle"):
        _plan([_task("a", ["b"]), _task("b", ["a"])])
    with pytest.raises(ValidationError, match="unknown"):
        _plan([_task("a", ["zzz"])])
    with pytest.raises(ValidationError, match="duplicate"):
        _plan([_task("a"), _task("a")])
    good = _plan([_task("a"), _task("b", ["a"])])
    with pytest.raises(ValidationError, match="does not match"):
        ExecutionPlan.model_validate({**good.model_dump(), "dependencies": []})


async def _no_sleep(_):
    return None


def _executor(handlers: dict, **kw) -> TaskGraphExecutor:
    reg = AgentRegistry()
    for name, h in handlers.items():
        reg.register(name, h)
    return TaskGraphExecutor(reg, sleep=_no_sleep, **kw)


async def test_independent_tasks_run_in_parallel():
    running, peak = 0, 0

    async def work(task, ctx):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return task.task_id

    plan = _plan([_task(f"t{i}", critical=False) for i in range(5)] + [_task("join", [f"t{i}" for i in range(5)])])
    ctx = ExecutionContext(pack=pack(1), plan=plan)
    report = await _executor({"a": work}, max_concurrency=3).run(plan, ctx)
    assert report.status == "succeeded" and peak == 3
    assert ctx.results["join"] == "join"


async def test_retryable_failures_are_retried():
    calls = {"n": 0}

    async def flaky(task, ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderTimeoutError("slow")
        return "ok"

    plan = _plan([_task("a", attempts=3)])
    report = await _executor({"a": flaky}).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    assert report.status == "succeeded" and report.result("a").attempts == 3


async def test_non_retryable_failure_is_not_retried():
    calls = {"n": 0}

    async def broken(task, ctx):
        calls["n"] += 1
        raise ValueError("bad input")

    plan = _plan([_task("a", attempts=5)])
    report = await _executor({"a": broken}).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    assert calls["n"] == 1 and report.status == "failed"
    assert report.result("a").error.type == "ValueError" and not report.result("a").error.retryable


async def test_timeout_is_enforced():
    async def hang(task, ctx):
        await asyncio.sleep(10)

    plan = _plan([_task("a", timeout=0.05, attempts=2)])
    report = await _executor({"a": hang}).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    r = report.result("a")
    assert r.status == TaskStatus.FAILED and r.attempts == 2 and "timed out" in r.error.message


async def test_partial_failure_tolerated_then_critical_failure_aborts():
    async def ok(task, ctx):
        return 1

    async def fail(task, ctx):
        raise ValueError("clip corrupt")

    tasks = [_task("c1", critical=False), _task("c2", critical=False, agent="bad"), _task("c3", critical=False),
             _task("merge", ["c1", "c2", "c3"], ratio=0.5), _task("story", ["merge"], agent="bad"), _task("validate", ["story"])]
    plan = _plan(tasks)
    report = await _executor({"a": ok, "bad": fail}).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    assert report.result("c2").status == TaskStatus.FAILED
    assert report.result("merge").status == TaskStatus.SUCCEEDED
    assert report.result("story").status == TaskStatus.FAILED
    assert report.result("validate").status == TaskStatus.SKIPPED
    assert report.status == "failed" and "story" in report.reasoning


async def test_insufficient_dependencies_skip_task():
    async def fail(task, ctx):
        raise ValueError("x")

    async def ok(task, ctx):
        return 1

    plan = _plan([_task("c1", critical=False, agent="bad"), _task("c2", critical=False, agent="bad"),
                  _task("merge", ["c1", "c2"], ratio=0.5, agent="ok")])
    report = await _executor({"bad": fail, "ok": ok}).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    assert report.result("merge").status == TaskStatus.SKIPPED and "0/2" in report.result("merge").reasoning


async def test_degraded_when_only_noncritical_fail_and_events_emitted():
    events = []

    async def fail(task, ctx):
        raise ValueError("x")

    async def ok(task, ctx):
        return 1

    plan = _plan([_task("c1", critical=False, agent="bad"), _task("c2", critical=False, agent="ok"),
                  _task("merge", ["c1", "c2"], ratio=0.5, agent="ok")])
    report = await _executor({"bad": fail, "ok": ok}, on_event=events.append).run(plan, ExecutionContext(pack=pack(1), plan=plan))
    assert report.status == "degraded"
    assert {(e.task_id, e.status) for e in events} >= {("c1", TaskStatus.FAILED), ("merge", TaskStatus.SUCCEEDED)}


async def test_unregistered_agent_fails_fast():
    plan = _plan([_task("a", agent="ghost")])
    with pytest.raises(ValueError, match="ghost"):
        await _executor({}).run(plan, ExecutionContext(pack=pack(1), plan=plan))
