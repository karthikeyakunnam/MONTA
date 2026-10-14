"""
MONTA API — Export Endpoints
==============================
Export rendered videos in platform-specific formats (Layer 6 & 13).
Wired to real FormatExporter and Render records.
"""

import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import current_user_id, get_db
from app.models.project import Project
from app.models.render import Render, RenderStatus
from services.render_farm.format_exporter import FormatExporter

logger = logging.getLogger("monta.api.export")

router = APIRouter()


class ExportRequest(BaseModel):
    """Export configuration."""
    render_id: str
    platform: str = "general"  # instagram, youtube_short, tiktok, twitter, square, general
    quality: str = "1080p"


@router.post("/", status_code=status.HTTP_200_OK)
async def export_video(
    request: ExportRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
):
    """Export rendered video for a specific platform.

    Formats:
    - Instagram Reel (9:16, 1080p, max 90s)
    - YouTube Short (9:16, 1080p, max 60s)
    - TikTok (9:16, 1080p, max 3min)
    - Twitter / X (16:9 or 1:1, max 140s)
    - Square (1:1, 1080p)
    - General MP4
    """
    render = await db.get(Render, request.render_id)
    if render is None or not render.output_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Master render not found")

    project = await db.get(Project, render.project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found or unauthorized")

    src_path = Path(render.output_path).resolve()
    if not src_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Master render file missing: {render.output_path}")

    # Build output path
    export_dir = settings.storage_root_path / "exports" / render.project_id
    export_dir.mkdir(parents=True, exist_ok=True)
    export_id = str(uuid.uuid4())
    out_file = export_dir / f"export_{request.platform}_{export_id}.mp4"

    exporter = FormatExporter()
    try:
        exported_path = await exporter.export_for_platform(
            input_path=str(src_path),
            output_path=str(out_file),
            platform=request.platform,
            resolution=request.quality,
        )
    except Exception as e:
        logger.exception("Format export failed: %s", e)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Export transcoding failed: {e}")

    file_size = os.path.getsize(exported_path)
    res_str = "1080x1920" if request.platform in ("instagram", "tiktok", "youtube_short") else "1920x1080"
    if request.platform == "square":
        res_str = "1080x1080"

    export_render = Render(
        id=export_id,
        project_id=render.project_id,
        status=RenderStatus.COMPLETE.value,
        resolution=res_str,
        format="mp4",
        platform=request.platform,
        output_path=str(exported_path),
        file_size_bytes=float(file_size),
        duration=render.duration,
        progress_percent=100.0,
    )
    db.add(export_render)
    await db.commit()

    return {
        "export_id": export_id,
        "render_id": request.render_id,
        "project_id": render.project_id,
        "platform": request.platform,
        "status": "complete",
        "output_path": str(exported_path),
        "file_size_bytes": file_size,
        "download_url": f"/api/v1/export/{export_id}/download",
    }


@router.get("/{export_id}/download")
async def download_export(
    export_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
):
    """Stream exported video file for playback or download."""
    render = await db.get(Render, export_id)
    if render is None or not render.output_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exported video not found")

    project = await db.get(Project, render.project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exported video not found")

    p = Path(render.output_path).resolve()
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exported video file is missing on storage")

    return FileResponse(
        path=str(p),
        media_type="video/mp4",
        filename=f"monta_export_{render.platform or 'video'}_{render.project_id}.mp4",
    )
