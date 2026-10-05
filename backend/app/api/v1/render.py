"""
MONTA API — Render Endpoints
==============================
Layer 13: Render Farm — Video rendering management, status, and download/playback.
"""

import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_user_id, get_db
from app.models.project import Project
from app.models.render import Render, RenderStatus

router = APIRouter()


class RenderRequest(BaseModel):
    project_id: str
    resolutions: List[str] = ["1080p"]
    format: str = "mp4"


@router.get("/{render_id}/download")
async def download_render(
    render_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
):
    """Streams the rendered MP4 video file for playback or download."""
    render = await db.get(Render, render_id)
    if render is None or not render.output_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendered output not found")
    project = await db.get(Project, render.project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendered output not found")

    p = Path(render.output_path).resolve()
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendered video file is missing on storage")

    return FileResponse(
        path=str(p),
        media_type="video/mp4",
        filename=f"monta_edit_{render.project_id}.mp4",
    )


@router.get("/{render_id}")
async def get_render_detail(
    render_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
):
    render = await db.get(Render, render_id)
    if render is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Render not found")
    project = await db.get(Project, render.project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Render not found")
    return {
        "id": render.id,
        "project_id": render.project_id,
        "status": render.status,
        "resolution": render.resolution,
        "format": render.format,
        "output_path": render.output_path,
        "file_size_bytes": render.file_size_bytes,
        "duration": render.duration,
        "download_url": f"/api/v1/render/{render.id}/download" if render.output_path else None,
    }
