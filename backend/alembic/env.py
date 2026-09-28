"""
MONTA — Alembic Environment
=============================
Async migrations for the backend ORM models and the Layer 3–7 narrative
memory tables. Run from ``backend/``: ``alembic upgrade head``.

``DATABASE_URL`` (env) overrides ``sqlalchemy.url`` in alembic.ini.
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND_DIR), str(BACKEND_DIR.parent)]

from app.db.base import Base  # noqa: E402
from memory.narrative_memory import metadata as narrative_metadata  # noqa: E402
from shared.calibration.store import metadata as calibration_metadata  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = [Base.metadata, narrative_metadata, calibration_metadata]
url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
