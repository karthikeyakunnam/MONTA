"""
MONTA — Clip Contracts (Layer 2 metadata, Layer 6 intelligence)
=================================================================
``TechnicalMetadata`` is what ffprobe can prove. ``SignalMetrics`` is what pixel
statistics can measure. ``VisionObservation`` is what a multimodal model
believes. ``ClipIntelligence`` is the fused, explainable result consumed by the
Story Architect.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.arbitration import ArbitrationSummary
from shared.contracts.base import NonEmptyText, Text
from shared.contracts.vocab import (
    CameraMotion,
    CameraType,
    Emotion,
    Lighting,
    ShotType,
    StoryRole,
)

CLIP_SCHEMA_VERSION = "clip.v1"

ShortText = Annotated[str, Field(max_length=200)]
Labels = Annotated[tuple[ShortText, ...], Field(max_length=32)]


class ClipSource(BaseModel):
    """A creator-uploaded clip as handed over by the Media Gateway."""

    model_config = ConfigDict(frozen=True)

    clip_id: str = Field(..., min_length=1, max_length=128)
    path: str = Field(..., min_length=1)


class TechnicalMetadata(BaseModel):
    """Container-level facts from ffprobe."""

    model_config = ConfigDict(frozen=True)

    clip_id: str
    path: str
    duration_s: float = Field(..., gt=0)
    fps: float = Field(..., gt=0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    codec: str
    has_audio: bool
    rotation: int = 0
    file_size_bytes: int = Field(..., ge=0)
    fingerprint: str = Field(..., description="Content hash used as the analysis cache key")

    @property
    def is_vertical(self) -> bool:
        w, h = (self.height, self.width) if self.rotation in (90, 270, -90) else (self.width, self.height)
        return h > w


class SignalMetrics(BaseModel):
    """Deterministic pixel measurements. Reproducible, cheap, model-free."""

    model_config = ConfigDict(frozen=True)

    signal_version: str
    sample_fps: float = Field(..., gt=0)
    frames_analyzed: int = Field(..., ge=0)
    brightness_mean: float = Field(..., ge=0, le=1)
    contrast: float = Field(..., ge=0)
    clipped_ratio: float = Field(..., ge=0, le=1)
    sharpness: float = Field(..., ge=0, description="Median variance of Laplacian on [0,1] luma")
    motion_mean: float = Field(..., ge=0, description="Mean absolute frame difference")
    motion_std: float = Field(..., ge=0)
    camera_speed: float = Field(..., ge=0, description="Global motion in frame-widths per second")
    camera_jitter: float = Field(..., ge=0, description="Frame-to-frame change of global motion, widths per second")
    direction_consistency: float = Field(..., ge=0, le=1)
    dominant_axis: str = Field(..., pattern="^(horizontal|vertical|none)$")
    cut_count: int = Field(..., ge=0)
    peak_time_s: float = Field(..., ge=0)
    energy_curve: tuple[float, ...] = Field((), description="Normalized motion over time, at most 24 points")


class RoleCandidate(BaseModel):
    """A narrative role a clip could play, with confidence."""

    model_config = ConfigDict(frozen=True)

    role: StoryRole
    confidence: float = Field(..., ge=0, le=1)


class VisionObservation(BaseModel):
    """Semantic understanding returned by a multimodal provider (validated at the provider boundary)."""

    model_config = ConfigDict(frozen=True)

    activities: Labels = ()
    objects: Labels = ()
    people: Labels = Field((), description="One short description per distinct person")
    camera_type: CameraType = CameraType.UNKNOWN
    shot_type: ShotType = ShotType.UNKNOWN
    scene_type: str = Field("", max_length=64)
    emotion: Emotion = Emotion.NEUTRAL
    emotion_confidence: float = Field(0.0, ge=0, le=1)
    energy_estimate: float = Field(..., ge=0, le=10)
    story_role_candidates: tuple[RoleCandidate, ...] = ()
    visual_tags: Labels = ()
    quality_issues: Labels = Field((), description="e.g. 'motion blur', 'out of focus', 'watermark'")
    reasoning: NonEmptyText


class AnalysisProvenance(BaseModel):
    """Which analyzers produced a ClipIntelligence and what was unavailable."""

    model_config = ConfigDict(frozen=True)

    signal_version: str
    vision_model: str | None
    analyzed_at: datetime
    cache_hit: bool = False
    degraded: tuple[Text, ...] = ()
    arbitration: ArbitrationSummary | None = None


class ClipIntelligence(BaseModel):
    """Layer 6 output for one clip — the fused, explainable view the Story Architect consumes."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = CLIP_SCHEMA_VERSION
    clip_id: str
    duration_s: float = Field(..., gt=0)

    activities: Labels = ()
    objects: Labels = ()
    people: Labels = ()
    camera_type: CameraType = CameraType.UNKNOWN
    camera_motion: CameraMotion = CameraMotion.UNKNOWN
    shot_type: ShotType = ShotType.UNKNOWN
    lighting: Lighting = Lighting.UNKNOWN
    quality_score: float = Field(..., ge=0, le=10)
    quality_confidence: float = Field(..., ge=0, le=1)
    energy_score: float = Field(..., ge=0, le=10)
    energy_confidence: float = Field(..., ge=0, le=1)
    emotion: Emotion | None = None
    emotion_confidence: float = Field(0.0, ge=0, le=1)
    scene_type: str = ""
    story_role_candidates: tuple[RoleCandidate, ...] = ()
    visual_tags: Labels = ()
    reasoning: NonEmptyText

    peak_time_s: float = Field(0.0, ge=0)
    energy_curve: tuple[float, ...] = ()
    usable: bool = True
    unusable_reason: Text | None = None
    provenance: AnalysisProvenance

    def role_confidence(self, role: StoryRole) -> float:
        """Confidence that this clip can play ``role`` (0 if not a candidate)."""
        return max((c.confidence for c in self.story_role_candidates if c.role == role), default=0.0)
