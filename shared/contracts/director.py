"""
MONTA — Director Contracts (Layer 5)
======================================
Execution plans are DAGs of typed tasks. The plan is validated on construction
(unique ids, known dependencies, acyclic), so an invalid plan can never reach
the executor.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.base import FREEZE, NonEmptyText, Text
from shared.contracts.explain import Explained

DIRECTOR_SCHEMA_VERSION = "director.v1"


class TaskKind(StrEnum):
    ANALYZE_CLIP = "analyze_clip"
    RECONCILE_INTENT = "reconcile_intent"
    DESIGN_STORY = "design_story"
    VALIDATE_STORY = "validate_story"


class Complexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(3, ge=1, le=10)
    base_delay_s: float = Field(0.5, ge=0)
    max_delay_s: float = Field(8.0, ge=0)


class TaskSpec(BaseModel):
    """One unit of work, assigned to exactly one specialized agent."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., min_length=1, max_length=160)
    kind: TaskKind
    agent: str = Field(..., min_length=1)
    depends_on: tuple[str, ...] = ()
    params: Annotated[dict[str, Any], FREEZE] = Field(default_factory=dict)
    critical: bool = Field(..., description="A failed critical task fails the whole plan")
    timeout_s: float = Field(..., gt=0)
    retry: RetryPolicy = RetryPolicy()
    min_dependency_success_ratio: float = Field(
        1.0, ge=0, le=1,
        description="Fraction of dependencies that must succeed for this task to run (tolerates partial footage failure)",
    )
    rationale: str = Field(..., min_length=1)


class Dependency(BaseModel):
    model_config = ConfigDict(frozen=True)

    upstream: str
    downstream: str
    reason: str


class ExecutionPlan(BaseModel):
    """Layer 5 output: the task graph plus the Director's assessment of it."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["director.v1"] = DIRECTOR_SCHEMA_VERSION
    plan_id: str
    execution_plan: tuple[TaskSpec, ...] = Field(..., min_length=1)
    dependencies: tuple[Dependency, ...]
    estimated_complexity: Explained[Complexity]
    confidence: float = Field(..., ge=0, le=1)
    confidence_reasoning: str = Field(..., min_length=1)
    estimated_model_calls: int = Field(..., ge=0)
    estimated_latency_s: float = Field(..., ge=0)

    @model_validator(mode="after")
    def _valid_dag(self):
        ids = [t.task_id for t in self.execution_plan]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task ids in execution plan")
        known = set(ids)
        for t in self.execution_plan:
            missing = set(t.depends_on) - known
            if missing:
                raise ValueError(f"task {t.task_id} depends on unknown tasks {sorted(missing)}")
        edges = {(d.upstream, d.downstream) for d in self.dependencies}
        declared = {(u, t.task_id) for t in self.execution_plan for u in t.depends_on}
        if edges != declared:
            raise ValueError("dependencies list does not match task depends_on declarations")
        topological_order(self.execution_plan)
        return self

    def task(self, task_id: str) -> TaskSpec:
        return next(t for t in self.execution_plan if t.task_id == task_id)


def topological_order(tasks: tuple[TaskSpec, ...] | list[TaskSpec]) -> list[str]:
    """Kahn's algorithm. Raises ValueError on cycles. Stable w.r.t. input order."""
    indegree = {t.task_id: len(t.depends_on) for t in tasks}
    children: dict[str, list[str]] = {t.task_id: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            children[dep].append(t.task_id)
    ready = [t.task_id for t in tasks if indegree[t.task_id] == 0]
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(order) != len(indegree):
        cyclic = sorted(k for k, v in indegree.items() if v > 0)
        raise ValueError(f"execution plan contains a cycle among {cyclic}")
    return order


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskError(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    message: Text
    retryable: bool


class TaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    agent: str
    status: TaskStatus
    attempts: int = Field(..., ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = Field(0.0, ge=0)
    error: TaskError | None = None
    reasoning: str = Field(..., min_length=1)


class TaskEvent(BaseModel):
    """Progress event emitted by the executor (for websockets / tracing)."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    task_id: str
    status: TaskStatus
    attempt: int
    at: datetime
    message: str = ""


class ExecutionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str
    status: Literal["succeeded", "degraded", "failed"]
    results: tuple[TaskResult, ...]
    duration_ms: float = Field(..., ge=0)
    reasoning: str = Field(..., min_length=1)

    def result(self, task_id: str) -> TaskResult:
        return next(r for r in self.results if r.task_id == task_id)
