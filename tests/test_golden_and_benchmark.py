"""Golden dataset framework, comparison tooling and benchmark runner (regression gates)."""

import asyncio
import json

import pytest

from benchmarks.report import BenchmarkReport, compare
from benchmarks.run import main as bench_main
from benchmarks.run import run as bench_run
from datasets.loader import GOLDEN_DIR, load_golden, manifest
from datasets.schema import GoldenProject
from evaluation.golden import run_golden
from benchmarks.variants import VARIANTS

REQUIRED_CATEGORIES = {"gym", "travel", "podcast", "wedding", "event", "cinematic", "product_launch"}
# Regression floor for the current release. Raise it when quality improves; never lower it silently.
MIN_GOLDEN_PASS_RATE = 0.70
MIN_MEAN_JUDGE = 7.4


def test_golden_dataset_covers_required_categories():
    projects = load_golden()
    assert REQUIRED_CATEGORIES <= {p.category for p in projects}
    for p in projects:
        assert p.prompt and p.clips and p.expected.story_patterns and p.expected.emotional_arc


def test_golden_schema_rejects_bad_references(tmp_path):
    data = json.loads((GOLDEN_DIR / "gym_nike_transformation.json").read_text())
    data["expected"]["hero_clip_ids"] = ["missing"]
    with pytest.raises(ValueError, match="unknown clips"):
        GoldenProject.model_validate(data)
    (tmp_path / "wrong_name.json").write_text(json.dumps(json.loads((GOLDEN_DIR / "gym_nike_transformation.json").read_text())))
    with pytest.raises(ValueError, match="file name"):
        load_golden(tmp_path)


def test_manifest_hash_is_stable():
    assert manifest() == manifest() and manifest()["files"] == len(load_golden())


async def test_golden_regression_floor():
    report = await run_golden(load_golden(), VARIANTS["current"].build())
    assert not [r.project_id for r in report.results if r.error], "no golden project may crash"
    assert report.pass_rate >= MIN_GOLDEN_PASS_RATE, {r.project_id: [c.detail for c in r.checks if c.passed is False]
                                                      for r in report.results if not r.passed}
    assert report.mean_judge["overall"] >= MIN_MEAN_JUDGE
    for check in ("pattern", "duration", "exclusions", "hero"):
        assert report.check_pass_rates[check] == 1.0, check


def test_benchmark_report_and_baseline_gate(tmp_path):
    report = asyncio.run(bench_run(["current"]))
    v = report.variants[0]
    assert v.projects == len(load_golden()) and v.cost_usd == 0.0 and v.cost_note == "no model calls"
    assert report.dataset["sha256"] and report.algorithm_versions["architect"]

    baseline = tmp_path / "base.json"
    assert bench_main(["--variants", "current", "--out", str(tmp_path / "r.json"), "--save-baseline", str(baseline)]) == 0
    assert bench_main(["--variants", "current", "--out", str(tmp_path / "r2.json"), "--baseline", str(baseline)]) == 0
    loaded = BenchmarkReport.model_validate_json(baseline.read_text())
    better = loaded.variants[0].model_copy(update={"story_quality": v.story_quality + 1.0})
    cmp = compare(v, better, "synthetic-better-baseline")
    assert "story_quality" in cmp.regressions


def test_committed_baseline_is_loadable():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "benchmarks" / "baselines" / "architect.v2.json"
    report = BenchmarkReport.model_validate_json(path.read_text())
    assert report.variants and report.variants[0].golden_pass_rate > 0
