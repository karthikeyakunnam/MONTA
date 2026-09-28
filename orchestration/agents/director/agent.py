"""
MONTA — Director Agent (Layer 5)
==================================
The coordinator. Never edits, never renders, never makes creative calls.

It turns a ContextPack into a validated task DAG (``DirectorPlanner``),
executes it through registered specialist agents (``TaskGraphExecutor``),
and reports the outcome. Planning is deterministic on purpose: orchestration
must be reproducible, cheap and auditable — creative judgement lives in the
Intent Engine, Video Intelligence and Story Architect.
"""

from orchestration.agents.director.executor import AgentRegistry, EventSink, ExecutionContext, TaskGraphExecutor
from orchestration.agents.director.planner import DirectorPlanner
from shared.contracts.context import ContextPack
from shared.contracts.director import ExecutionPlan, ExecutionReport


class DirectorAgent:
    def __init__(self, planner: DirectorPlanner, registry: AgentRegistry, *, max_concurrency: int = 8,
                 on_event: EventSink | None = None):
        self.planner = planner
        self.executor = TaskGraphExecutor(registry, max_concurrency=max_concurrency, on_event=on_event)

    def plan(self, pack: ContextPack) -> ExecutionPlan:
        return self.planner.plan(pack)

    async def execute(self, plan: ExecutionPlan, pack: ContextPack) -> tuple[ExecutionReport, ExecutionContext]:
        ctx = ExecutionContext(pack=pack, plan=plan)
        report = await self.executor.run(plan, ctx)
        return report, ctx
