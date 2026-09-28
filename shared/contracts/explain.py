"""
MONTA — Explainability Primitives
===================================
No black-box outputs: every decision is an ``Explained[T]`` and every score is a
``Score``. Both require a non-empty reasoning string and a calibrated
confidence, so the Critic (Layer 12) can always answer "why?".
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.base import NonEmptyText, Text

T = TypeVar("T")


class Evidence(BaseModel):
    """A single observation that supported (weight > 0) or opposed (weight < 0) a decision."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(..., description="Producer of the evidence: prompt, llm, footage, signal, vision, memory, preferences, default")
    detail: Text
    weight: float = 1.0
    span: Text | None = Field(None, description="Exact input fragment the evidence came from, when applicable")


class Explained(BaseModel, Generic[T]):
    """A decision with its value, confidence, reasoning and supporting evidence."""

    model_config = ConfigDict(frozen=True)

    value: T
    confidence: float = Field(..., ge=0.0, le=1.0, description="Calibrated confidence (equals raw when no calibration map exists)")
    raw_confidence: float | None = Field(None, ge=0.0, le=1.0, description="Uncalibrated confidence as produced by the component")
    reasoning: NonEmptyText
    evidence: tuple[Evidence, ...] = ()
    is_default: bool = Field(False, description="True when no evidence existed and a policy default was used")


class Score(BaseModel):
    """A 0–10 quality score with the confidence the scorer has in it."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(..., ge=0.0, le=10.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: NonEmptyText


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp ``x`` into ``[lo, hi]``."""
    return max(lo, min(hi, x))
