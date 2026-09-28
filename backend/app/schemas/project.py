"""
MONTA — Project & Clip Schemas
================================
The API contract Layer 1 renders. Field names match the UI's vocabulary, and
every rejection carries its machine code plus the sentence shown to the creator.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ClipStatus, JobState, ProjectStatus

PLATFORMS = ("instagram", "tiktok", "youtube_short", "youtube", "general")


class ProjectCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="Project name")
    prompt: str = Field(..., min_length=3, max_length=4000, description="Free-form editing instruction")
    target_platform: Literal[PLATFORMS] = "instagram"  # type: ignore[valid-type]
    description: str | None = Field(None, max_length=2000)

    @field_validator("title", "prompt")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cannot be blank")
        return v.strip()


class ProjectUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    prompt: str | None = Field(None, min_length=3, max_length=4000)
    target_platform: Literal[PLATFORMS] | None = None  # type: ignore[valid-type]


class ValidationIssueOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    detail: dict[str, Any] = {}


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: ClipStatus
    filename: str
    original_filename: str
    format: str
    duration: float | None = None
    fps: float | None = None
    resolution: str | None = None
    resolution_class: str | None = None
    video_codec: str | None = None
    bitrate: int | None = None
    file_size_bytes: int
    has_audio: bool
    audio_streams: list[dict[str, Any]] | None = None
    sha256: str
    duplicate_of: str | None = None
    rejection_issues: list[ValidationIssueOut] | None = None
    created_at: datetime | None = None


class UploadFileResult(BaseModel):
    """Per-file outcome; a 207 response carries one of these for every file in the request."""

    filename: str
    accepted: bool
    clip: ClipOut | None = None
    issues: list[ValidationIssueOut] | None = None
    duplicate_of: str | None = None


class UploadResponse(BaseModel):
    project_id: str
    accepted: int
    rejected: int
    results: list[UploadFileResult]
    project_status: ProjectStatus


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    state: JobState
    stage: str | None = None
    task_id: str | None = None
    attempts: int
    error: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class QuotaClassOut(BaseModel):
    resolution_class: str
    clips_used: int
    max_clips: int
    seconds_used: float
    max_seconds: float


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str | None = None
    status: ProjectStatus
    status_detail: str | None = None
    raw_prompt: str | None = None
    target_platform: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    clip_count: int = 0


class StoryAct(BaseModel):
    act_id: str
    name: str
    clip_ids: list[str] = []


class ProjectDetail(ProjectOut):
    clips: list[ClipOut] = []
    jobs: list[JobOut] = []
    quota: list[QuotaClassOut] = []
    total_duration_s: float = 0
    story_pattern: str | None = None
    story_duration_s: float | None = None
    story_cuts: int | None = None
    story_score: float | None = None
    render_status: str | None = None
    render_url: str | None = None


class ProgressEventOut(BaseModel):
    project_id: str
    stage: str
    message: str
    job_id: str | None = None
    clip_id: str | None = None
    percent: float | None = None
    detail: dict[str, Any] = {}
    at: datetime


class JobSubmission(BaseModel):
    """Returned by POST /projects/{id}/pipeline — ``tracking_id`` is what the UI polls or watches."""

    tracking_id: str
    job_id: str
    task_id: str | None
    state: JobState
    project_status: ProjectStatus


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    queue: bool
    storage: bool
    ffprobe: bool
    detail: dict[str, str] = {}
