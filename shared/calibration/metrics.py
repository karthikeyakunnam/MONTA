"""
MONTA — Reliability Statistics
================================
* Reliability curve — per confidence bin: mean confidence vs observed accuracy.
* ECE — expected calibration error: Σ (n_b / N) · |acc_b − conf_b|.
* MCE — maximum calibration error over populated bins.
* Brier score — mean (confidence − correct)².
* Histogram — how confidences are distributed (a well-calibrated but always-0.5
  model is useless; sharpness matters too).
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field


class ReliabilityBin(BaseModel):
    model_config = ConfigDict(frozen=True)

    lo: float
    hi: float
    count: float
    mean_confidence: float | None
    accuracy: float | None


class ReliabilityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: float = Field(..., description="Weighted sample count")
    ece: float
    mce: float
    brier: float
    accuracy: float
    mean_confidence: float
    bins: tuple[ReliabilityBin, ...]
    histogram: tuple[float, ...] = Field(..., description="Weighted count per confidence bin")


def reliability_report(confidences: Sequence[float], correct: Sequence[bool], weights: Sequence[float] | None = None,
                       n_bins: int = 10) -> ReliabilityReport:
    if len(confidences) != len(correct):
        raise ValueError("confidences and correct must align")
    if not confidences:
        raise ValueError("no samples")
    w = list(weights) if weights is not None else [1.0] * len(confidences)
    bins_w = [0.0] * n_bins
    bins_c = [0.0] * n_bins
    bins_a = [0.0] * n_bins
    for c, y, wi in zip(confidences, correct, w):
        b = min(n_bins - 1, int(c * n_bins))
        bins_w[b] += wi
        bins_c[b] += wi * c
        bins_a[b] += wi * float(y)
    total = sum(bins_w)
    bins, ece, mce = [], 0.0, 0.0
    for i in range(n_bins):
        if bins_w[i] > 0:
            mc, acc = bins_c[i] / bins_w[i], bins_a[i] / bins_w[i]
            gap = abs(acc - mc)
            ece += bins_w[i] / total * gap
            mce = max(mce, gap)
            bins.append(ReliabilityBin(lo=i / n_bins, hi=(i + 1) / n_bins, count=bins_w[i], mean_confidence=round(mc, 4), accuracy=round(acc, 4)))
        else:
            bins.append(ReliabilityBin(lo=i / n_bins, hi=(i + 1) / n_bins, count=0, mean_confidence=None, accuracy=None))
    brier = sum(wi * (c - float(y)) ** 2 for c, y, wi in zip(confidences, correct, w)) / total
    return ReliabilityReport(
        n=total, ece=round(ece, 4), mce=round(mce, 4), brier=round(brier, 4),
        accuracy=round(sum(bins_a) / total, 4), mean_confidence=round(sum(bins_c) / total, 4),
        bins=tuple(bins), histogram=tuple(round(x, 3) for x in bins_w),
    )
