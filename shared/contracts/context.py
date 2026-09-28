"""
MONTA — ContextPack (Layer 4 output)
======================================
The only input downstream agents (Director, Video Intelligence, Story
Architect) are allowed to consume. It bundles the interpreted intent with
everything MONTA knows about the creator and their footage, plus provenance so
a degraded source (e.g. memory store timeout) is visible rather than silent.
"""

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.base import FREEZE, Text
from shared.contracts.clip import ClipIntelligence, TechnicalMetadata
from shared.contracts.explain import Explained
from shared.contracts.intent import IntentAnalysis
from shared.contracts.memory import NarrativeMemoryRecord, PatternPrior
from shared.contracts.vocab import CaptionStyle, ColorGrade, MusicStyle, Pace, Platform

CONTEXT_SCHEMA_VERSION = "context.v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExplicitPreferences(BaseModel):
    """Preferences the creator set directly in settings. Always outrank inferred ones."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    pace: Pace | None = None
    color_grade: ColorGrade | None = None
    caption_style: CaptionStyle | None = None
    music_style: MusicStyle | None = None
    platform: Platform | None = None
    blocked_patterns: tuple[str, ...] = ()


class UserPreferences(BaseModel):
    """Explicit + inferred preferences, each explained."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    pace: Explained[Pace] | None = None
    color_grade: Explained[ColorGrade] | None = None
    caption_style: Explained[CaptionStyle] | None = None
    music_style: Explained[MusicStyle] | None = None
    platform: Explained[Platform] | None = None
    preferred_patterns: tuple[str, ...] = ()
    disliked_patterns: tuple[str, ...] = ()
    sample_size: int = Field(0, ge=0)


class StylePresetRef(BaseModel):
    """A Layer 8 style preset judged relevant to this request."""

    model_config = ConfigDict(frozen=True)

    name: str
    parameters: Annotated[dict[str, Any], FREEZE]
    relevance: float = Field(..., ge=0, le=1)
    reasoning: Text


class PlatformSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: Platform
    max_duration_s: float = Field(..., gt=0)
    ideal_duration_s: tuple[float, float]
    aspect_ratio: str
    resolution: str
    short_form: bool


class ContextSourceStatus(BaseModel):
    """Provenance of one context source."""

    model_config = ConfigDict(frozen=True)

    source: str
    status: Literal["ok", "degraded", "unavailable"]
    latency_ms: float = Field(..., ge=0)
    detail: Text = ""


class ContextPack(BaseModel):
    """Immutable, versioned context consumed by every agent in Layers 5–7."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["context.v1"] = CONTEXT_SCHEMA_VERSION
    project_id: str
    user_id: str
    created_at: datetime = Field(default_factory=_utcnow)

    user_prompt: str
    intent: IntentAnalysis
    user_preferences: UserPreferences
    historical_edits: tuple[NarrativeMemoryRecord, ...] = ()
    previous_successful_projects: tuple[NarrativeMemoryRecord, ...] = ()
    style_presets: tuple[StylePresetRef, ...] = ()
    story_patterns: tuple[PatternPrior, ...] = Field((), description="Learned priors for the current project type")
    platform: PlatformSpec
    video_metadata: tuple[TechnicalMetadata, ...] = Field(..., min_length=1)
    clip_intelligence: Annotated[dict[str, ClipIntelligence], FREEZE] = Field(default_factory=dict)
    provenance: tuple[ContextSourceStatus, ...] = ()
    memory_arm: Literal["memory", "control"] = Field("memory", description="Narrative-memory experiment arm")

    def prior_for(self, pattern_id: str) -> PatternPrior | None:
        return next((p for p in self.story_patterns if p.story_pattern == pattern_id), None)

    def metadata_for(self, clip_id: str) -> TechnicalMetadata | None:
        return next((m for m in self.video_metadata if m.clip_id == clip_id), None)

    @property
    def degraded_sources(self) -> tuple[str, ...]:
        return tuple(s.source for s in self.provenance if s.status != "ok")
