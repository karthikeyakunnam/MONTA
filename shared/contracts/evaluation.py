"""
MONTA — Evaluation Contracts
==============================
Outputs of evaluators that sit *outside* the generation path: the Story
Judge, benchmark reports, and calibration records. Keeping them separate
from ``story.py`` makes the independence rule enforceable: the Story
Architect produces ``StoryPlan``; only evaluators produce ``StoryJudgement``.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.base import NonEmptyText


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


JUDGE_DIMENSIONS = (
    "coherence", "emotion", "pacing", "hook", "ending", "prompt_alignment", "clip_relevance",
)


class DimensionScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float = Field(..., ge=0, le=10)
    rationale: NonEmptyText


class StoryJudgement(BaseModel):
    """Independent verdict on one StoryPlan."""

    model_config = ConfigDict(frozen=True)

    judge_id: str = Field(..., description="e.g. 'heuristic:judge.v1' or 'llm:gemini:gemini-2.0-flash'")
    plan_pattern: str
    overall_score: float = Field(..., ge=0, le=10)
    coherence_score: float = Field(..., ge=0, le=10)
    emotion_score: float = Field(..., ge=0, le=10)
    pacing_score: float = Field(..., ge=0, le=10)
    hook_score: float = Field(..., ge=0, le=10)
    ending_score: float = Field(..., ge=0, le=10)
    prompt_alignment_score: float = Field(..., ge=0, le=10)
    clip_relevance_score: float = Field(..., ge=0, le=10)
    confidence: float = Field(..., ge=0, le=1)
    valid_plan: bool = Field(..., description="The plan passed its own validation (a failing plan can never beat a passing one)")
    rationale: dict[str, DimensionScore]
    components: tuple[str, ...] = Field((), description="Judges that contributed to a composite verdict")
    judged_at: datetime = Field(default_factory=_utcnow)

    @property
    def rank_key(self) -> tuple[bool, float]:
        """Sort key for best-of selection: valid plans first, then overall score."""
        return (self.valid_plan, self.overall_score)


class StoryAttempt(BaseModel):
    """One story produced during a run (initial or retry) and how it was judged."""

    model_config = ConfigDict(frozen=True)

    attempt: int = Field(..., ge=0)
    story_pattern: str
    judgement: StoryJudgement
    kept: bool
    reason: NonEmptyText


OutcomeLabel = Literal["correct", "incorrect"]
