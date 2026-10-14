"""
MONTA API — Render Endpoints
==============================
Layer 13: Render Farm — Video rendering management, status, and download/playback.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import current_user_id, get_db
from app.models.project import Project
from app.models.render import Render, RenderStatus
from app.services.storage_paths import resolve_stored_mp4

router = APIRouter()


class RenderRequest(BaseModel):
    project_id: str
    resolutions: List[str] = ["1080p"]
    format: str = "mp4"


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def create_render(
    request: RenderRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
):
    """Submits a render or multi-resolution export job for a project."""
    import uuid
    from datetime import datetime, timezone

    project = await db.get(Project, request.project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    render_id = str(uuid.uuid4())
    render_row = Render(
        id=render_id,
        project_id=request.project_id,
        status=RenderStatus.QUEUED.value,
        resolution=request.resolutions[0] if request.resolutions else "1080p",
        format=request.format,
        started_at=datetime.now(timezone.utc),
    )
    db.add(render_row)
    await db.commit()

    return {
        "render_id": render_id,
        "project_id": request.project_id,
        "status": RenderStatus.QUEUED.value,
        "resolutions": request.resolutions,
        "format": request.format,
    }


@router.get("/{render_id}/download")
async def download_render(
    render_id: str,
    download: bool = Query(False, description="Return the MP4 as an attachment instead of inline preview media"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
):
    """Streams the rendered MP4 video file for playback or download."""
    render = await db.get(Render, render_id)
    if render is None or render.status != RenderStatus.COMPLETE.value:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendered output not found")
    project = await db.get(Project, render.project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendered output not found")

    p = resolve_stored_mp4(render.output_path, settings.storage_root_path)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rendered video file is missing or unavailable on storage")

    return FileResponse(
        path=str(p),
        media_type="video/mp4",
        filename=f"monta_edit_{render.project_id}.mp4" if download else None,
        content_disposition_type="attachment" if download else "inline",
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
        "available": bool(resolve_stored_mp4(render.output_path, settings.storage_root_path)),
        "duration": render.duration,
        "file_size_bytes": render.file_size_bytes,
        "download_url": f"/api/v1/render/{render.id}/download"
        if resolve_stored_mp4(render.output_path, settings.storage_root_path) else None,
    }
