"""
MONTA API — Project Endpoints
===============================
Project lifecycle and the pipeline trigger.

    POST   /projects            create (name + free-form prompt)
    GET    /projects            list, newest first
    GET    /projects/{id}       dashboard payload: clips, metadata, quota, jobs, story, render
    PATCH  /projects/{id}       rename / re-prompt (refused while a run is in flight)
    DELETE /projects/{id}       delete project rows and its stored files
    POST   /projects/{id}/pipeline   queue Layers 3–7, returns a tracking id
    GET    /projects/{id}/events     progress events already recorded (websocket replay)
"""

import mimetypes

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_user_id, get_db, get_limits, get_progress_bus, get_queue, get_storage
from app.models import ClipStatus, ProjectStatus
from app.schemas.project import (
    ClipOut,
    JobOut,
    JobSubmission,
    ProgressEventOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
    QuotaClassOut,
    EditOptions,
    RevisionCreate,
    RetryRequest,
    RenderOut,
)
from app.services.progress import ProgressBus
from app.services.project_service import ProjectConflict, ProjectNotFound, ProjectService, ProjectSummary
from app.services.storage_paths import resolve_stored_mp4
from app.services.queue import PipelineQueue, QueueUnavailable
from services.media_gateway import GatewayLimits, LocalMediaStorage

router = APIRouter()


def _service(db: AsyncSession, queue: PipelineQueue | None = None, progress: ProgressBus | None = None) -> ProjectService:
    return ProjectService(db, queue=queue, progress=progress)


def _job_out(job) -> JobOut:
    fields = {name: getattr(job, name) for name in JobOut.model_fields if hasattr(job, name)}
    result = job.result if isinstance(job.result, dict) else {}
    fields["revision_mode"] = result.get("revision_mode")
    fields["inherited_requirements"] = result.get("inherited_requirements", [])
    return JobOut.model_validate(fields)


def _detail(summary: ProjectSummary, limits: GatewayLimits) -> ProjectDetail:
    project = summary.project
    usable = summary.usable_clips
    quota = []
    for cls in limits.classes:
        same = [c for c in usable if (c.resolution_class or "1080p") == cls.name]
        quota.append(QuotaClassOut(
            resolution_class=cls.name, clips_used=len(same), max_clips=cls.max_clips,
            seconds_used=round(sum(c.duration or 0.0 for c in same), 2), max_seconds=cls.max_total_seconds,
        ))
    story = project.story_plan or {}
    judgement = project.story_judgement or {}
    preferred_render_id = judgement.get("current_render_id") if isinstance(judgement, dict) else None
    render_candidates = list(summary.renders)
    if preferred_render_id:
        render_candidates.sort(key=lambda item: item.id != preferred_render_id)
    render = None
    render_file = None
    if render_candidates:
        from app.config import settings
        for candidate in render_candidates:
            if candidate.output_path and candidate.status == "complete":
                try:
                    candidate_file = resolve_stored_mp4(candidate.output_path, settings.storage_root_path)
                except (OSError, ValueError):
                    continue
                if candidate_file.is_file():
                    render, render_file = candidate, candidate_file
                    break
    return ProjectDetail(
        id=project.id, title=project.title, description=project.description, status=ProjectStatus(project.status),
        status_detail=project.status_detail, raw_prompt=project.raw_prompt, target_platform=project.target_platform,
        edit_options=EditOptions.model_validate(project.edit_options or {}),
        prompt_version=project.prompt_version or 1,
        created_at=project.created_at, updated_at=project.updated_at, clip_count=len(usable),
        clips=[_clip_out(c) for c in summary.clips],
        jobs=[_job_out(j) for j in summary.jobs],
        quota=quota, total_duration_s=summary.total_duration_s,
        story_pattern=story.get("story_pattern"),
        story_duration_s=story.get("total_duration_s"),
        story_cuts=len(story.get("timeline") or []) or None,
        story_score=judgement.get("overall_score"),
        render_status=getattr(render, "status", None),
        render_url=f"/api/v1/render/{render.id}/download" if render_file else None,
        render_resolution=render.resolution if render_file else None,
        render_duration_s=render.duration if render_file else None,
        render_size_bytes=render_file.stat().st_size if render_file else None,
        renders=[
            RenderOut(
                id=r.id, job_id=r.job_id, status=r.status, resolution=r.resolution, format=r.format,
                platform=r.platform, duration=r.duration, file_size_bytes=int(r.file_size_bytes or 0),
                created_at=r.created_at,
                is_current=bool(render and r.id == render.id),
                preview_url=f"/api/v1/render/{r.id}/download" if r.status == "complete" and r.output_path
                and _valid_render_path(r.output_path) else None,
            )
            for r in summary.renders
        ],
    )


def _clip_out(clip) -> ClipOut:
    payload = ClipOut.model_validate(clip).model_dump(mode="python")
    payload["preview_url"] = f"/api/v1/projects/{clip.project_id}/clips/{clip.id}/preview"
    return ClipOut.model_validate(payload)


def _valid_render_path(path: str) -> bool:
    from app.config import settings
    try:
        return resolve_stored_mp4(path, settings.storage_root_path).is_file()
    except (OSError, ValueError):
        return False


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_db),
                         user_id: str = Depends(current_user_id)) -> ProjectOut:
    """Create a project from a name and a free-form editing prompt."""
    project = await _service(db).create_project(
        user_id=user_id, title=payload.title, prompt=payload.prompt,
        target_platform=payload.target_platform, description=payload.description,
        edit_options=payload.edit_options.model_dump(mode="json"),
    )
    return ProjectOut(id=project.id, title=project.title, description=project.description,
                      status=ProjectStatus(project.status), raw_prompt=project.raw_prompt,
                      target_platform=project.target_platform, edit_options=payload.edit_options,
                      created_at=project.created_at, clip_count=0)


@router.get("", response_model=list[ProjectOut])
@router.get("/", response_model=list[ProjectOut], include_in_schema=False)
async def list_projects(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db),
                        user_id: str = Depends(current_user_id)) -> list[ProjectOut]:
    rows = await _service(db).list_projects(user_id, limit)
    return [
        ProjectOut(id=p.id, title=p.title, description=p.description, status=ProjectStatus(p.status),
                   status_detail=p.status_detail, raw_prompt=p.raw_prompt, target_platform=p.target_platform,
                   edit_options=EditOptions.model_validate(p.edit_options or {}),
                   prompt_version=p.prompt_version or 1,
                   created_at=p.created_at, updated_at=p.updated_at, clip_count=n)
        for p, n in rows
    ]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db), user_id: str = Depends(current_user_id),
                      limits: GatewayLimits = Depends(get_limits)) -> ProjectDetail:
    """Everything the dashboard shows: clips with metadata, quota, jobs, story and render state."""
    try:
        summary = await _service(db).summary(project_id, user_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return _detail(summary, limits)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, payload: ProjectUpdate, db: AsyncSession = Depends(get_db),
                         user_id: str = Depends(current_user_id)) -> ProjectOut:
    try:
        project = await _service(db).update(project_id, user_id, title=payload.title, prompt=payload.prompt,
                                           target_platform=payload.target_platform,
                                           edit_options=payload.edit_options.model_dump(mode="json") if payload.edit_options else None)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ProjectConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return ProjectOut(id=project.id, title=project.title, description=project.description,
                      status=ProjectStatus(project.status), status_detail=project.status_detail,
                      raw_prompt=project.raw_prompt, target_platform=project.target_platform,
                      edit_options=EditOptions.model_validate(project.edit_options or {}),
                      prompt_version=project.prompt_version or 1,
                      created_at=project.created_at, updated_at=project.updated_at)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db), user_id: str = Depends(current_user_id),
                         storage: LocalMediaStorage = Depends(get_storage)) -> None:
    try:
        await _service(db).delete(project_id, user_id, storage=storage)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ProjectConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e


@router.post("/{project_id}/pipeline", response_model=JobSubmission, status_code=status.HTTP_202_ACCEPTED)
async def start_pipeline(project_id: str, db: AsyncSession = Depends(get_db), user_id: str = Depends(current_user_id),
                         queue: PipelineQueue = Depends(get_queue),
                         progress: ProgressBus = Depends(get_progress_bus),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JobSubmission:
    """Queue Layers 3–7 for this project and return the tracking id."""
    service = _service(db, queue=queue, progress=progress)
    try:
        job = await service.start_pipeline(project_id, user_id, idempotency_key=idempotency_key)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ProjectConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except QueueUnavailable as e:
        # The job row is persisted as submit_failed; the client may retry.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    project = await service.get(project_id, user_id)
    return JobSubmission(tracking_id=job.id, job_id=job.id, task_id=job.task_id,
                         state=job.state, project_status=ProjectStatus(project.status))


@router.post("/{project_id}/revisions", response_model=JobSubmission, status_code=status.HTTP_202_ACCEPTED)
async def create_revision(project_id: str, payload: RevisionCreate, db: AsyncSession = Depends(get_db),
                          user_id: str = Depends(current_user_id), queue: PipelineQueue = Depends(get_queue),
                          progress: ProgressBus = Depends(get_progress_bus)) -> JobSubmission:
    """Create a new prompt/options version in the same project using its stored clips."""
    service = _service(db, queue=queue, progress=progress)
    try:
        job = await service.revise(
            project_id, user_id, prompt=payload.prompt, target_platform=payload.target_platform,
            edit_options=payload.edit_options.model_dump(mode="json"), idempotency_key=payload.idempotency_key,
        )
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ProjectConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except QueueUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    project = await service.get(project_id, user_id)
    return JobSubmission(tracking_id=job.id, job_id=job.id, task_id=job.task_id,
                         state=job.state, project_status=ProjectStatus(project.status))


@router.post("/{project_id}/jobs/{job_id}/retry", response_model=JobSubmission,
             status_code=status.HTTP_202_ACCEPTED)
async def retry_revision(project_id: str, job_id: str, payload: RetryRequest,
                         db: AsyncSession = Depends(get_db), user_id: str = Depends(current_user_id),
                         queue: PipelineQueue = Depends(get_queue),
                         progress: ProgressBus = Depends(get_progress_bus)) -> JobSubmission:
    service = _service(db, queue=queue, progress=progress)
    try:
        job = await service.retry(project_id, user_id, job_id, idempotency_key=payload.idempotency_key)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ProjectConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except QueueUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    project = await service.get(project_id, user_id)
    return JobSubmission(tracking_id=job.id, job_id=job.id, task_id=job.task_id,
                         state=job.state, project_status=ProjectStatus(project.status))


@router.post("/{project_id}/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_revision(project_id: str, job_id: str, db: AsyncSession = Depends(get_db),
                          user_id: str = Depends(current_user_id), queue: PipelineQueue = Depends(get_queue),
                          progress: ProgressBus = Depends(get_progress_bus)) -> JobOut:
    try:
        job = await _service(db, queue=queue, progress=progress).cancel_queued(project_id, user_id, job_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ProjectConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return JobOut.model_validate(job)


@router.get("/{project_id}/events", response_model=list[ProgressEventOut])
async def project_events(project_id: str, limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db),
                         user_id: str = Depends(current_user_id),
                         progress: ProgressBus = Depends(get_progress_bus)) -> list[ProgressEventOut]:
    """Recorded progress events, so a page load or reconnect shows history immediately."""
    try:
        await _service(db).get(project_id, user_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    events = await progress.history(project_id, limit)
    return [ProgressEventOut(**e.model_dump()) for e in events]


@router.get("/{project_id}/clips", response_model=list[ClipOut])
async def project_clips(project_id: str, include_rejected: bool = Query(True), db: AsyncSession = Depends(get_db),
                        user_id: str = Depends(current_user_id)) -> list[ClipOut]:
    try:
        summary = await _service(db).summary(project_id, user_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    clips = summary.clips if include_rejected else [c for c in summary.clips if c.status != ClipStatus.REJECTED.value]
    return [_clip_out(c) for c in clips]


@router.get("/{project_id}/clips/{clip_id}/preview")
async def clip_preview(
    project_id: str,
    clip_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
    storage: LocalMediaStorage = Depends(get_storage),
) -> FileResponse:
    """Serve an uploaded source preview after project ownership and path checks."""
    try:
        summary = await _service(db).summary(project_id, user_id)
    except ProjectNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found") from e
    clip = next((item for item in summary.clips if item.id == clip_id), None)
    if clip is None or clip.status == ClipStatus.REJECTED.value:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip preview not found")
    try:
        path = storage.path_for(clip.storage_key)
    except (OSError, ValueError) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip preview not found") from e
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip preview file is unavailable")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=clip.original_filename)
