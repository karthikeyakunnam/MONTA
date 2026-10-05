"""
MONTA — Progress Bus
======================
One Redis channel per project (``monta:progress:{project_id}``) carries real
stage events from the worker to every open websocket. Events are also appended
to a capped list, so a client that connects late (or reconnects) receives what
it missed instead of an empty screen.

There are no synthetic percentages here: ``percent`` is only present when the
producer actually knows a ratio (for example clips analyzed / clips total).
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import redis.asyncio as redis
from pydantic import BaseModel, ConfigDict, Field

from shared.observability import catalog as m

logger = logging.getLogger("monta.progress")


class Stage(StrEnum):
    """The states Layer 1 renders. Every event carries exactly one of them."""

    UPLOADED = "uploaded"
    VALIDATING = "validating"
    ANALYZING = "analyzing"
    STORY_BUILDING = "story_building"
    TIMELINE_BUILDING = "timeline_building"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"


class ProgressEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    stage: Stage
    message: str
    job_id: str | None = None
    clip_id: str | None = None
    percent: float | None = Field(None, ge=0, le=100, description="Only when a real ratio is known")
    detail: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def as_json(self) -> str:
        return self.model_dump_json()


class ProgressBus:
    """Publish/subscribe over Redis. Publishing never raises into the request path."""

    def __init__(self, redis_url: str, channel_prefix: str, history_prefix: str, history_length: int):
        self.redis_url = redis_url
        self.channel_prefix = channel_prefix
        self.history_prefix = history_prefix
        self.history_length = history_length
        self._client: redis.Redis | None = None

    def channel(self, project_id: str) -> str:
        return f"{self.channel_prefix}:{project_id}"

    def history_key(self, project_id: str) -> str:
        return f"{self.history_prefix}:{project_id}"

    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self.redis_url, decode_responses=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def publish(self, event: ProgressEvent) -> bool:
        """Fan an event out. Returns False when Redis is unreachable (the caller keeps working)."""
        payload = event.as_json()
        try:
            client = self.client()
            pipe = client.pipeline()
            pipe.publish(self.channel(event.project_id), payload)
            pipe.rpush(self.history_key(event.project_id), payload)
            pipe.ltrim(self.history_key(event.project_id), -self.history_length, -1)
            pipe.expire(self.history_key(event.project_id), 86400)
            await pipe.execute()
        except Exception as e:  # progress is best-effort; the DB remains the source of truth
            logger.warning("progress publish failed", extra={"project_id": event.project_id, "error": str(e)})
            return False
        m.PROGRESS_EVENTS.inc(stage=event.stage.value)
        return True

    async def history(self, project_id: str, limit: int | None = None) -> list[ProgressEvent]:
        try:
            raw = await self.client().lrange(self.history_key(project_id), -(limit or self.history_length), -1)
        except Exception as e:
            logger.warning("progress history unavailable", extra={"project_id": project_id, "error": str(e)})
            return []
        events = []
        for item in raw:
            try:
                events.append(ProgressEvent.model_validate_json(item))
            except ValueError:
                continue
        return events

    async def subscribe(self, project_id: str) -> AsyncIterator[str]:
        """Yield raw JSON payloads for a project until the caller stops iterating."""
        pubsub = self.client().pubsub()
        await pubsub.subscribe(self.channel(project_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    yield message["data"]
        finally:
            await pubsub.unsubscribe(self.channel(project_id))
            await pubsub.aclose()


def publish_sync(redis_url: str, channel_prefix: str, history_prefix: str, history_length: int,
                 event: ProgressEvent) -> bool:
    """Blocking publish for the Celery worker (no event loop there)."""
    import redis as sync_redis

    payload = event.as_json()
    try:
        client = sync_redis.from_url(redis_url, decode_responses=True)
        pipe = client.pipeline()
        pipe.publish(f"{channel_prefix}:{event.project_id}", payload)
        pipe.rpush(f"{history_prefix}:{event.project_id}", payload)
        pipe.ltrim(f"{history_prefix}:{event.project_id}", -history_length, -1)
        pipe.expire(f"{history_prefix}:{event.project_id}", 86400)
        pipe.execute()
        client.close()
    except Exception as e:
        logger.warning("worker progress publish failed", extra={"project_id": event.project_id, "error": str(e)})
        return False
    m.PROGRESS_EVENTS.inc(stage=event.stage.value)
    return True
