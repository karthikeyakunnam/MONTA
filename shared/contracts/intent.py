"""
MONTA — Intent Contract (Layer 3 output)
==========================================
Structured, explainable interpretation of a free-form creator request.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
