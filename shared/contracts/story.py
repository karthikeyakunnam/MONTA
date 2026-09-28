"""
MONTA — Story Contracts (Layer 7)
===================================
Story patterns (reusable narrative templates), the story plan the architect
produces, and the timeline validation report.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.base import FREEZE, NonEmptyText, Text
from shared.contracts.explain import Explained, Score
from shared.contracts.vocab import Emotion, Genre, Pace, StoryRole

STORY_SCHEMA_VERSION = "story.v1"

EnergyDirection = Literal["rise", "hold", "fall"]


class ActTemplate(BaseModel):
    """One act of a story pattern."""

    model_config = ConfigDict(frozen=True)

    act_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    purpose: str = Field(..., min_length=1, description="Why this act exists in the story")
    target_energy: tuple[float, float]
    energy_direction: EnergyDirection
    preferred_roles: tuple[StoryRole, ...] = Field(..., min_length=1)
    preferred_emotions: tuple[Emotion, ...] = ()
    share: float = Field(..., gt=0, le=1, description="Fraction of total runtime")

    @model_validator(mode="after")
    def _energy_range(self):
        lo, hi = self.target_energy
        if not (0 <= lo <= hi <= 10):
            raise ValueError(f"{self.act_id}: target_energy must satisfy 0 <= lo <= hi <= 10")
        return self


class PatternValidation(BaseModel):
    """Story-type-specific timeline rules. A podcast is not judged by fitness-reel energy rules."""

    model_config = ConfigDict(frozen=True)

    low_energy_threshold: float = Field(4.0, ge=0, le=10, description="Energy below this counts as a low-energy cut")
    max_low_energy_run: int = Field(3, ge=1, description="Max consecutive low-energy cuts")
    spike_scale: float = Field(1.0, gt=0, description="Multiplier on the pace-dependent energy-jump limit")
    direction_tolerance: float = Field(0.5, ge=0)
    hold_tolerance: float = Field(3.5, ge=0)
    min_act_energy_delta: float = Field(0.5, ge=0, description="Adjacent acts must differ by this much energy or by emotion")
    duration_tolerance: float = Field(0.05, gt=0, le=0.5, description="Allowed relative deviation from target duration")


class StoryPattern(BaseModel):
    """A reusable narrative template from the Story Pattern Library."""

    model_config = ConfigDict(frozen=True)

    pattern_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    version: int = Field(1, ge=1)
    description: str
    genres: tuple[Genre, ...] = Field(..., min_length=1)
    emotions: tuple[Emotion, ...] = Field(..., min_length=1)
    keywords: tuple[str, ...] = ()
    acts: tuple[ActTemplate, ...] = Field(..., min_length=2)
    required_emotions: tuple[Emotion, ...] = Field(..., min_length=1)
    pacing_curve: tuple[float, ...] = Field(..., description="Cut-length multiplier per act; <1 is faster")
    hero_moment_position: float = Field(..., ge=0, le=1, description="Where the hero moment lands, as a fraction of runtime")
    hero_roles: tuple[StoryRole, ...] = (StoryRole.HERO, StoryRole.PEAK, StoryRole.PAYOFF)
    validation: PatternValidation = PatternValidation()

    @model_validator(mode="after")
    def _consistency(self):
        if len(self.pacing_curve) != len(self.acts):
            raise ValueError(f"{self.pattern_id}: pacing_curve needs one entry per act")
        if any(m <= 0 for m in self.pacing_curve):
            raise ValueError(f"{self.pattern_id}: pacing multipliers must be positive")
        total = sum(a.share for a in self.acts)
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"{self.pattern_id}: act shares sum to {total:.3f}, expected 1.0")
        ids = [a.act_id for a in self.acts]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.pattern_id}: duplicate act ids")
        return self

    def act_at(self, position: float) -> int:
        """Index of the act covering a runtime fraction."""
        cursor = 0.0
        for i, act in enumerate(self.acts):
            cursor += act.share
            if position <= cursor + 1e-9:
                return i
        return len(self.acts) - 1


class PatternScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern_id: str
    total: float
    intent_fit: float
    footage_fit: float
    memory_prior: float
    preference_adjustment: float
    reasoning: str


class TimelineSegment(BaseModel):
    """One cut on the output timeline."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(..., ge=0)
    clip_id: str
    act_id: str
    start: float = Field(..., ge=0, description="Output timeline seconds")
    end: float = Field(..., gt=0)
    source_in: float = Field(..., ge=0, description="Seconds into the source clip")
    source_out: float = Field(..., gt=0)
    energy: float = Field(..., ge=0, le=10)
    is_hero: bool = False
    purpose: Text
    reasoning: NonEmptyText

    @property
    def duration(self) -> float:
        return self.end - self.start


class ClipDecision(BaseModel):
    """Why a clip was selected or rejected."""

    model_config = ConfigDict(frozen=True)

    clip_id: str
    selected: bool
    act_id: str | None = None
    fit_score: float = Field(..., ge=0, le=1)
    reasons: tuple[str, ...] = Field(..., min_length=1)


class ActRationale(BaseModel):
    """Why an act exists and how it was filled."""

    model_config = ConfigDict(frozen=True)

    act_id: str
    name: str
    purpose: str
    why_exists: str
    clip_ids: tuple[str, ...]
    energy_direction: EnergyDirection
    target_energy: tuple[float, float]
    realized_mean_energy: float
    cut_length_s: float


class ValidationRule(StrEnum):
    NO_GAPS = "no_gaps"
    NO_OVERLAPS = "no_overlaps"
    NO_INVALID_CLIPS = "no_invalid_clips"
    SINGLE_HERO_MOMENT = "single_hero_moment"
    NO_ABRUPT_ENERGY_SPIKES = "no_abrupt_energy_spikes"
    MAX_LOW_ENERGY_RUN = "max_low_energy_run"
    ACT_EMOTIONAL_PROGRESSION = "act_emotional_progression"
    WITHIN_SOURCE_DURATION = "within_source_duration"
    NO_EMPTY_ACTS = "no_empty_acts"
    TARGET_DURATION = "target_duration"
    NO_SOURCE_OVERLAP = "no_source_overlap"
    NO_JUMP_CUTS = "no_jump_cuts"


class ValidationViolation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: ValidationRule
    severity: Literal["error", "warning"]
    message: str
    segment_indices: tuple[int, ...] = ()
    clip_ids: tuple[str, ...] = ()


class ValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    profile: str = Field("default", description="Pattern whose validation profile was applied")
    rules_checked: tuple[ValidationRule, ...]
    violations: tuple[ValidationViolation, ...] = ()
    summary: str

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")


class StoryReasoning(BaseModel):
    """Everything the Critic needs to audit a story."""

    model_config = ConfigDict(frozen=True)

    pattern_selection: str
    candidates: tuple[PatternScore, ...]
    adaptations: tuple[str, ...]
    pacing: str
    emotion: str
    clip_decisions: tuple[ClipDecision, ...]
    acts: tuple[ActRationale, ...]
    repairs: tuple[str, ...] = ()
    refinement: str | None = None


class StoryPlan(BaseModel):
    """Layer 7 output."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["story.v1"] = STORY_SCHEMA_VERSION
    story_pattern: str
    pattern_version: int
    story_confidence: float = Field(..., ge=0, le=1)
    act_assignments: Annotated[dict[str, tuple[str, ...]], FREEZE]
    timeline: tuple[TimelineSegment, ...] = Field(..., min_length=1)
    pace: Explained[Pace]
    emotion: Explained[Emotion]
    reasoning: StoryReasoning
    story_score: Score
    emotion_score: Score
    pacing_score: Score
    validation: ValidationReport
    total_duration_s: float = Field(..., gt=0)
    target_duration_s: float = Field(..., gt=0, description="Duration the planner committed to")
    requested_duration_s: float | None = Field(None, gt=0, description="Duration the creator asked for, if any")
    hero_clip_id: str
    algorithm_version: str = Field(..., description="Story Architect algorithm/weights version for reproducibility")

    @property
    def selected_clip_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(s.clip_id for s in self.timeline))
