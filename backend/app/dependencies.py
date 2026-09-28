"""
MONTA Backend — Dependency Injection
=====================================
Request-scoped database sessions, the caller's identity, and the singletons that
own sockets or subprocess handles (media gateway, queue, progress bus).

The gateway, queue and progress bus are created once per process and reused: each
holds a connection pool or a storage handle that must not be rebuilt per request.
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session_maker
from app.services.progress import ProgressBus
from app.services.queue import PipelineQueue
from services.media_gateway import DEFAULT_LIMITS, GatewayLimits, LocalMediaStorage, MediaGateway, MetadataExtractor


def get_session_maker_dependency():
    """Session factory for contexts without a request scope (websockets, startup checks)."""
    return get_session_maker()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """One transaction per request: commit on success, roll back on any exception."""
    async with get_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@lru_cache(maxsize=1)
def get_limits() -> GatewayLimits:
    """Ingest policy, with the deployment's overrides from settings applied."""
    from services.media_gateway.config import ResolutionClass

    hd = ResolutionClass(name="1080p", max_pixels=1920 * 1080, max_clips=settings.MAX_CLIPS_1080P,
                         max_total_seconds=settings.MAX_MINUTES_1080P * 60)
    uhd = ResolutionClass(name="4k", max_pixels=3840 * 2160, max_clips=settings.MAX_CLIPS_4K,
                          max_total_seconds=settings.MAX_MINUTES_4K * 60)
    extensions = frozenset(e.strip().lower() for e in settings.SUPPORTED_FORMATS.split(",") if e.strip())
    return GatewayLimits(
        allowed_extensions=extensions or DEFAULT_LIMITS.allowed_extensions,
        max_file_bytes=settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
        max_request_bytes=settings.MAX_REQUEST_SIZE_MB * 1024 * 1024,
        max_clip_seconds=float(settings.MAX_CLIP_DURATION_SECONDS),
        classes=(hd, uhd),
    )


@lru_cache(maxsize=1)
def get_storage() -> LocalMediaStorage:
    return LocalMediaStorage(settings.storage_root_path, get_limits())


@lru_cache(maxsize=1)
def get_gateway() -> MediaGateway:
    from services.media_gateway.metadata import DEFAULT_PROBE_CONCURRENCY

    extractor = MetadataExtractor(
        settings.FFPROBE_PATH, settings.PROBE_TIMEOUT_SECONDS,
        max_concurrency=settings.PROBE_MAX_CONCURRENCY or DEFAULT_PROBE_CONCURRENCY,
    )
    return MediaGateway(get_storage(), extractor, get_limits())


@lru_cache(maxsize=1)
def get_queue() -> PipelineQueue:
    return PipelineQueue(
        broker_url=settings.CELERY_BROKER_URL, result_backend=settings.CELERY_RESULT_BACKEND,
        task_name=settings.PIPELINE_TASK_NAME, timeout_s=settings.QUEUE_SUBMIT_TIMEOUT_SECONDS,
        enabled=settings.QUEUE_ENABLED,
    )


@lru_cache(maxsize=1)
def get_progress_bus() -> ProgressBus:
    return ProgressBus(settings.REDIS_URL, settings.PROGRESS_CHANNEL_PREFIX,
                       settings.PROGRESS_HISTORY_KEY_PREFIX, settings.PROGRESS_HISTORY_LENGTH)


async def current_user_id(x_monta_user: str | None = Header(default=None)) -> str:
    """Caller identity. Until auth lands this is a header with a configured default.

    A malformed header is the *client's* mistake, so it must surface as a 400. Letting the
    underlying ``ValueError`` escape a dependency would render it as a 500 and page an
    on-call engineer for what is really a bad request.
    """
    from services.media_gateway import validate_identifier

    if not x_monta_user:
        return settings.DEFAULT_USER_ID
    try:
        return validate_identifier(x_monta_user.strip(), "user id")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "X-MONTA-User must be 1-64 characters of letters, digits, '_' or '-'.") from e


DbSession = Depends(get_db)
UserId = Depends(current_user_id)
