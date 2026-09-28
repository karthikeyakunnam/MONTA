"""
MONTA — Task Graph Executor (Layer 5)
=======================================
Runs an ``ExecutionPlan`` DAG:

* schedules every task whose dependencies are terminal, up to ``max_concurrency``;
* enforces per-task timeouts;
* retries retryable failures with exponential backoff per the task's policy;
* skips tasks whose dependencies failed below their tolerated success ratio;
* aborts the remaining graph when a critical task fails;
* emits ``TaskEvent``s for progress streaming and tracing.

Agents are plain async callables registered by name; the executor never
knows what they do.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.contracts.context import ContextPack
from shared.observability import catalog as m
from shared.observability.context import bind
from shared.observability.tracing import span
from shared.contracts.director import (
    ExecutionPlan,
    ExecutionReport,
    TaskError,
    TaskEvent,
    TaskResult,
    TaskSpec,
    TaskStatus,
)

logger = logging.getLogger("monta.director")


@dataclass
class ExecutionContext:
    """Mutable run state shared by agents. ``pack`` is replaced (never mutated) as agents enrich it."""

    pack: ContextPack
    plan: ExecutionPlan
    results: dict[str, Any] = field(default_factory=dict)
    statuses: dict[str, TaskStatus] = field(default_factory=dict)

    def dependency_outputs(self, task: TaskSpec) -> dict[str, Any]:
        return {d: self.results[d] for d in task.depends_on if self.statuses.get(d) == TaskStatus.SUCCEEDED}


AgentHandler = Callable[[TaskSpec, ExecutionContext], Awaitable[Any]]
EventSink = Callable[[TaskEvent], Awaitable[None] | None]
Sleep = Callable[[float], Awaitable[None]]


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, AgentHandler] = {}

    def register(self, name: str, handler: AgentHandler) -> None:
        if name in self._agents:
            raise ValueError(f"agent '{name}' already registered")
        self._agents[name] = handler

    def get(self, name: str) -> AgentHandler:
        return self._agents[name]

    def __contains__(self, name: str) -> bool:
        return name in self._agents


def _is_retryable(e: BaseException) -> bool:
    if isinstance(e, (asyncio.TimeoutError, ConnectionError)):
        return True
    return bool(getattr(e, "retryable", False))


class TaskGraphExecutor:
    def __init__(self, registry: AgentRegistry, *, max_concurrency: int = 8, sleep: Sleep = asyncio.sleep,
                 on_event: EventSink | None = None):
        self.registry = registry
        self.max_concurrency = max_concurrency
        self._sleep = sleep
        self._on_event = on_event

    async def _emit(self, plan_id: str, task_id: str, status: TaskStatus, attempt: int, message: str = "") -> None:
        if self._on_event is None:
            return
        try:
            r = self._on_event(TaskEvent(plan_id=plan_id, task_id=task_id, status=status, attempt=attempt,
                                         at=datetime.now(timezone.utc), message=message))
            if asyncio.iscoroutine(r):
                await r
        except Exception:  # observers must never break execution
            logger.exception("task event sink failed")

    async def run(self, plan: ExecutionPlan, ctx: ExecutionContext) -> ExecutionReport:
        missing = sorted({t.agent for t in plan.execution_plan if t.agent not in self.registry})
        if missing:
            raise ValueError(f"no agent registered for: {missing}")
        with bind(plan_id=plan.plan_id), span("director.execute", tasks=len(plan.execution_plan)) as sp:
            report = await self._run(plan, ctx)
            sp.set(status=report.status)
            m.PLANS.inc(status=report.status)
            return report

    async def _run(self, plan: ExecutionPlan, ctx: ExecutionContext) -> ExecutionReport:
        started = time.perf_counter()
        tasks = {t.task_id: t for t in plan.execution_plan}
        pending = dict(tasks)
        results: dict[str, TaskResult] = {}
        running: dict[asyncio.Task, str] = {}
        aborted_by: str | None = None
        for tid in tasks:
            ctx.statuses[tid] = TaskStatus.PENDING

        def terminal(tid: str) -> bool:
            return ctx.statuses[tid] in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.SKIPPED)

        while pending or running:
            if aborted_by is None:
                for tid in [t for t in pending if all(terminal(d) for d in tasks[t].depends_on)]:
                    task = pending[tid]
                    ok = sum(1 for d in task.depends_on if ctx.statuses[d] == TaskStatus.SUCCEEDED)
                    needed = task.min_dependency_success_ratio * len(task.depends_on)
                    if task.depends_on and ok < needed - 1e-9:
                        del pending[tid]
                        results[tid] = self._skip(task, f"only {ok}/{len(task.depends_on)} dependencies succeeded "
                                                        f"(needs {task.min_dependency_success_ratio:.0%})")
                        ctx.statuses[tid] = TaskStatus.SKIPPED
                        await self._emit(plan.plan_id, tid, TaskStatus.SKIPPED, 0, results[tid].reasoning)
                        if task.critical:
                            aborted_by = tid
                        continue
                    if len(running) >= self.max_concurrency:
                        break
                    del pending[tid]
                    ctx.statuses[tid] = TaskStatus.RUNNING
                    running[asyncio.create_task(self._run_task(plan.plan_id, task, ctx))] = tid
            if aborted_by is not None and pending:
                for tid, task in list(pending.items()):
                    results[tid] = self._skip(task, f"aborted: critical task '{aborted_by}' did not succeed")
                    ctx.statuses[tid] = TaskStatus.SKIPPED
                    await self._emit(plan.plan_id, tid, TaskStatus.SKIPPED, 0, results[tid].reasoning)
                pending.clear()
            if not running:
                if pending:  # unreachable for a validated DAG; guards against scheduler bugs
                    raise RuntimeError(f"scheduler stalled with pending tasks {sorted(pending)}")
                break
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for fut in done:
                tid = running.pop(fut)
                result = fut.result()
                results[tid] = result
                ctx.statuses[tid] = result.status
                if result.status == TaskStatus.FAILED and tasks[tid].critical:
                    aborted_by = tid

        ordered = tuple(results[t.task_id] for t in plan.execution_plan)
        failed = [r for r in ordered if r.status == TaskStatus.FAILED]
        skipped = [r for r in ordered if r.status == TaskStatus.SKIPPED]
        critical_bad = [r for r in failed + skipped if tasks[r.task_id].critical]
        if critical_bad:
            status = "failed"
            reasoning = f"critical task(s) did not succeed: {', '.join(r.task_id for r in critical_bad)}"
        elif failed or skipped:
            status = "degraded"
            reasoning = (f"{len(failed)} non-critical task(s) failed ({', '.join(r.task_id for r in failed)}); "
                         "the plan completed with the remaining results")
        else:
            status = "succeeded"
            reasoning = f"all {len(ordered)} tasks succeeded"
        return ExecutionReport(plan_id=plan.plan_id, status=status, results=ordered,
                               duration_ms=round((time.perf_counter() - started) * 1000, 2), reasoning=reasoning)

    @staticmethod
    def _skip(task: TaskSpec, why: str) -> TaskResult:
        return TaskResult(task_id=task.task_id, agent=task.agent, status=TaskStatus.SKIPPED, attempts=0, reasoning=why)

    async def _run_task(self, plan_id: str, task: TaskSpec, ctx: ExecutionContext) -> TaskResult:
        with bind(task_id=task.task_id, agent=task.agent), span("director.task", kind=task.kind.value) as sp:
            result = await self._run_task_inner(plan_id, task, ctx)
            sp.set(status=result.status.value, attempts=result.attempts)
            m.TASKS.inc(agent=task.agent, status=result.status.value)
            m.TASK_DURATION.observe(result.duration_ms / 1000, agent=task.agent, status=result.status.value)
            return result

    async def _run_task_inner(self, plan_id: str, task: TaskSpec, ctx: ExecutionContext) -> TaskResult:
        handler = self.registry.get(task.agent)
        started_at = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        error: TaskError | None = None
        for attempt in range(1, task.retry.max_attempts + 1):
            await self._emit(plan_id, task.task_id, TaskStatus.RUNNING, attempt)
            try:
                ctx.results[task.task_id] = await asyncio.wait_for(handler(task, ctx), task.timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                retryable = _is_retryable(e)
                msg = f"timed out after {task.timeout_s}s" if isinstance(e, asyncio.TimeoutError) else str(e)
                error = TaskError(type=type(e).__name__, message=msg[:1000], retryable=retryable)
                logger.warning("task %s attempt %d failed: %s", task.task_id, attempt, msg)
                if not retryable or attempt == task.retry.max_attempts:
                    break
                m.TASK_RETRIES.inc(agent=task.agent, error=error.type)
                await self._sleep(min(task.retry.max_delay_s, task.retry.base_delay_s * 2 ** (attempt - 1)))
                continue
            await self._emit(plan_id, task.task_id, TaskStatus.SUCCEEDED, attempt)
            return TaskResult(
                task_id=task.task_id, agent=task.agent, status=TaskStatus.SUCCEEDED, attempts=attempt,
                started_at=started_at, finished_at=datetime.now(timezone.utc),
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                reasoning=f"succeeded on attempt {attempt}",
            )
        await self._emit(plan_id, task.task_id, TaskStatus.FAILED, attempt, error.message if error else "")
        return TaskResult(
            task_id=task.task_id, agent=task.agent, status=TaskStatus.FAILED, attempts=attempt,
            started_at=started_at, finished_at=datetime.now(timezone.utc),
            duration_ms=round((time.perf_counter() - t0) * 1000, 2), error=error,
            reasoning=f"failed after {attempt} attempt(s): {error.type if error else 'unknown'}"
                      + ("" if error and error.retryable else " (not retryable)"),
        )
