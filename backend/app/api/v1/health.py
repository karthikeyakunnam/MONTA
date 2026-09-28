"""
MONTA API — Health
====================
``GET /api/v1/health`` reports each dependency separately, so the UI can warn
"uploads are down" or "processing is paused" instead of a single opaque failure.

* database — a real ``SELECT 1``
* queue    — a broker connection attempt (bounded)
* storage  — the upload root is writable
* ffprobe  — the binary the validation engine needs is present
"""

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_gateway, get_queue, get_storage
from app.schemas.project import HealthOut
from app.services.queue import PipelineQueue
from services.media_gateway import LocalMediaStorage, MediaGateway

logger = logging.getLogger("monta.health")
router = APIRouter()


@router.get("", response_model=HealthOut)
@router.get("/", response_model=HealthOut, include_in_schema=False)
async def health(db: AsyncSession = Depends(get_db), queue: PipelineQueue = Depends(get_queue),
                 storage: LocalMediaStorage = Depends(get_storage),
                 gateway: MediaGateway = Depends(get_gateway)) -> HealthOut:
    detail: dict[str, str] = {}

    try:
        await db.execute(text("SELECT 1"))
        database = True
    except Exception as e:
        database = False
        detail["database"] = f"{type(e).__name__}: {e}"[:200]

    queue_ok = await queue.ping()
    if not queue_ok:
        detail["queue"] = ("the job queue is disabled" if not settings.QUEUE_ENABLED
                           else "the broker did not answer; uploads still work, processing is paused")

    try:
        probe = storage.uploads / ".healthcheck"
        await asyncio.to_thread(probe.write_bytes, b"ok")
        await asyncio.to_thread(probe.unlink)
        storage_ok = True
    except Exception as e:
        storage_ok = False
        detail["storage"] = f"{type(e).__name__}: {e}"[:200]

    ffprobe_ok = gateway.extractor.available()
    if not ffprobe_ok:
        detail["ffprobe"] = f"{settings.FFPROBE_PATH} not found; uploads cannot be validated"

    healthy = database and storage_ok and ffprobe_ok and queue_ok
    return HealthOut(status="ok" if healthy else "degraded", database=database, queue=queue_ok,
                     storage=storage_ok, ffprobe=ffprobe_ok, detail=detail)
