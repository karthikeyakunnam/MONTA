"""
MONTA API — Progress WebSocket
================================
``/ws/projects/{project_id}`` streams real stage events.

On connect the socket sends, in order:
1. ``snapshot`` — the project's current status, clip states and latest job, read from the database;
2. every recorded event from the Redis history, so a reconnect is not blank;
3. live events as the worker publishes them.

If Redis is unreachable the socket sends a ``bus_unavailable`` event and closes with
1011, so the UI can fall back to polling instead of showing a frozen screen. The
server never invents progress: percentages appear only when a producer sent one.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.dependencies import current_user_id, get_progress_bus, get_session_maker_dependency
from app.models import ClipStatus
from app.services.project_service import ProjectNotFound, ProjectService
from shared.observability import catalog as m
from shared.observability.context import bind, new_trace

logger = logging.getLogger("monta.ws")
router = APIRouter()

CLOSE_NOT_FOUND = 4404
CLOSE_INVALID_USER = 4400
CLOSE_BUS_DOWN = 1011
PING_INTERVAL_S = 20.0


async def _snapshot(project_id: str, user_id: str) -> dict:
    session_maker = get_session_maker_dependency()
    async with session_maker() as db:
        service = ProjectService(db)
        summary = await service.summary(project_id, user_id)
    return {
        "type": "snapshot",
        "project_id": project_id,
        "status": summary.project.status,
        "status_detail": summary.project.status_detail,
        "clips": [
            {"clip_id": c.id, "filename": c.original_filename, "status": c.status,
             "duration_s": c.duration, "resolution": c.resolution,
             "issues": c.rejection_issues if c.status == ClipStatus.REJECTED.value else None}
            for c in summary.clips
        ],
        "job": ({"job_id": summary.latest_job.id, "state": summary.latest_job.state,
                 "stage": summary.latest_job.stage, "error": summary.latest_job.error}
                if summary.latest_job else None),
        "story": {"pattern": (summary.project.story_plan or {}).get("story_pattern"),
                  "duration_s": (summary.project.story_plan or {}).get("total_duration_s")},
    }


@router.websocket("/ws/projects/{project_id}")
async def progress_websocket(websocket: WebSocket, project_id: str, user: str | None = Query(default=None)) -> None:
    # Accept first, then validate: a websocket cannot carry an HTTP error body, so the only
    # way to tell the client *why* it was refused is an error frame followed by a close code.
    await websocket.accept()
    try:
        user_id = await current_user_id(user or websocket.headers.get("x-monta-user"))
    except HTTPException as e:
        await websocket.send_text(json.dumps({"type": "error", "code": "invalid_user", "message": e.detail}))
        await websocket.close(code=CLOSE_INVALID_USER)
        return
    with new_trace(project_id=project_id, user_id=user_id), bind(stage="websocket"):
        try:
            snapshot = await _snapshot(project_id, user_id)
        except ProjectNotFound:
            await websocket.send_text(json.dumps({"type": "error", "code": "project_not_found",
                                                  "message": "This project does not exist."}))
            await websocket.close(code=CLOSE_NOT_FOUND)
            return
        await websocket.send_text(json.dumps(snapshot, default=str))

        bus = get_progress_bus()
        try:
            for event in await bus.history(project_id):
                await websocket.send_text(event.as_json())
        except Exception as e:
            logger.warning("progress history failed", extra={"error": str(e)})

        m.WS_CONNECTIONS.set(m.WS_CONNECTIONS.value() + 1)
        pump = asyncio.create_task(_pump(websocket, bus, project_id))
        reader = asyncio.create_task(_drain(websocket))
        try:
            done, pending = await asyncio.wait({pump, reader}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                    raise exc
        except Exception as e:
            logger.warning("progress stream ended", extra={"error": f"{type(e).__name__}: {e}"})
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({"type": "error", "code": "bus_unavailable",
                                                      "message": "Live updates are unavailable; retrying by polling."}))
                await websocket.close(code=CLOSE_BUS_DOWN)
        finally:
            m.WS_CONNECTIONS.set(max(0.0, m.WS_CONNECTIONS.value() - 1))
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()


async def _pump(websocket: WebSocket, bus, project_id: str) -> None:
    """Forward published events; heartbeat keeps proxies from idling the socket out."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    async def subscribe() -> None:
        async for payload in bus.subscribe(project_id):
            if queue.full():
                queue.get_nowait()          # drop the oldest: fresh state matters more than a full log
            await queue.put(payload)

    sub = asyncio.create_task(subscribe())
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), PING_INTERVAL_S)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                if sub.done() and sub.exception():
                    raise sub.exception()
                continue
            await websocket.send_text(payload)
    finally:
        sub.cancel()


async def _drain(websocket: WebSocket) -> None:
    """Read client frames. Any message is treated as a keepalive; disconnects end the socket."""
    while True:
        await websocket.receive_text()
