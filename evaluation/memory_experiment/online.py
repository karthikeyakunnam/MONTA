"""
MONTA — Online Memory Analysis
================================
Compares published-story outcomes between arms. Primary metric: user rating
(satisfaction). Secondary: completion rate, engagement.

Statistics:
* difference of means (memory − control) per metric
* 95% bootstrap CI (percentile, 2 000 resamples, seeded)
* two-sided permutation p-value (2 000 permutations, seeded)

``decide``:
* ``keep``      — primary CI lower bound > 0 and p < 0.05
* ``disable``   — primary CI upper bound < 0 and p < 0.05 (memory hurts)
* ``collect``   — not enough evidence either way (reports the sample size shortfall)
"""

import random
import statistics
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from shared.contracts.memory import NarrativeMemoryRecord

METRICS = {
    "user_rating": lambda r: r.user_rating,
    "completion_rate": lambda r: r.completion_rate,
    "engagement_score": lambda r: r.engagement_score,
}
MIN_PER_ARM = 30


class MetricComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    memory_mean: float | None
    control_mean: float | None
    difference: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None


class OnlineMemoryReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    n_memory: int
    n_control: int
    comparisons: tuple[MetricComparison, ...]
    decision: Literal["keep", "disable", "collect"]
    reasoning: str


def _bootstrap_ci(a: Sequence[float], b: Sequence[float], rng: random.Random, n: int = 2000) -> tuple[float, float]:
    diffs = []
    for _ in range(n):
        ra = [a[rng.randrange(len(a))] for _ in a]
        rb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(statistics.fmean(ra) - statistics.fmean(rb))
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def _permutation_p(a: Sequence[float], b: Sequence[float], rng: random.Random, n: int = 2000) -> float:
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pooled = list(a) + list(b)
    hits = 0
    for _ in range(n):
        rng.shuffle(pooled)
        if abs(statistics.fmean(pooled[:len(a)]) - statistics.fmean(pooled[len(a):])) >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (n + 1)


def analyze_online(records: Sequence[NarrativeMemoryRecord], arm_of: Callable[[str], str], *, seed: int = 0) -> OnlineMemoryReport:
    memory = [r for r in records if arm_of(r.user_id) == "memory"]
    control = [r for r in records if arm_of(r.user_id) == "control"]
    rng = random.Random(seed)
    comps = []
    for name, get in METRICS.items():
        a, b = [get(r) for r in memory], [get(r) for r in control]
        if len(a) < 2 or len(b) < 2:
            comps.append(MetricComparison(metric=name, memory_mean=statistics.fmean(a) if a else None,
                                          control_mean=statistics.fmean(b) if b else None,
                                          difference=None, ci_low=None, ci_high=None, p_value=None))
            continue
        lo, hi = _bootstrap_ci(a, b, rng)
        comps.append(MetricComparison(metric=name, memory_mean=round(statistics.fmean(a), 4), control_mean=round(statistics.fmean(b), 4),
                                      difference=round(statistics.fmean(a) - statistics.fmean(b), 4),
                                      ci_low=round(lo, 4), ci_high=round(hi, 4), p_value=round(_permutation_p(a, b, rng), 4)))
    decision, reasoning = decide(comps[0], len(memory), len(control))
    return OnlineMemoryReport(n_memory=len(memory), n_control=len(control), comparisons=tuple(comps),
                              decision=decision, reasoning=reasoning)


def decide(primary: MetricComparison, n_memory: int, n_control: int) -> tuple[Literal["keep", "disable", "collect"], str]:
    if min(n_memory, n_control) < MIN_PER_ARM or primary.p_value is None:
        return "collect", f"need ≥{MIN_PER_ARM} rated stories per arm (have memory={n_memory}, control={n_control})"
    if primary.ci_low > 0 and primary.p_value < 0.05:
        return "keep", (f"memory raises {primary.metric} by {primary.difference:+.2f} "
                        f"(95% CI {primary.ci_low:+.2f}..{primary.ci_high:+.2f}, p={primary.p_value})")
    if primary.ci_high < 0 and primary.p_value < 0.05:
        return "disable", (f"memory lowers {primary.metric} by {primary.difference:+.2f} "
                           f"(95% CI {primary.ci_low:+.2f}..{primary.ci_high:+.2f}, p={primary.p_value})")
    return "collect", (f"no significant effect on {primary.metric} yet (diff {primary.difference:+.2f}, "
                       f"CI {primary.ci_low:+.2f}..{primary.ci_high:+.2f}, p={primary.p_value})")
