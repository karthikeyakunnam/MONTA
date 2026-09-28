"""
MONTA API — Upload Endpoints (Layer 2)
========================================
    POST /upload/{project_id}          one or many files (multipart, streamed)
    GET  /upload/limits                the rules the UI enforces before sending
    DELETE /upload/{project_id}/{clip_id}   remove a clip and its bytes

Why one endpoint for one *and* many files: the browser retries a single failed
file from a multi-file selection, so the client must be able to send exactly one
file without a different code path. Each file is decided independently and the
response reports per-file results, which is what makes retry possible.

Status codes:
* 201 every file accepted
* 207 mixed — some accepted, some rejected (per-file detail in the body)
* 422 every file rejected
* 413 the request exceeded the byte ceiling
* 404 unknown project
"""

import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_user_id, get_db, get_gateway, get_limits, get_progress_bus, get_storage
from app.models import ProjectStatus
from app.schemas.project import ClipOut, UploadFileResult, UploadResponse
from app.services.progress import ProgressBus
from app.services.project_service import ProjectNotFound, ProjectService
from app.services.upload_service import UploadService
from services.media_gateway import GatewayLimits, LocalMediaStorage, MediaGateway
from shared.observability import catalog as m

router = APIRouter()
MAX_FILES_PER_REQUEST = 25


@router.get("/limits")
async def upload_limits(limits: GatewayLimits = Depends(get_limits)) -> dict:
    """The exact rules the browser should pre-check, so obvious rejects never leave the device."""
    return {
        "allowed_extensions": sorted(limits.allowed_extensions),
        "max_file_bytes": limits.max_file_bytes,
        "max_request_bytes": limits.max_request_bytes,
        "max_files_per_request": MAX_FILES_PER_REQUEST,
        "max_clip_seconds": limits.max_clip_seconds,
        "min_clip_seconds": limits.min_clip_seconds,
        "resolution_classes": [
            {"name": c.name, "max_pixels": c.max_pixels, "max_clips": c.max_clips,
             "max_total_seconds": c.max_total_seconds}
            for c in limits.classes
        ],
    }


@router.post("/{project_id}", response_model=UploadResponse)
async def upload_clips(
    project_id: str,
    request: Request,
    files: list[UploadFile] = File(..., description="One or more video files"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
    gateway: MediaGateway = Depends(get_gateway),
    limits: GatewayLimits = Depends(get_limits),
    progress: ProgressBus = Depends(get_progress_bus),
):
    """Validate, probe, store and record every file in the request."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limits.max_request_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"This upload is {int(declared) / (1024 ** 3):.1f} GB. The limit is "
                            f"{limits.max_request_bytes / (1024 ** 3):.1f} GB per request.")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"{len(files)} files in one request; the limit is {MAX_FILES_PER_REQUEST}.")

    projects = ProjectService(db, progress=progress)
    try:
        project = await projects.get_locked(project_id, user_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    service = UploadService(db, gateway, projects, progress=progress)
    results: list[UploadFileResult] = []
    started = time.perf_counter()
    for upload in files:
        outcome = await service.ingest_upload(project=project, upload=upload)
        results.append(UploadFileResult(
            filename=outcome.filename, accepted=outcome.accepted,
            clip=ClipOut.model_validate(outcome.clip) if outcome.clip else None,
            issues=outcome.issues, duplicate_of=outcome.duplicate_of,
        ))
        await upload.close()

    accepted = sum(1 for r in results if r.accepted)
    rejected = len(results) - accepted
    m.UPLOAD_DURATION.observe(time.perf_counter() - started,
                              outcome="accepted" if rejected == 0 else "rejected" if accepted == 0 else "mixed")
    body = UploadResponse(project_id=project_id, accepted=accepted, rejected=rejected, results=results,
                          project_status=ProjectStatus(project.status))
    code = status.HTTP_201_CREATED if rejected == 0 else (
        status.HTTP_422_UNPROCESSABLE_ENTITY if accepted == 0 else status.HTTP_207_MULTI_STATUS)
    return JSONResponse(status_code=code, content=body.model_dump(mode="json"))


@router.delete("/{project_id}/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clip(project_id: str, clip_id: str, db: AsyncSession = Depends(get_db),
                      user_id: str = Depends(current_user_id), gateway: MediaGateway = Depends(get_gateway),
                      storage: LocalMediaStorage = Depends(get_storage),
                      progress: ProgressBus = Depends(get_progress_bus)) -> None:
    projects = ProjectService(db, progress=progress)
    try:
        project = await projects.get(project_id, user_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    removed = await UploadService(db, gateway, projects, progress=progress).delete_clip(project, clip_id)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"clip {clip_id} not found in this project")
