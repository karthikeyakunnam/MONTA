"""
MONTA — Golden Project Schema
===============================
A golden project fixes the *inputs* (prompt, platform, analyzed clip
metadata) and the *expectations* a professional editor signed off on
(acceptable story patterns, pacing, emotional arc, hero shot, exclusions).

Clip metadata is expressed at the Layer 6 output level (what the Video
Intelligence Team should produce), so Layer 3–7 behaviour is evaluated
independently of vision-model variance. Layer 6 itself is evaluated against
labeled media in ``evaluation/video_intelligence``.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.clip import AnalysisProvenance, ClipIntelligence, RoleCandidate
from shared.contracts.vocab import CameraMotion, Emotion, Genre, Lighting, Pace, Platform, ShotType, StoryRole

GOLDEN_SCHEMA_VERSION = "golden.v1"
Category = Literal["gym", "travel", "podcast", "wedding", "event", "cinematic", "product_launch"]
ArcShape = Literal["build", "build_release", "steady", "hook_first"]


class GoldenClip(BaseModel):
    model_config = ConfigDict(frozen=True)

    clip_id: str
    duration_s: float = Field(..., gt=0)
    activities: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    scene_type: str = ""
    shot_type: ShotType = ShotType.UNKNOWN
    camera_motion: CameraMotion = CameraMotion.UNKNOWN
    lighting: Lighting = Lighting.GOOD
    quality_score: float = Field(..., ge=0, le=10)
    energy_score: float = Field(..., ge=0, le=10)
    emotion: Emotion | None = None
    story_roles: tuple[RoleCandidate, ...] = ()
    usable: bool = True
    note: str = ""

    def to_clip_intelligence(self) -> ClipIntelligence:
        return ClipIntelligence(
            clip_id=self.clip_id, duration_s=self.duration_s, activities=self.activities, objects=self.objects,
            scene_type=self.scene_type, shot_type=self.shot_type, camera_motion=self.camera_motion, lighting=self.lighting,
            quality_score=self.quality_score, quality_confidence=0.9, energy_score=self.energy_score, energy_confidence=0.8,
            emotion=self.emotion, emotion_confidence=0.8 if self.emotion else 0.0, story_role_candidates=self.story_roles,
            reasoning=f"golden label{': ' + self.note if self.note else ''}", peak_time_s=self.duration_s / 2,
            usable=self.usable, unusable_reason=None if self.usable else "golden: marked unusable",
            provenance=AnalysisProvenance(signal_version="golden", vision_model="golden:labels",
                                          analyzed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )


class ExpectedArc(BaseModel):
    model_config = ConfigDict(frozen=True)

    shape: ArcShape = Field(..., description="build: rises to a late peak; build_release: rises then resolves; "
                                             "steady: even energy; hook_first: strongest moment opens")
    start_pace: Pace | None = None
    end_emotion: Emotion | None = None


class GoldenExpectations(BaseModel):
    model_config = ConfigDict(frozen=True)

    story_patterns: tuple[str, ...] = Field(..., min_length=1, description="Acceptable patterns, preferred first")
    genre: Genre
    pace: Pace
    emotion: Emotion | None = Field(None, description="Target emotion, when the prompt implies one")
    emotional_arc: ExpectedArc
    hero_clip_ids: tuple[str, ...] = Field((), description="Acceptable hero shots")
    must_exclude: tuple[str, ...] = Field((), description="Clips a professional editor would never use")
    duration_s: float | None = Field(None, gt=0)
    min_judge_score: float = Field(6.5, ge=0, le=10)


class GoldenProject(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["golden.v1"] = GOLDEN_SCHEMA_VERSION
    project_id: str = Field(..., pattern=r"^[a-z0-9_]+$")
    category: Category
    description: str
    prompt: str
    platform: Platform = Platform.INSTAGRAM
    clips: tuple[GoldenClip, ...] = Field(..., min_length=1)
    expected: GoldenExpectations
    labeled_by: str = Field(..., description="Who signed off the expectations")
    version: int = Field(1, ge=1)

    @model_validator(mode="after")
    def _references(self):
        ids = {c.clip_id for c in self.clips}
        if len(ids) != len(self.clips):
            raise ValueError(f"{self.project_id}: duplicate clip ids")
        unknown = (set(self.expected.hero_clip_ids) | set(self.expected.must_exclude)) - ids
        if unknown:
            raise ValueError(f"{self.project_id}: expectations reference unknown clips {sorted(unknown)}")
        return self

    def clip_intelligence(self) -> dict[str, ClipIntelligence]:
        return {c.clip_id: c.to_clip_intelligence() for c in self.clips}


def role(r: StoryRole, c: float) -> RoleCandidate:
    return RoleCandidate(role=r, confidence=c)
