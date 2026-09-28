# Layers 3–7 — Launch Cycle 1: Implementation Report

**Date:** 2026-09-22 · **Follows:** `2026-09-21-layers-3-7-launch-readiness.md`
**Suite:** 199 tests pass (plus 9 live-provider tests deselected by default). The suite ran
8× with real FFmpeg 7.0 on PATH and once without it; a flaky calibration test was found and made
deterministic.

## Verdict

Criteria 1 and 3–12 are met. **Criterion 2, real model integration, is built but not
executed.** This environment has no Qwen, DashScope or Gemini credentials, so the live suite
skips. Layers 3–7 must not be called production-complete until that suite runs green and its
results are recorded below.

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | All critical bugs fixed | **Met** | See the table below. Each fix has a regression test. |
| 2 | Real model integration tested | **Not met** (built; unexecuted) | `tests/providers_live/test_live_providers.py` has 9 tests: schema compliance per model, vision with a real PNG, timeout, invalid key, failover to a real secondary, Qwen×Gemini arbitration. Run with `python -m pytest -m live tests/providers_live` plus credentials. |
| 3 | Golden dataset exists | **Met** | `datasets/golden_projects/`: 14 projects × 7 categories, schema-validated. Labels are v1 from a single labeler (see `datasets/README.md`). |
| 4 | Benchmark runner exists | **Met** | `python -m benchmarks.run` writes `benchmark_report.json` and exits 1 on regression. Baseline: `benchmarks/baselines/architect.v2.json`. |
| 5 | Story Judge exists | **Met** | `evaluation/story_judge/`: heuristic, LLM and composite judges, with an import-level independence test and a self-judging guard. Validity against humans (`agreement.py`) is **not yet measured**, since no human ratings exist. |
| 6 | Confidence calibration exists | **Met (mechanism)** | `shared/calibration/`: decision and outcome logs (SQL plus Alembic 0002), isotonic/Platt fitting, reliability curves, ECE/MCE/Brier, histograms, runtime calibrator. On held-out data from a deliberately overconfident source, ECE drops from 0.17 to ≤0.05. **Real ECE per field is unknown until labeled outcomes accumulate.** |
| 7 | Observability exists | **Met** | `shared/observability/`: correlation ids in contextvars, spans that nest end to end (test asserts one trace per run), JSON logs, 30 metrics (catalog.py), and `/api/v1/metrics` for Prometheus. |
| 8 | Arbitration exists | **Met** | `shared/arbitration/`: concurrent multi-model output, per-field rules weighted by *measured* reliability (Beta posterior from calibration outcomes), and a recorded `ArbitrationSummary`. Wired into Layer 3 and Layer 6 with `MONTA_ARBITRATION=consensus`. |
| 9 | Memory impact measurable | **Met (mechanism)** | `evaluation/memory_experiment/`: stable arm assignment, a control arm isolated in the ContextComposer, an offline paired judge comparison, and online bootstrap/permutation analysis with keep / disable / collect decisions. **No real outcome data yet → current decision: collect.** |
| 10 | Every layer has production metrics | **Met** | L3 intent confidence, defaults and issues · L4 source status · L5 task status, duration and retries · L6 clip outcomes, vision degradation, cache, media tools · L7 pattern mix, validation, violations, duration deviation, judge score, retries. Providers: calls, latency, tokens, retries, circuit, fallback, structured attempts, arbitration. |
| 11 | Every layer has regression tests | **Met** | 17 test modules; the golden regression floor is `tests/test_golden_and_benchmark.py`. |
| 12 | No architecture placeholders | **Met for Layers 3–7** | No `TODO` or stubs remain in Layers 3–7, `shared/`, `evaluation/`, `benchmarks/` or `datasets/`. Layers 8–13 stubs (outside scope) remain, and the Critic still returns 0, so the graph runs every retry (now harmless; see bug 1). |

## Critical bug fixes

| Bug | Fix | Proof |
|---|---|---|
| 1. Retry ships a worse story | `build_story_node` judges every attempt with the independent judge and keeps the best-ranked plan (valid first, then score). Every attempt is recorded in `story_attempts`. | `test_master_graph_retries_never_reduce_story_quality`: final ≥ every attempt. The graph now keeps a better story when one is found (fitness_reel 8.x over the initial pick), where before it shipped product_launch at 6.16. |
| 2. Prompt corruption | Tokens that are English words, or regular inflections of one, are never corrected. The wordlist is 210k words (Webster 2nd, public domain, plus creator vocabulary) in `services/prompt_engine/data/`. | 20,000 random words plus 9,000 inflections produce 0 corrections. ride, pride, storm, hope, slot, gold, load, mild are unchanged. Real typos (fsat, agressive, nkie, podcsat) are still fixed. |
| 3. Substring matching | `shared/text.py` matches on token and phrase boundaries with inflection tolerance. It is used for pattern keywords, footage genre cues, the judge, golden checks and L6 activity scoring. | "pretty"≠pr, "shadow"≠ad, "reflection"≠flex, "deadlifts"=deadlift. |
| 4. Contract bypass | `revalidate()` rebuilds through `model_validate`. `model_copy(update=)` is banned by a static test. Dict fields are `FrozenDict`. Free text is bounded and control characters are stripped. | Invalid enum, confidence >1 and unknown fields raise; mutation raises `TypeError`. |
| 5. Orphan processes | Every tool runs in its own session. Timeout **and** cancellation send SIGTERM, then SIGKILL, and reap the group. | Tests count grandchildren after a timeout, a cancel and a Director timeout: 0. |
| 6. Duration misses | `fit_to_duration` admits or drops clips. `waterfill` solves cut lengths so Σ equals the target. Long takes are split into rhythm-sized, non-overlapping windows, interleaved in energy order. There is a new `target_duration` rule. | Six scenarios (long takes, dense, sparse, podcast, short) are all within ±5% (0.00% observed). The footage-limited case is reported, not faked. |
| 7. Pattern validation | `PatternValidation` per pattern (talking-head and reflective profiles). Low-energy runs count shots, not pieces. Deliberate release drops into "fall" acts get 1.5× the limit. New rules: `no_source_overlap`, `no_jump_cuts` (warning). | A podcast on realistic energies passes its own profile; the same timeline fails the fitness profile. |

## Findings the new evaluation surfaced (and what was done)

| Finding | Source | Action |
|---|---|---|
| Split long takes were cut at max length, not rhythm ("fast" edit with 3.4s cuts) | Golden run + judge pacing | Pieces are now sized to the act rhythm |
| Round-robin interleave created sawtooth energy in rising acts | Golden arc check | Energy-ordered interleave |
| Global camera motion was corrupted by subject motion (a pan read as "dynamic") | L6 synthetic suite, real FFmpeg | Median of 2×2 tile phase correlations (`signals.v2`); camera-motion accuracy 5/6 → 6/6 |
| "street" labeled any city footage as lifestyle | Golden genre check | Cue removed |
| No pace inferred from mood | Golden pace check | Emotion-implied pace, confidence 0.35 |
| Judge only read each clip's top role | Golden podcast hook score | Judge considers every role with confidence ≥0.5 |

**Golden results:**

| Measure | Before this cycle's fixes | After |
|---|---|---|
| Pass rate | 50% | 71% |
| Judge mean | 6.94 | 7.68 |
| Validity | 79% | 93% |

The remaining 4 failures are listed in `evaluation/README.md`. None was "fixed" by editing labels.

## Required next steps before GA (in order)

1. Run `tests/providers_live` with real Qwen, Qwen-VL and Gemini keys. Record each model's
   first-attempt schema validity, p50/p95 latency and tokens here. Fix any vendor-shape
   mismatches found.
2. Label about 200 real clips (manifest format) and run `evaluation.video_intelligence.run --with-vision --consistency`.
   Re-fit the signal thresholds if energy or quality Spearman is below 0.6.
3. Collect ≥30 human story ratings and run `agreement()`. Give the LLM judge weight only if
   it is trusted.
4. Accumulate golden and override outcomes, then `Calibrator.refit()`. Ship
   `MONTA_CALIBRATION_PATH` only once ECE ≤0.05 on held-out data.
5. Enable `MONTA_MEMORY_CONTROL_SHARE=0.1`. Keep memory only if `analyze_online` returns `keep`.
6. Commit and regenerate `benchmarks/baselines/architect.v2.json` on a clean sha. Add
   `python -m benchmarks.run --baseline …` as a required CI check.
