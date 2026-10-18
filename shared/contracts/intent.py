"""
MONTA — Intent Contract (Layer 3 output)
==========================================
Structured, explainable interpretation of a free-form creator request.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.arbitration import ArbitrationSummary
from shared.contracts.base import NonEmptyText, Text
from shared.contracts.explain import Explained
from shared.contracts.vocab import (
    CaptionStyle,
    ColorGrade,
    Emotion,
    Genre,
    MusicStyle,
    Pace,
    Platform,
)

INTENT_SCHEMA_VERSION = "intent.v1"

ArcSection = Literal["start", "middle", "end"]


class Correction(BaseModel):
    """A spelling/slang normalization applied to the raw prompt."""

    model_config = ConfigDict(frozen=True)

    original: str
    corrected: str
    kind: Literal["spelling", "slang"]


class ArcBeat(BaseModel):
    """Requested pace and/or emotion for one section of the edit ("slow start", "huge ending")."""

    model_config = ConfigDict(frozen=True)

    section: ArcSection
    pace: Pace | None = None
    emotion: Emotion | None = None
    span: Text
    reasoning: NonEmptyText


class TextOverlayRequirement(BaseModel):
    """Prompt-quoted text with only the placement and timing the creator specified."""

    model_config = ConfigDict(frozen=True)

    role: Literal["title", "subtitle", "credit", "caption"]
    text: NonEmptyText
    source_span: NonEmptyText
    position: Literal["center", "below_title", "top", "lower_third", "bottom"] | None = None
    color: Literal["white", "magenta"] | None = None
    font_family: Literal["serif", "sans_serif"] | None = None
    wide_tracking: bool | None = None
    glow: bool | None = None
    bold: bool | None = None
    background: Literal["dark_card"] | None = None
    start_s: float | None = Field(None, ge=0)
    end_s: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _validate_timing(self):
        if self.start_s is not None and self.end_s is not None and self.end_s <= self.start_s:
            raise ValueError("text overlay end_s must be after start_s")
        return self


class SceneBeatRequirement(BaseModel):
    """An ordered visual beat explicitly named in the creator's prompt."""

    model_config = ConfigDict(frozen=True)

    order: int = Field(..., ge=0)
    description: NonEmptyText
    source_span: NonEmptyText


class CreativeDirective(BaseModel):
    """A prompt or setting instruction with explicit priority and provenance."""

    model_config = ConfigDict(frozen=True)

    category: Literal["visual", "narrative", "text", "color", "framing", "motion",
                      "transition", "audio", "duration", "aspect", "source"]
    priority: Literal["required", "preferred", "optional"]
    value: NonEmptyText
    source_span: NonEmptyText
    origin: Literal["prompt", "explicit_setting"] = "prompt"


class CreativeBrief(BaseModel):
    """Keep visual treatment, emotional goals, and energy direction as separate axes."""

    model_config = ConfigDict(frozen=True)

    visual_treatments: tuple[str, ...] = ()
    emotional_tones: tuple[Emotion, ...] = ()
    energy_trajectory: Literal["rising", "steady", "falling"] | None = None
    text_overlays: tuple[TextOverlayRequirement, ...] = ()
    scene_beats: tuple[SceneBeatRequirement, ...] = ()
    directives: tuple[CreativeDirective, ...] = ()
    evidence: tuple[str, ...] = ()
    visual_confidence: float = Field(0.0, ge=0.0, le=1.0)
    emotion_confidence: float = Field(0.0, ge=0.0, le=1.0)


class Ambiguity(BaseModel):
    """A field whose value could not be decided with confidence."""

    model_config = ConfigDict(frozen=True)

    field: str
    candidates: tuple[str, ...]
    reasoning: NonEmptyText
    clarifying_question: Text


class Conflict(BaseModel):
    """Instructions that contradict each other. ``resolution`` states what the engine chose."""

    model_config = ConfigDict(frozen=True)

    fields: tuple[str, ...]
    values: tuple[str, ...]
    severity: Literal["hard", "soft"]
    reasoning: NonEmptyText
    resolution: Text


class MissingInfo(BaseModel):
    """A required field the creator did not specify; a default was applied."""

    model_config = ConfigDict(frozen=True)

    field: str
    default_used: str
    clarifying_question: Text


class IntentAnalysis(BaseModel):
    """Layer 3 output. Core fields are always present; optional fields appear only with evidence."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["intent.v1"] = INTENT_SCHEMA_VERSION
    raw_prompt: str
    normalized_prompt: str
    corrections: tuple[Correction, ...] = ()

    genre: Explained[Genre]
    emotion: Explained[Emotion]
    pace: Explained[Pace]
    target_platform: Explained[Platform]

    color_grade: Explained[ColorGrade] | None = None
    caption_style: Explained[CaptionStyle] | None = None
    music_style: Explained[MusicStyle] | None = None
    reference_style: Explained[str] | None = Field(None, description="Named reference such as 'nike' or 'movie trailer'")
    target_duration_s: Explained[float] | None = None
    pacing_arc: tuple[ArcBeat, ...] = ()
    creative_brief: CreativeBrief = Field(default_factory=CreativeBrief)

    ambiguities: tuple[Ambiguity, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    missing: tuple[MissingInfo, ...] = ()

    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    extractors: tuple[str, ...] = Field(..., min_length=1, description="Extractors that contributed, e.g. ('lexical', 'llm:gemini-2.0-flash')")
    degraded: tuple[str, ...] = Field((), description="Why a richer extraction path was unavailable")
    revisions: tuple[str, ...] = Field((), description="Post-extraction adjustments (footage reconciliation, user preferences)")
    arbitration: ArbitrationSummary | None = Field(None, description="Present when several LLMs were consulted")

    def arc_for(self, section: ArcSection) -> ArcBeat | None:
        """Return the arc beat for a section, if the creator specified one."""
        return next((b for b in self.pacing_arc if b.section == section), None)

    @property
    def needs_clarification(self) -> bool:
        """True when the UI should surface a clarifying question before rendering."""
        return any(c.severity == "hard" for c in self.conflicts) or self.overall_confidence < 0.35
