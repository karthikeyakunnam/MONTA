"""
MONTA — Narrative Memory Contracts
====================================
Outcome records for finished stories and the learned priors derived from them.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.vocab import Emotion, Genre, Pace

# Weights of the observable outcome signals in the success index. Engagement
# dominates because it is the least biased signal (ratings are sparse and skew
# positive; completion depends heavily on duration).
SUCCESS_WEIGHTS = {"engagement": 0.4, "completion": 0.3, "rating": 0.3}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NarrativeMemoryRecord(BaseModel):
    """Outcome of one published story, written by the Learning Layer after metrics settle."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(..., min_length=1, max_length=64)
    project_id: str = Field(..., min_length=1, max_length=64)
    user_id: str = Field(..., min_length=1, max_length=64)
    project_type: Genre
    story_pattern: str = Field(..., min_length=1, max_length=64)
    pace: Pace | None = None
    emotion: Emotion | None = None
    engagement_score: float = Field(..., ge=0, le=10)
    completion_rate: float = Field(..., ge=0, le=10)
    user_rating: float = Field(..., ge=0, le=10)
    successful_elements: tuple[str, ...] = Field((), description="e.g. 'hero_at_0.9', 'slow_open', 'Nike captions'")
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def success_index(self) -> float:
        """Weighted outcome in [0, 1]."""
        return (
            SUCCESS_WEIGHTS["engagement"] * self.engagement_score
            + SUCCESS_WEIGHTS["completion"] * self.completion_rate
            + SUCCESS_WEIGHTS["rating"] * self.user_rating
        ) / 10.0


class PatternStats(BaseModel):
    """Raw aggregate of records for one (project_type, pattern) pair."""

    model_config = ConfigDict(frozen=True)

    story_pattern: str
    project_type: Genre | None = Field(None, description="None means aggregated across all project types")
    sample_size: int = Field(..., ge=0)
    mean_success: float = Field(..., ge=0, le=1)
    top_elements: tuple[str, ...] = ()


class PatternPrior(BaseModel):
    """Smoothed belief about how well a pattern performs for a project type."""

    model_config = ConfigDict(frozen=True)

    story_pattern: str
    project_type: Genre
    success_mean: float = Field(..., ge=0, le=1)
    confidence: float = Field(..., ge=0, le=1, description="n / (n + k); 0 when there is no history")
    sample_size: int = Field(..., ge=0)
    top_elements: tuple[str, ...] = ()
    reasoning: str = Field(..., min_length=1)
