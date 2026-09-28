"""
MONTA — Judge Validity
========================
A judge is only as good as its agreement with people. Given human ratings
(0–10 overall, optional per dimension) for a set of story plans, this module
reports Spearman correlation, pairwise ranking accuracy and per-dimension
agreement, and decides whether the judge is trusted.

Trust rule (applied before an LLM judge is given weight in production):
    n ≥ 30 rated plans  and  Spearman ρ ≥ 0.6  and  pairwise accuracy ≥ 0.7
"""

import itertools
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from evaluation.video_intelligence.evaluate import spearman
from shared.contracts.evaluation import JUDGE_DIMENSIONS, StoryJudgement

MIN_N = 30
MIN_RHO = 0.6
MIN_PAIRWISE = 0.7


class HumanRating(BaseModel):
    model_config = ConfigDict(frozen=True)

    plan_id: str
    overall: float
    dimensions: dict[str, float] = {}
    raters: int = 1


class JudgeAgreement(BaseModel):
    model_config = ConfigDict(frozen=True)

    judge_id: str
    n: int
    spearman_overall: float | None
    pairwise_accuracy: float | None
    spearman_by_dimension: dict[str, float | None]
    trusted: bool
    reasoning: str


def agreement(judge_id: str, pairs: Sequence[tuple[StoryJudgement, HumanRating]]) -> JudgeAgreement:
    judge_scores = [j.overall_score for j, _ in pairs]
    human_scores = [h.overall for _, h in pairs]
    rho = spearman(judge_scores, human_scores)
    comparable = correct = 0
    for (j1, h1), (j2, h2) in itertools.combinations(pairs, 2):
        if h1.overall == h2.overall:
            continue
        comparable += 1
        correct += (j1.overall_score - j2.overall_score) * (h1.overall - h2.overall) > 0
    pairwise = round(correct / comparable, 4) if comparable else None
    by_dim = {}
    for dim in JUDGE_DIMENSIONS:
        rows = [(getattr(j, f"{dim}_score"), h.dimensions[dim]) for j, h in pairs if dim in h.dimensions]
        by_dim[dim] = spearman([a for a, _ in rows], [b for _, b in rows]) if rows else None
    trusted = len(pairs) >= MIN_N and (rho or 0) >= MIN_RHO and (pairwise or 0) >= MIN_PAIRWISE
    reasoning = (f"n={len(pairs)} (min {MIN_N}), ρ={rho} (min {MIN_RHO}), pairwise={pairwise} (min {MIN_PAIRWISE})")
    return JudgeAgreement(judge_id=judge_id, n=len(pairs), spearman_overall=rho, pairwise_accuracy=pairwise,
                          spearman_by_dimension=by_dim, trusted=trusted, reasoning=reasoning)
