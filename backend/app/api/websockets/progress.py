"""
MONTA API — WebSocket Progress Handler
========================================
Real-time progress updates for rendering and processing.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Set
import json

router = APIRouter()

# Active WebSocket connections per project
connections: Dict[str, Set[WebSocket]] = {}


@router.websocket("/ws/{project_id}")
async def progress_websocket(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint for real-time progress updates.
    
    Sends events like:
    - upload_progress
    - analysis_progress  
    - render_progress
    - export_complete
    """
    await websocket.accept()

    if project_id not in connections:
        connections[project_id] = set()
    connections[project_id].add(websocket)

    try:
        while True:
            # Keep connection alive, receive any client messages
            data = await websocket.receive_text()
            # TODO: Handle client commands (pause, cancel, etc.)
    except WebSocketDisconnect:
        connections[project_id].discard(websocket)
        if not connections[project_id]:
            del connections[project_id]


async def broadcast_progress(project_id: str, event: dict):
    """Broadcast a progress event to all connected clients for a project."""
    if project_id in connections:
        message = json.dumps(event)
        for ws in connections[project_id].copy():
            try:
                await ws.send_text(message)
            except Exception:
                connections[project_id].discard(ws)
