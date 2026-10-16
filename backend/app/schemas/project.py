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
from shared.contracts.vocab import CaptionStyle, ColorGrade, MusicStyle, Pace

PLATFORMS = ("instagram", "tiktok", "youtube_short", "youtube", "general")


class EditOptions(BaseModel):
    """Structured options that the existing story, style, audio and render layers consume."""

    target_duration_s: float | None = Field(None, gt=0, le=600, exclude_if=lambda value: value is None)
    color_grade: ColorGrade | None = Field(None, exclude_if=lambda value: value is None)
    style_preset: Literal["cinematic", "trailer", "vlog", "hype", "montage"] | None = Field(
        None, exclude_if=lambda value: value is None,
    )
    pace: Pace | None = Field(None, exclude_if=lambda value: value is None)
    transition: Literal["cut", "crossfade", "fade_black", "fade_white", "wipe_left", "wipe_right", "slide_left", "slide_right"] | None = Field(
        None, exclude_if=lambda value: value is None,
    )
    transition_duration_ms: int | None = Field(None, ge=0, le=2000, exclude_if=lambda value: value is None)
    scale_mode: Literal["fit", "fill", "stretch"] | None = Field(None, exclude_if=lambda value: value is None)
    brightness: float | None = Field(None, ge=-1, le=1, exclude_if=lambda value: value is None)
    contrast: float | None = Field(None, ge=0, le=3, exclude_if=lambda value: value is None)
    saturation: float | None = Field(None, ge=0, le=3, exclude_if=lambda value: value is None)
    gamma: float | None = Field(None, ge=0.1, le=10, exclude_if=lambda value: value is None)
    shadow_coolness: float | None = Field(None, ge=-1, le=1, exclude_if=lambda value: value is None)
    highlight_warmth: float | None = Field(None, ge=-1, le=1, exclude_if=lambda value: value is None)
    vignette: float | None = Field(None, ge=0, le=1, exclude_if=lambda value: value is None)
    caption_style: CaptionStyle | None = Field(None, exclude_if=lambda value: value is None)
    music_style: MusicStyle | None = Field(None, exclude_if=lambda value: value is None)
    add_music: bool = True
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:5"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class ProjectCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="Project name")
    prompt: str = Field(..., min_length=3, max_length=4000, description="Free-form editing instruction")
    target_platform: Literal[PLATFORMS] = "instagram"  # type: ignore[valid-type]
    edit_options: EditOptions = Field(default_factory=EditOptions)
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
    edit_options: EditOptions | None = None


class RevisionCreate(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=4000)
    target_platform: Literal[PLATFORMS] = "instagram"  # type: ignore[valid-type]
    edit_options: EditOptions = Field(default_factory=EditOptions)
    idempotency_key: str = Field(..., min_length=8, max_length=96)

    @field_validator("prompt", "idempotency_key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cannot be blank")
        return value.strip()


class RetryRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=96)

    @field_validator("idempotency_key")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cannot be blank")
        return value.strip()


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
    preview_url: str | None = None


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
    prompt_version: int | None = None
    prompt_snapshot: str | None = None
    target_platform_snapshot: str | None = None
    edit_options_snapshot: EditOptions | None = None
    retry_of_job_id: str | None = None
    revision_mode: str | None = None
    inherited_requirements: list[str] = Field(default_factory=list)
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
    edit_options: EditOptions = Field(default_factory=EditOptions)
    prompt_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    clip_count: int = 0


class StoryAct(BaseModel):
    act_id: str
    name: str
    clip_ids: list[str] = []


class RenderOut(BaseModel):
    id: str
    job_id: str | None = None
    is_current: bool = False
    status: str
    resolution: str
    format: str
    platform: str | None = None
    duration: float | None = None
    file_size_bytes: int | None = None
    created_at: datetime | None = None
    preview_url: str | None = None


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
    render_resolution: str | None = None
    render_duration_s: float | None = None
    render_size_bytes: int | None = None
    renders: list[RenderOut] = []


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
    ollama_available: bool = False
    local_ai_ready: bool = False
    ollama_models: list[str] = Field(default_factory=list)
    detail: dict[str, str] = {}
