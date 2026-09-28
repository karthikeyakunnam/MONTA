# MONTA Benchmarks

```bash
python -m benchmarks.run                                                    # current code → benchmark_report.json
python -m benchmarks.run --baseline benchmarks/baselines/architect.v2.json  # vs previous version (exit 1 on regression)
python -m benchmarks.run --variants current,env_models                      # vs configured models
python -m benchmarks.run --save-baseline benchmarks/baselines/<version>.json
```

**Variants** (`benchmarks/variants.py`):

| Variant | What it runs |
|---|---|
| `current` | This code, with no model calls |
| `current_no_memory` | Memory ablation |
| `env_models` | Providers from the environment: `MONTA_TEXT_PROVIDERS`, `MONTA_ARBITRATION=consensus`, `MONTA_JUDGE_PROVIDERS`, `MONTA_STORY_REFINER` |

To compare alternative models, run `env_models` under different environments, for example
`MONTA_TEXT_PROVIDERS=qwen`, then `=gemini`, then `=qwen,gemini` with consensus. Keep each report.

**Metrics** (`benchmarks/report.py`):
- `story_quality`, `prompt_alignment`, `narrative_score`: independent Story Judge means.
- `golden_pass_rate` and per-check rates.
- `validity_rate`.
- `runtime_p50_s` / `runtime_p95_s`.
- Tokens per model, and `cost_usd`.

**Cost** is computed only from `benchmarks/pricing.json`:

```json
{"gemini:gemini-2.0-flash": {"input": 0.10, "output": 0.40}}
```

The values are USD per 1M tokens, taken from your vendor contract. When a model has no price,
the report says so (`cost_usd: null`) instead of guessing.

**Regression rules:**

| Metric | Regression when |
|---|---|
| Story quality | drops more than 0.20 |
| Alignment, narrative | drop more than 0.25 |
| Golden pass rate, validity rate | drop more than 5 points |
| p95 runtime | rises more than 50% **and** more than 0.5s |

**Baselines:** `baselines/architect.v2.json` is this release on dataset
`6115d2ed…` (git sha `153a511-dirty`, because the work was uncommitted). Regenerate it
after committing, so the baseline records a clean sha.
