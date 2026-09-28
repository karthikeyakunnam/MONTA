"""
MONTA — Arbitration Contracts
===============================
Audit trail for multi-model decisions: which models answered, what each said,
how much each was trusted, which rule picked the winner, and by what margin.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.contracts.base import NonEmptyText, Text

FieldKind = Literal["categorical", "explained", "numeric", "set", "scored_roles", "text"]


class ArbitrationCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str
    value: Text
    weight: float = Field(..., ge=0)
    reliability: float = Field(..., ge=0, le=1, description="Measured per-field accuracy (Beta posterior mean)")
    self_confidence: float | None = Field(None, ge=0, le=1)


class ArbitrationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str
    field: str
    kind: FieldKind
    candidates: tuple[ArbitrationCandidate, ...]
    winner: Text
    agreement: float = Field(..., ge=0, le=1, description="Share of total weight supporting the winner")
    margin: float = Field(..., ge=0, le=1, description="(winner − runner-up) / total weight")
    rule: Literal["unanimous", "weighted_vote", "reliability_tiebreak", "weighted_mean", "support_threshold", "most_reliable"]
    rationale: NonEmptyText


class ArbitrationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_name: str
    models_queried: tuple[str, ...]
    models_succeeded: tuple[str, ...]
    failures: tuple[Text, ...] = ()
    consensus: float = Field(..., ge=0, le=1, description="Mean agreement across arbitrated fields")
    records: tuple[ArbitrationRecord, ...] = ()
