"""
MONTA — Calibration Records
=============================
A ``DecisionRecord`` is written whenever a component emits a confident
decision. An ``OutcomeRecord`` is written when the truth becomes known. The
pair (raw_confidence, correct) is the unit of calibration.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OutcomeSource = Literal["golden", "human_label", "user_override", "implicit_accept"]

# How much an outcome source is trusted when fitting (implicit signals are noisy).
SOURCE_WEIGHT: dict[str, float] = {"golden": 1.0, "human_label": 1.0, "user_override": 0.9, "implicit_accept": 0.5}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_decision_id() -> str:
    return uuid.uuid4().hex


class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(default_factory=new_decision_id)
    component: str = Field(..., description="e.g. 'intent', 'intent.llm', 'vision', 'story'")
    field: str = Field(..., description="e.g. 'genre', 'emotion', 'story_confidence'")
    model_id: str | None = Field(None, description="Model that produced the value (arbitration candidates)")
    predicted_value: str
    raw_confidence: float = Field(..., ge=0, le=1)
    calibrated_confidence: float = Field(..., ge=0, le=1)
    versions: str = Field("", description="Component/lexicon/model versions, for drift analysis")
    project_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    correct: bool
    source: OutcomeSource
    observed_value: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class LabeledDecision(BaseModel):
    """A decision joined with its (latest) outcome."""

    model_config = ConfigDict(frozen=True)

    decision: DecisionRecord
    outcome: OutcomeRecord

    @property
    def weight(self) -> float:
        return SOURCE_WEIGHT[self.outcome.source]
