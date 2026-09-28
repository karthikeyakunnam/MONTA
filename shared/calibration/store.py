"""
MONTA — Calibration Store
===========================
Append-only decision and outcome logs. The SQL implementation uses tables
``decision_log`` / ``outcome_log`` (Alembic revision ``0002_calibration``).
"""

import asyncio
from collections import defaultdict
from datetime import timezone
from typing import Protocol

from sqlalchemy import Boolean, Column, DateTime, Float, Index, MetaData, String, Table, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.calibration.records import DecisionRecord, LabeledDecision, OutcomeRecord


class CalibrationStore(Protocol):
    async def add_decisions(self, decisions: list[DecisionRecord]) -> None: ...
    async def add_outcomes(self, outcomes: list[OutcomeRecord]) -> None: ...
    async def labeled(self, component: str | None = None, field: str | None = None) -> list[LabeledDecision]: ...


class InMemoryCalibrationStore:
    def __init__(self):
        self.decisions: dict[str, DecisionRecord] = {}
        self.outcomes: dict[str, OutcomeRecord] = {}
        self._lock = asyncio.Lock()

    async def add_decisions(self, decisions: list[DecisionRecord]) -> None:
        async with self._lock:
            for d in decisions:
                self.decisions[d.decision_id] = d

    async def add_outcomes(self, outcomes: list[OutcomeRecord]) -> None:
        async with self._lock:
            for o in outcomes:
                prev = self.outcomes.get(o.decision_id)
                if prev is None or o.created_at >= prev.created_at:
                    self.outcomes[o.decision_id] = o

    async def labeled(self, component: str | None = None, field: str | None = None) -> list[LabeledDecision]:
        out = []
        for did, o in self.outcomes.items():
            d = self.decisions.get(did)
            if d and (component is None or d.component == component) and (field is None or d.field == field):
                out.append(LabeledDecision(decision=d, outcome=o))
        return out


metadata = MetaData()

decision_log = Table(
    "decision_log", metadata,
    Column("decision_id", String(32), primary_key=True),
    Column("component", String(64), nullable=False),
    Column("field", String(64), nullable=False),
    Column("model_id", String(128), nullable=True),
    Column("predicted_value", String(256), nullable=False),
    Column("raw_confidence", Float, nullable=False),
    Column("calibrated_confidence", Float, nullable=False),
    Column("versions", String(512), nullable=False),
    Column("project_id", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_decision_log_component_field", "component", "field", "created_at"),
)

outcome_log = Table(
    "outcome_log", metadata,
    Column("id", String(64), primary_key=True),
    Column("decision_id", String(32), nullable=False),
    Column("correct", Boolean, nullable=False),
    Column("source", String(32), nullable=False),
    Column("observed_value", String(256), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_outcome_log_decision", "decision_id", "created_at"),
)


def _tz(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SqlCalibrationStore:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def create_schema(self) -> None:
        """Local/dev only; production schema is owned by Alembic."""
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def add_decisions(self, decisions: list[DecisionRecord]) -> None:
        if not decisions:
            return
        async with self.engine.begin() as conn:
            await conn.execute(insert(decision_log), [d.model_dump() for d in decisions])

    async def add_outcomes(self, outcomes: list[OutcomeRecord]) -> None:
        if not outcomes:
            return
        rows = [{**o.model_dump(), "id": f"{o.decision_id}:{o.created_at.timestamp():.6f}"} for o in outcomes]
        async with self.engine.begin() as conn:
            await conn.execute(insert(outcome_log), rows)

    async def labeled(self, component: str | None = None, field: str | None = None) -> list[LabeledDecision]:
        d, o = decision_log, outcome_log
        q = select(d, o.c.correct, o.c.source, o.c.observed_value, o.c.created_at.label("outcome_at")).join(
            o, o.c.decision_id == d.c.decision_id)
        if component:
            q = q.where(d.c.component == component)
        if field:
            q = q.where(d.c.field == field)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).all()
        latest: dict[str, tuple] = {}
        for r in rows:
            if r.decision_id not in latest or _tz(r.outcome_at) >= _tz(latest[r.decision_id].outcome_at):
                latest[r.decision_id] = r
        out = []
        for r in latest.values():
            dec = DecisionRecord(
                decision_id=r.decision_id, component=r.component, field=r.field, model_id=r.model_id,
                predicted_value=r.predicted_value, raw_confidence=r.raw_confidence,
                calibrated_confidence=r.calibrated_confidence, versions=r.versions, project_id=r.project_id,
                created_at=_tz(r.created_at),
            )
            out.append(LabeledDecision(decision=dec, outcome=OutcomeRecord(
                decision_id=r.decision_id, correct=r.correct, source=r.source, observed_value=r.observed_value,
                created_at=_tz(r.outcome_at))))
        return out


def group_by_field(labeled: list[LabeledDecision]) -> dict[tuple[str, str], list[LabeledDecision]]:
    groups: dict[tuple[str, str], list[LabeledDecision]] = defaultdict(list)
    for x in labeled:
        groups[(x.decision.component, x.decision.field)].append(x)
    return dict(groups)
