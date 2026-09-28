"""
MONTA — Database Session
==========================
The engine is created on first use, not at import, so tests (and Alembic) can
point ``DATABASE_URL`` somewhere else before anything connects. ``dispose_engine``
releases the pool on shutdown.

SQLite gets ``check_same_thread=False`` and no pool sizing; PostgreSQL gets a
bounded pool with ``pool_pre_ping`` so a recycled connection never surfaces as a
request error.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def _create_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=settings.DEBUG, connect_args={"check_same_thread": False})
    return create_async_engine(url, echo=settings.DEBUG, pool_size=20, max_overflow=10, pool_pre_ping=True)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _create_engine(settings.DATABASE_URL)
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_maker


async def dispose_engine() -> None:
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
    _engine, _session_maker = None, None
