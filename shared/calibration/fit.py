"""
MONTA — Calibration Fitting
=============================
Chooses the calibrator by sample size (weighted):

* n ≥ 200 → isotonic regression (pool-adjacent-violators); non-parametric,
            monotone, the standard for well-populated fields.
* 30 ≤ n < 200 → Platt scaling (1-D logistic on logit(raw)); smooth, low variance.
* n < 30 → identity; too little data to move confidences honestly.

Maps are versioned and serializable (JSON) so the runtime can load them
without the training data.
"""

import math
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

ISOTONIC_MIN = 200
PLATT_MIN = 30
EPS = 1e-4


class CalibrationMap(BaseModel):
    model_config = ConfigDict(frozen=True)

    component: str
    field: str
    method: Literal["isotonic", "platt", "identity"]
    n: float
    knots_x: tuple[float, ...] = ()
    knots_y: tuple[float, ...] = ()
    platt_a: float = 1.0
    platt_b: float = 0.0
    fitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = 1

    def apply(self, raw: float) -> float:
        raw = min(1.0, max(0.0, raw))
        if self.method == "identity":
            return raw
        if self.method == "platt":
            z = self.platt_a * _logit(raw) + self.platt_b
            return round(1 / (1 + math.exp(-z)), 4)
        return round(float(np.interp(raw, self.knots_x, self.knots_y)), 4)


def _logit(p: float) -> float:
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


def isotonic(x: Sequence[float], y: Sequence[float], w: Sequence[float]) -> tuple[list[float], list[float]]:
    """Weighted pool-adjacent-violators. Returns knots (block mean x, block value)."""
    order = np.argsort(np.asarray(x), kind="stable")
    xs, ys, ws = (np.asarray(v, dtype=float)[order] for v in (x, y, w))
    blocks: list[list[float]] = []  # [sum_wy, sum_w, sum_wx]
    for xi, yi, wi in zip(xs, ys, ws):
        blocks.append([wi * yi, wi, wi * xi])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            b = blocks.pop()
            blocks[-1] = [blocks[-1][0] + b[0], blocks[-1][1] + b[1], blocks[-1][2] + b[2]]
    kx = [b[2] / b[1] for b in blocks]
    ky = [b[0] / b[1] for b in blocks]
    return [0.0] + kx + [1.0], [ky[0]] + ky + [ky[-1]]


def platt(x: Sequence[float], y: Sequence[float], w: Sequence[float], iters: int = 100) -> tuple[float, float]:
    """Weighted logistic regression y ~ σ(a·logit(x) + b) by Newton's method."""
    z = np.array([_logit(v) for v in x])
    t = np.asarray(y, dtype=float)
    wt = np.asarray(w, dtype=float)
    a, b = 1.0, 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(a * z + b)))
        g = np.array([np.sum(wt * (p - t) * z), np.sum(wt * (p - t))])
        s = wt * p * (1 - p) + 1e-9
        H = np.array([[np.sum(s * z * z), np.sum(s * z)], [np.sum(s * z), np.sum(s)]]) + 1e-6 * np.eye(2)
        step = np.linalg.solve(H, g)
        a, b = a - step[0], b - step[1]
        if np.max(np.abs(step)) < 1e-8:
            break
    return float(a), float(b)


def fit_calibration(component: str, field: str, raw: Sequence[float], correct: Sequence[bool],
                    weights: Sequence[float] | None = None, version: int = 1) -> CalibrationMap:
    w = list(weights) if weights is not None else [1.0] * len(raw)
    n = float(sum(w))
    y = [float(c) for c in correct]
    if n >= ISOTONIC_MIN:
        kx, ky = isotonic(raw, y, w)
        return CalibrationMap(component=component, field=field, method="isotonic", n=n,
                              knots_x=tuple(round(v, 6) for v in kx), knots_y=tuple(round(v, 6) for v in ky), version=version)
    if n >= PLATT_MIN:
        a, b = platt(raw, y, w)
        return CalibrationMap(component=component, field=field, method="platt", n=n, platt_a=a, platt_b=b, version=version)
    return CalibrationMap(component=component, field=field, method="identity", n=n, version=version)
