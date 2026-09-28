"""
MONTA — Benchmark Report Schema & Comparison
==============================================
Metrics per variant:

* story_quality      — mean independent-judge overall score (0–10)
* prompt_alignment   — mean judge prompt-alignment score (0–10)
* narrative_score    — mean judge coherence score (0–10)
* golden_pass_rate   — share of golden projects passing every applicable check
* check_pass_rates   — per golden check (pattern, pace, arc, duration, …)
* validity_rate      — share of plans passing pattern-specific validation
* runtime_p50_s / runtime_p95_s — wall time per project
* tokens / cost_usd  — model usage; cost only when pricing is configured (never guessed)

``compare`` diffs a variant against a baseline report and flags regressions.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

REPORT_VERSION = "benchmark.v1"

# metric → (direction, tolerated change). direction +1: higher is better.
REGRESSION_RULES: dict[str, tuple[int, float]] = {
    "story_quality": (+1, 0.20),
    "prompt_alignment": (+1, 0.25),
    "narrative_score": (+1, 0.25),
    "golden_pass_rate": (+1, 0.05),
    "validity_rate": (+1, 0.05),
    "runtime_p95_s": (-1, 0.50),   # relative: +50% is a regression
}
RELATIVE_METRICS = {"runtime_p95_s"}
RUNTIME_ABS_FLOOR_S = 0.5  # sub-half-second p95 changes are noise, not regressions


class VariantMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant: str
    description: str
    projects: int
    story_quality: float
    prompt_alignment: float
    narrative_score: float
    golden_pass_rate: float
    validity_rate: float
    check_pass_rates: dict[str, float]
    category_pass_rates: dict[str, float]
    judge_dimensions: dict[str, float]
    runtime_p50_s: float
    runtime_p95_s: float
    tokens: dict[str, int] = Field(default_factory=dict, description="model_id → total tokens")
    cost_usd: float | None
    cost_note: str
    failures: dict[str, list[str]] = Field(default_factory=dict, description="project_id → failed checks / errors")


class MetricDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    baseline: float
    current: float
    delta: float
    regression: bool


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    variant: str
    baseline_label: str
    deltas: tuple[MetricDelta, ...]
    regressions: tuple[str, ...]


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_version: str = REPORT_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    git_sha: str
    algorithm_versions: dict[str, str]
    dataset: dict
    variants: tuple[VariantMetrics, ...]
    comparisons: tuple[Comparison, ...] = ()

    @property
    def has_regression(self) -> bool:
        return any(c.regressions for c in self.comparisons)


def compare(current: VariantMetrics, baseline: VariantMetrics, baseline_label: str) -> Comparison:
    deltas, regressions = [], []
    for metric, (direction, tolerance) in REGRESSION_RULES.items():
        b, c = getattr(baseline, metric), getattr(current, metric)
        delta = c - b
        if metric in RELATIVE_METRICS:
            worse = b > 0 and (c - b) / b > tolerance and (c - b) > RUNTIME_ABS_FLOOR_S
        else:
            worse = direction * delta < -tolerance
        deltas.append(MetricDelta(metric=metric, baseline=b, current=c, delta=round(delta, 4), regression=worse))
        if worse:
            regressions.append(metric)
    return Comparison(variant=current.variant, baseline_label=baseline_label, deltas=tuple(deltas), regressions=tuple(regressions))
