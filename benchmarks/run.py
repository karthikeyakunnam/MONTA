"""
MONTA — Benchmark Runner
==========================
Usage::

    python -m benchmarks.run                                   # current variant → benchmark_report.json
    python -m benchmarks.run --variants current,env_models     # compare against configured models
    python -m benchmarks.run --baseline benchmarks/baselines/architect.v2.json   # previous version
    python -m benchmarks.run --save-baseline benchmarks/baselines/architect.v2.json

Exit status 1 when any variant regresses against the baseline (CI gate).
Cost is computed only from ``benchmarks/pricing.json`` (USD per 1M tokens per
model_id); without pricing the report lists tokens and says cost is unknown.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from benchmarks.report import BenchmarkReport, Comparison, VariantMetrics, compare
from benchmarks.variants import VARIANTS, Variant
from datasets.loader import load_golden, manifest
from evaluation.golden import run_golden
from evaluation.story_judge.judge import JUDGE_VERSION
from orchestration.agents.intelligence.fusion import FUSION_VERSION
from orchestration.agents.intelligence.signals import SIGNAL_VERSION
from orchestration.agents.story.architect import ALGORITHM_VERSION
from services.prompt_engine.lexicon import LEXICON_VERSION
from shared.observability.metrics import REGISTRY

PRICING_PATH = Path(__file__).parent / "pricing.json"


def _token_snapshot() -> dict[tuple[str, str, str], float]:
    return {k: v for _, k, v in REGISTRY.get("monta_provider_tokens_total").samples()}


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip() + (
            "-dirty" if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _cost(tokens: dict[tuple[str, str, str], float], pricing: dict) -> tuple[float | None, str]:
    if not tokens:
        return 0.0, "no model calls"
    total, missing = 0.0, set()
    for (provider, model, direction), n in tokens.items():
        price = pricing.get(f"{provider}:{model}")
        if not price:
            missing.add(f"{provider}:{model}")
            continue
        total += n / 1e6 * float(price["input" if direction == "input" else "output"])
    if missing:
        return None, f"no pricing for {sorted(missing)} in benchmarks/pricing.json"
    return round(total, 6), "computed from benchmarks/pricing.json"


async def run_variant(variant: Variant, projects, pricing: dict) -> VariantMetrics:
    before = _token_snapshot()
    report = await run_golden(projects, variant.build())
    after = _token_snapshot()
    used = {k: after[k] - before.get(k, 0.0) for k in after if after[k] - before.get(k, 0.0) > 0}
    per_model: dict[str, int] = defaultdict(int)
    for (provider, model, _), n in used.items():
        per_model[f"{provider}:{model}"] += int(n)
    cost, note = _cost(used, pricing)
    runtimes = sorted(r.runtime_s for r in report.results)
    p95 = runtimes[min(len(runtimes) - 1, int(round(0.95 * (len(runtimes) - 1))))]
    judged = [r.judgement for r in report.results if r.judgement]
    mean = lambda xs: round(statistics.fmean(xs), 4) if xs else 0.0  # noqa: E731
    failures = {}
    for r in report.results:
        failed = [f"{c.name}: {c.detail}" for c in r.checks if c.passed is False]
        if r.error:
            failed.append(f"error: {r.error}")
        if failed:
            failures[r.project_id] = failed
    return VariantMetrics(
        variant=variant.name, description=variant.description, projects=report.projects,
        story_quality=mean([j.overall_score for j in judged]),
        prompt_alignment=mean([j.prompt_alignment_score for j in judged]),
        narrative_score=mean([j.coherence_score for j in judged]),
        golden_pass_rate=report.pass_rate,
        validity_rate=report.check_pass_rates.get("valid", 0.0),
        check_pass_rates=report.check_pass_rates, category_pass_rates=report.category_pass_rates,
        judge_dimensions=report.mean_judge,
        runtime_p50_s=round(statistics.median(runtimes), 4), runtime_p95_s=round(p95, 4),
        tokens=dict(per_model), cost_usd=cost, cost_note=note, failures=failures,
    )


async def run(variant_names: list[str], *, baseline_path: str | None = None, categories: set[str] | None = None) -> BenchmarkReport:
    projects = load_golden(categories=categories)
    pricing = json.loads(PRICING_PATH.read_text()) if PRICING_PATH.exists() else {}
    unknown = set(variant_names) - set(VARIANTS)
    if unknown:
        raise SystemExit(f"unknown variants {sorted(unknown)}; available: {sorted(VARIANTS)}")
    metrics = [await run_variant(VARIANTS[n], projects, pricing) for n in variant_names]
    comparisons: list[Comparison] = []
    if baseline_path:
        base = BenchmarkReport.model_validate_json(Path(baseline_path).read_text())
        base_by_name = {v.variant: v for v in base.variants}
        for m in metrics:
            ref = base_by_name.get(m.variant) or base.variants[0]
            comparisons.append(compare(m, ref, f"{Path(baseline_path).name}:{ref.variant}@{base.git_sha}"))
    return BenchmarkReport(
        git_sha=_git_sha(),
        algorithm_versions={"architect": ALGORITHM_VERSION, "judge": JUDGE_VERSION, "lexicon": LEXICON_VERSION,
                            "signals": SIGNAL_VERSION, "fusion": FUSION_VERSION},
        dataset=manifest(), variants=tuple(metrics), comparisons=tuple(comparisons),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", default="current")
    ap.add_argument("--baseline")
    ap.add_argument("--categories")
    ap.add_argument("--out", default="benchmark_report.json")
    ap.add_argument("--save-baseline")
    args = ap.parse_args(argv)
    cats = set(args.categories.split(",")) if args.categories else None
    report = asyncio.run(run(args.variants.split(","), baseline_path=args.baseline, categories=cats))
    payload = report.model_dump_json(indent=2)
    Path(args.out).write_text(payload)
    if args.save_baseline:
        Path(args.save_baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_baseline).write_text(payload)
    for v in report.variants:
        print(f"{v.variant:18} quality={v.story_quality:.2f} align={v.prompt_alignment:.2f} narrative={v.narrative_score:.2f} "
              f"golden={v.golden_pass_rate:.0%} valid={v.validity_rate:.0%} p95={v.runtime_p95_s:.3f}s cost={v.cost_usd} ({v.cost_note})")
    for c in report.comparisons:
        status = "REGRESSION: " + ", ".join(c.regressions) if c.regressions else "no regression"
        print(f"vs {c.baseline_label}: {status}")
    return 1 if report.has_regression else 0


if __name__ == "__main__":
    sys.exit(main())
