"""
MONTA Backend — Dependency Injection
=====================================
FastAPI dependencies for database sessions, auth, etc.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# TODO: Add auth dependencies
# TODO: Add rate limiting dependencies
# TODO: Add Redis dependency
