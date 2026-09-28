"""
MONTA — Narrative Memory
==========================
Stores the outcome of every published story and turns outcomes into priors
the Story Architect uses to choose patterns.

Two interchangeable stores implement ``NarrativeMemoryStore``:

* ``InMemoryNarrativeStore`` — process-local; for single-node deployments,
  workers with a warm cache, and tests.
* ``SqlNarrativeStore`` — SQLAlchemy async (PostgreSQL in production, SQLite
  in development). Schema is managed by Alembic revision
  ``0001_narrative_memory``; ``create_schema`` exists only for local tooling.

``NarrativeLearner`` computes hierarchical, Bayesian-smoothed priors:
pattern-level history across all project types forms the prior for the
type-specific estimate, so a pattern with 3 fitness outcomes is neither
ignored nor over-trusted.
"""

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Float, Index, MetaData, String, Table, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.contracts.context import ExplicitPreferences
from shared.contracts.memory import NarrativeMemoryRecord, PatternPrior, PatternStats
from shared.contracts.vocab import Genre

SUCCESS_THRESHOLD = 0.7
TOP_ELEMENTS = 5


class NarrativeMemoryStore(Protocol):
    async def record(self, record: NarrativeMemoryRecord) -> None: ...
    async def for_user(self, user_id: str, limit: int = 50) -> list[NarrativeMemoryRecord]: ...
    async def successful(self, project_type: Genre, *, limit: int = 10, min_success: float = SUCCESS_THRESHOLD) -> list[NarrativeMemoryRecord]: ...
    async def pattern_stats(self, project_type: Genre | None) -> list[PatternStats]: ...


class PreferenceStore(Protocol):
    async def get(self, user_id: str) -> ExplicitPreferences | None: ...
    async def put(self, preferences: ExplicitPreferences) -> None: ...


def _top_elements(records: list[NarrativeMemoryRecord]) -> tuple[str, ...]:
    counts = Counter(e for r in records if r.success_index >= SUCCESS_THRESHOLD for e in r.successful_elements)
    return tuple(e for e, _ in counts.most_common(TOP_ELEMENTS))


# ---------------------------------------------------------------------------- in-memory


class InMemoryNarrativeStore:
    """Thread-unsafe but asyncio-safe store. Upserts by ``record_id``."""

    def __init__(self, records: list[NarrativeMemoryRecord] | None = None):
        self._records: dict[str, NarrativeMemoryRecord] = {r.record_id: r for r in records or []}
        self._lock = asyncio.Lock()

    async def record(self, record: NarrativeMemoryRecord) -> None:
        async with self._lock:
            self._records[record.record_id] = record

    async def for_user(self, user_id: str, limit: int = 50) -> list[NarrativeMemoryRecord]:
        rows = [r for r in self._records.values() if r.user_id == user_id]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)[:limit]

    async def successful(self, project_type: Genre, *, limit: int = 10, min_success: float = SUCCESS_THRESHOLD) -> list[NarrativeMemoryRecord]:
        rows = [r for r in self._records.values() if r.project_type == project_type and r.success_index >= min_success]
        return sorted(rows, key=lambda r: r.success_index, reverse=True)[:limit]

    async def pattern_stats(self, project_type: Genre | None) -> list[PatternStats]:
        groups: dict[str, list[NarrativeMemoryRecord]] = defaultdict(list)
        for r in self._records.values():
            if project_type is None or r.project_type == project_type:
                groups[r.story_pattern].append(r)
        return [
            PatternStats(
                story_pattern=pattern, project_type=project_type, sample_size=len(rows),
                mean_success=sum(r.success_index for r in rows) / len(rows), top_elements=_top_elements(rows),
            )
            for pattern, rows in sorted(groups.items())
        ]


class InMemoryPreferenceStore:
    def __init__(self, preferences: list[ExplicitPreferences] | None = None):
        self._prefs = {p.user_id: p for p in preferences or []}

    async def get(self, user_id: str) -> ExplicitPreferences | None:
        return self._prefs.get(user_id)

    async def put(self, preferences: ExplicitPreferences) -> None:
        self._prefs[preferences.user_id] = preferences


# ---------------------------------------------------------------------------- SQL

metadata = MetaData()

narrative_memory_table = Table(
    "narrative_memory",
    metadata,
    Column("record_id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False),
    Column("user_id", String(64), nullable=False),
    Column("project_type", String(32), nullable=False),
    Column("story_pattern", String(64), nullable=False),
    Column("pace", String(16), nullable=True),
    Column("emotion", String(32), nullable=True),
    Column("engagement_score", Float, nullable=False),
    Column("completion_rate", Float, nullable=False),
    Column("user_rating", Float, nullable=False),
    Column("success_index", Float, nullable=False),
    Column("successful_elements", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_narrative_memory_type_pattern", "project_type", "story_pattern"),
    Index("ix_narrative_memory_user_created", "user_id", "created_at"),
    Index("ix_narrative_memory_type_success", "project_type", "success_index"),
    CheckConstraint("engagement_score BETWEEN 0 AND 10", name="ck_nm_engagement_range"),
    CheckConstraint("completion_rate BETWEEN 0 AND 10", name="ck_nm_completion_range"),
    CheckConstraint("user_rating BETWEEN 0 AND 10", name="ck_nm_rating_range"),
    CheckConstraint("success_index BETWEEN 0 AND 1", name="ck_nm_success_range"),
)

user_style_preferences_table = Table(
    "user_style_preferences",
    metadata,
    Column("user_id", String(64), primary_key=True),
    Column("preferences", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _row_to_record(row) -> NarrativeMemoryRecord:
    created = row.created_at
    if created.tzinfo is None:  # SQLite drops tz info
        created = created.replace(tzinfo=timezone.utc)
    return NarrativeMemoryRecord(
        record_id=row.record_id, project_id=row.project_id, user_id=row.user_id, project_type=row.project_type,
        story_pattern=row.story_pattern, pace=row.pace, emotion=row.emotion, engagement_score=row.engagement_score,
        completion_rate=row.completion_rate, user_rating=row.user_rating,
        successful_elements=tuple(row.successful_elements or ()), created_at=created,
    )


class SqlNarrativeStore:
    """Narrative memory on any SQLAlchemy async engine."""

    ELEMENT_SCAN_LIMIT = 2000

    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def create_schema(self) -> None:
        """Local/dev only. Production schema is owned by Alembic."""
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def record(self, record: NarrativeMemoryRecord) -> None:
        t = narrative_memory_table
        values = {
            **record.model_dump(mode="python", exclude={"successful_elements"}),
            "project_type": record.project_type.value,
            "pace": record.pace.value if record.pace else None,
            "emotion": record.emotion.value if record.emotion else None,
            "success_index": record.success_index,
            "successful_elements": list(record.successful_elements),
        }
        async with self.engine.begin() as conn:
            await conn.execute(delete(t).where(t.c.record_id == record.record_id))
            await conn.execute(insert(t).values(**values))

    async def for_user(self, user_id: str, limit: int = 50) -> list[NarrativeMemoryRecord]:
        t = narrative_memory_table
        q = select(t).where(t.c.user_id == user_id).order_by(t.c.created_at.desc()).limit(limit)
        async with self.engine.connect() as conn:
            return [_row_to_record(r) for r in (await conn.execute(q)).all()]

    async def successful(self, project_type: Genre, *, limit: int = 10, min_success: float = SUCCESS_THRESHOLD) -> list[NarrativeMemoryRecord]:
        t = narrative_memory_table
        q = (
            select(t)
            .where(t.c.project_type == project_type.value, t.c.success_index >= min_success)
            .order_by(t.c.success_index.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            return [_row_to_record(r) for r in (await conn.execute(q)).all()]

    async def pattern_stats(self, project_type: Genre | None) -> list[PatternStats]:
        t = narrative_memory_table
        where = [t.c.project_type == project_type.value] if project_type else []
        agg = (
            select(t.c.story_pattern, func.count().label("n"), func.avg(t.c.success_index).label("mean"))
            .where(*where)
            .group_by(t.c.story_pattern)
            .order_by(t.c.story_pattern)
        )
        elements_q = (
            select(t.c.story_pattern, t.c.successful_elements)
            .where(*where, t.c.success_index >= SUCCESS_THRESHOLD)
            .order_by(t.c.created_at.desc())
            .limit(self.ELEMENT_SCAN_LIMIT)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(agg)).all()
            element_rows = (await conn.execute(elements_q)).all()
        counts: dict[str, Counter] = defaultdict(Counter)
        for pattern, elements in element_rows:
            counts[pattern].update(elements or ())
        return [
            PatternStats(
                story_pattern=r.story_pattern, project_type=project_type, sample_size=int(r.n),
                mean_success=float(r.mean), top_elements=tuple(e for e, _ in counts[r.story_pattern].most_common(TOP_ELEMENTS)),
            )
            for r in rows
        ]


class SqlPreferenceStore:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def get(self, user_id: str) -> ExplicitPreferences | None:
        t = user_style_preferences_table
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(t.c.preferences).where(t.c.user_id == user_id))).first()
        return ExplicitPreferences.model_validate(row.preferences) if row else None

    async def put(self, preferences: ExplicitPreferences) -> None:
        t = user_style_preferences_table
        async with self.engine.begin() as conn:
            await conn.execute(delete(t).where(t.c.user_id == preferences.user_id))
            await conn.execute(insert(t).values(
                user_id=preferences.user_id, preferences=preferences.model_dump(mode="json"),
                updated_at=datetime.now(timezone.utc),
            ))


# ---------------------------------------------------------------------------- learning


class NarrativeLearner:
    """Turns stored outcomes into ``PatternPrior``s."""

    def __init__(self, store: NarrativeMemoryStore, *, smoothing_k: float = 5.0, neutral_prior: float = 0.5):
        self.store = store
        self.k = smoothing_k
        self.neutral = neutral_prior

    async def priors(self, project_type: Genre, pattern_ids: list[str]) -> list[PatternPrior]:
        typed_stats, global_stats = await asyncio.gather(
            self.store.pattern_stats(project_type), self.store.pattern_stats(None)
        )
        typed = {s.story_pattern: s for s in typed_stats}
        overall = {s.story_pattern: s for s in global_stats}
        out = []
        for pid in pattern_ids:
            g, t = overall.get(pid), typed.get(pid)
            base = (g.sample_size * g.mean_success + self.k * self.neutral) / (g.sample_size + self.k) if g else self.neutral
            if t:
                mean = (t.sample_size * t.mean_success + self.k * base) / (t.sample_size + self.k)
                conf = t.sample_size / (t.sample_size + self.k)
                reasoning = (
                    f"{t.sample_size} {project_type.value} stories with this pattern averaged {t.mean_success:.2f} success; "
                    f"smoothed toward the cross-genre prior {base:.2f}."
                )
                elements = t.top_elements
            elif g:
                mean, conf = base, 0.5 * g.sample_size / (g.sample_size + self.k)
                reasoning = f"No {project_type.value} history; {g.sample_size} stories in other genres averaged {g.mean_success:.2f}."
                elements = g.top_elements
            else:
                mean, conf = self.neutral, 0.0
                reasoning = "No history for this pattern yet; neutral prior."
                elements = ()
            out.append(PatternPrior(
                story_pattern=pid, project_type=project_type, success_mean=round(mean, 4), confidence=round(conf, 4),
                sample_size=t.sample_size if t else 0, top_elements=elements, reasoning=reasoning,
            ))
        return out
