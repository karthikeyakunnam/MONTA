# Layers 3–7 — Launch-Readiness Review

**Date:** 2026-09-21 · **Scope:** Layers 3–7, shared contracts, providers, narrative memory, master-graph integration
**Verdict: NOT production-ready.** The code works on fixtures, but it has not been checked against
reality. No real model call, no real footage and no human judgment has ever touched it. One integration
defect ships a worse story than the system itself computed. The system is suitable for an internal alpha
behind a flag, and only after the Phase 1 blockers are fixed.

## Method

The 110 passing tests were **not** treated as evidence of correctness. They exercise fixtures
written by the same author as the code. Every claim below is either:
- **[P]** proven by a probe run against the code during this review (results are quoted; each
  probe becomes a regression test in Phase 1, see §3), or
- **[C]** established by reading the cited line.

**[U]** marks a risk that is plausible but unverified.

---

## 0. Headline findings (evidence first)

| # | Finding | Evidence | Severity |
|---|---|---|---|
| 1 | **The master graph ships the 4th-best story.** The Critic stub returns 0, so `should_retry` fires 3 times and each retry *excludes the best pattern*. For a gym reel the graph shipped `product_launch` (story 6.16) instead of `motivational_reel` (story 8.74), and validation still reported **passed**. | [P] `orchestration/graphs/master_graph.py:199` | **Critical** |
| 2 | **Spelling correction rewrites ordinary English into domain terms.** `ride→bride`, `pride→bride`, `storm→story`, `hope→hype`, `slot→slow`, `gold→good`, `load→road`. A "pride parade" becomes a wedding. | [P] `services/prompt_engine/normalizer.py:63` | **High** |
| 3 | **Substring matching instead of token matching.** `"a pretty product video"` hits fitness keyword `pr`; `"shadow puppet"` hits product keyword `ad`; `"lift the mood"` hits fitness. The footage genre cues have the same bug (`flex` ⊂ `reflection`, `race` ⊂ `embrace`). | [P] `orchestration/agents/story/selection.py:65`, `services/prompt_engine/engine.py:242` | **High** |
| 4 | **Contract validation is bypassable.** `model_copy(update=…)` accepted `overall_confidence=7.5` and `genre="not-a-genre"`. It is used in the intent merge, reconciliation, preferences, `enrich_with_footage` and the cache copy. | [P] | **High** |
| 5 | **"Immutable" ContextPack isn't.** The dict fields (`clip_intelligence`, `act_assignments`, `TaskSpec.params`) are mutable; `pack.clip_intelligence.clear()` succeeded. | [P] `shared/contracts/context.py`, `story.py`, `director.py` | Medium |
| 6 | **Orphaned FFmpeg processes.** When the Director timeout cancels a task, the subprocess keeps running. `_run` only kills on its *own* timeout. Retries therefore stack decoders. | [P] `orchestration/agents/intelligence/media.py:115` | **High** |
| 7 | **Signal work can't be cancelled.** `asyncio.to_thread(compute_signals)` keeps burning CPU after a timeout. | [C] `orchestration/agents/intelligence/team.py:92` | Medium |
| 8 | **Retry amplification.** Worst case per clip: executor 2 × ResilientProvider 3 × structured repair 2 = **12 vision calls**. Intent extraction can take about 75 s with no overall deadline. | [C] `planner.py:51`, `registry.py`, `structured.py`, `llm_extractor.py:113` | **High** |
| 9 | **One cut per clip.** Three 180 s takes and a 30 s request produced a **10.7 s** edit with 3 cuts. Long single takes, the most common creator upload, are nearly unusable. | [P] `orchestration/agents/story/assembly.py` (`build_timeline`) | **High** |
| 10 | **The validator ignores pattern genre.** A podcast built from talking heads (energy 2–3) *always* fails `max_low_energy_run` and the progression check. Its confidence of 0.67 still looks shippable. | [P] `orchestration/agents/story/validator.py:41-42` | **High** |
| 11 | **Story scores are self-graded.** `story_score` averages the fit values the assigner just maximized. The LLM refiner is adopted only if it beats the heuristic's *own* score (`architect.py:142`), so it can never overrule the heuristic's taste. | [C] | **High** (evaluation validity) |
| 12 | **Zero real-model validation.** Every provider test uses `MockTransport` or `ScriptedProvider`. Gemini request shape, DashScope `response_format` support for `qwen-vl-max`, model-name validity and token costs are all unverified. | [C] `tests/test_providers.py` | **Critical** |
| 13 | **Silent fleet-wide vision loss.** If a vendor rejects `response_format` (400 → non-retryable), *every* clip degrades to signal-only analysis. It is logged at WARNING and nothing alerts. | [C] `http.py`, `team.py` | **High** |
| 14 | **The narrative-memory schema forces fabricated data.** `user_rating` is required, so unrated projects must invent a rating. The upsert is DELETE+INSERT, which races under concurrency on PostgreSQL (unique violation). | [C] `shared/contracts/memory.py:37`, `memory/narrative_memory.py:175` | High |
| 15 | **Cross-tenant data sits in ContextPack.** `previous_successful_projects` holds *other creators'* `user_id`/`project_id`/elements, and the pack is persisted in graph state and returned to callers. | [C] `services/context_composer/composer.py:130` | High (privacy) |
| 16 | **No observability.** There are 8 `logger` calls in all of Layers 3–7, no correlation IDs, no metrics and no traces. Token counts and structured-repair attempts are captured and then thrown away (`vision.py:81`, `refiner.py:111`, `engine.py:83`). | [C] | **Critical** for launch |
| 17 | **FFmpeg input isn't sandboxed.** No `-protocol_whitelist`, symlinks are followed, and `MAX_CLIP_DURATION_SECONDS` (shared/constants) is never enforced in Layer 6. | [C] `media.py:127,136,150` | Medium–High |
| 18 | **Never run on the deployment runtime.** The Dockerfiles use Python 3.11; tests ran on 3.14. The real-FFmpeg test was skipped in this environment. | [C] | Medium |
| — | Energy measurements may depend on clip length (sample fps drops from 4 to 1 for long clips). The probe was inconclusive because the synthetic texture saturates. | [U] `signals.py`, `media.py:sample_luma` | Needs real-footage test |

---

## 1. Maturity scores

Scale: **0** absent · **1** stub/prototype · **2** works on fixtures · **3** validated on real inputs ·
**4** measured and monitored in production · **5** continuously evaluated and optimized.

| Component | Score | Risk | Remaining effort | Why not higher |
|---|---|---|---|---|
| Shared contracts | 2.5 | Medium | 1 eng-wk | Validation bypass via `model_copy`; mutable dicts; no algorithm version in `StoryPlan`/`ExecutionPlan`; free-text fields unbounded; enum evolution unsafe for stored rows |
| Provider abstraction | 1.5 | **Critical** | 2 eng-wks | Never called a real endpoint; retry amplification; per-process breaker with no half-open gating; no cost accounting; no shared rate limit |
| L3 Prompt Intelligence | 2.0 | High | 3 eng-wks | Proven false corrections and substring bugs; English-only with no language detection; confidence uncalibrated; no deadline; LLM self-confidence trusted |
| L4 Context Composer | 2.0 | High | 1.5 eng-wks | Cross-tenant data; prior queries uncached (2 full GROUP BYs per request); broad `except` hides bugs as "unavailable" |
| L5 Director | 2.0 | High | 2 eng-wks | Orphaned processes; no deadline budget; no persistence, resume, cancel or idempotency; inline event sink; estimates are made-up constants |
| L6 Video Intelligence | 1.5 | **Critical** | 5 eng-wks | Thresholds fit to synthetic textures only; no audio/speech; no dedup; FFmpeg path untested here; cross-tenant cache; vendor data-handling unreviewed |
| L7 Story Architect | 2.0 | **Critical** | 4 eng-wks | One cut per clip; pattern-agnostic validator; self-graded scores; arbitrary weights; clip-level rather than window-level segment energy |
| Narrative memory | 1.0 | High | 3 eng-wks | No evidence it improves outcomes; biased success index; upsert race; forced ratings; popularity feedback loop |
| Graph integration | 1.0 | **Critical** | 0.5 eng-wk | Ships the 4th-best story; no best-so-far tracking; about 7 KB of state per clip on every checkpoint |
| Observability | 0.5 | **Critical** | 3 eng-wks | See §F |
| Evaluation / benchmarking / calibration / datasets | 0 | **Critical** | 8–12 eng-wks (+ labeling budget) | See §B–§I |

**Total to production-grade:** about 33–40 engineer-weeks plus a labeling budget. Critical path:
datasets → evaluation → calibration.

---

## 2. Layer-by-layer review

Each entry names the risk category it belongs to (weakness, assumption, production, scale,
reliability, AI quality, observability, benchmarking, evaluation, security, data integrity, maintainability).

### Shared contracts — `shared/contracts/*`
- **Data integrity.** `model_copy(update)` bypasses validation [P]. *Fix:* a
  `revalidate(model, **updates) = type(model).model_validate({**model.model_dump(), **updates})` helper,
  plus a lint that bans `model_copy(update=` in `services/`, `orchestration/` and `memory/`.
- **Data integrity.** Dict fields are mutable inside frozen models [P]. *Fix:* use `Mapping` types
  with `MappingProxyType` wrappers, or tuple-of-pairs.
- **Security / scale.** `reasoning`, `Evidence.detail`, `VisionObservation.*` and LLM `reasoning`
  have no `max_length`. LLM output is echoed to clients (prompt-injection text; state bloat).
  *Fix:* bound them (e.g. 1000 chars); strip control characters; the UI must render them as text.
- **Maintainability / benchmarking.** `StoryPlan` records `pattern_version` but not the algorithm or
  weights version. `ExecutionPlan` records no pipeline version. Runs can't be reproduced or compared
  across releases. *Missing schema:* `ComponentVersions{lexicon, signals, fusion, selection_weights,
  validator_config, architect, prompt_hashes, model_ids}` on every output.
- **Data integrity.** Removing or renaming a `Genre`/`Emotion` value makes stored
  `NarrativeMemoryRecord`s fail validation *on read*, which breaks the whole query. *Fix:* read-side
  tolerance (unknown values map to a sentinel) plus a documented enum deprecation policy.
- **Assumption.** "Python 3.11 compatible" has never been executed. *Fix:* run CI on 3.11 (the Docker
  base image).

### Providers — `shared/providers/*`
- **A. Real validation absent** [C]. See §A.
- **Reliability.** Retry amplification (finding 8). *Fix:* one **deadline budget** propagated through
  a `contextvars` Deadline. Each layer spends from it, and retries stop when the remaining budget is
  below p50 latency. Only one layer may retry a given failure class: providers retry transport errors,
  structured output repairs schema errors, and the Director retries only infrastructure errors.
- **Reliability.** The `CircuitBreaker` lets unlimited concurrent calls through in half-open state
  (thundering herd) and is per-process. *Fix:* allow a single probe in half-open; Redis-backed shared
  state for fleets.
- **Reliability.** `FallbackProvider` fails over on `ProviderAuthError` on *every* request, so a
  misconfigured primary appears only as added latency. *Fix:* treat auth errors as a configuration
  error — mark the provider disabled, alert, and skip it until reconfigured.
- **Security.** `ProviderResponseError` embeds `response.text[:500]`, which may contain the user's
  prompt or images echoed back, and it gets logged. *Fix:* redact it; log a hash and length.
- **Cost / scale.** `schema_prompt()` embeds the full JSON schema, including `$defs`, in *every*
  call. The vision schema is a few thousand tokens × every clip; unmeasured. *Fix:* measure it; use
  vendor-native structured output (Gemini `responseSchema`, OpenAI-style `json_schema`) where
  supported; keep a compact hand-written schema otherwise.
- **Observability.** `ModelResponse.input_tokens/output_tokens` are never aggregated, and
  `StructuredResult.attempts` is discarded by every caller.
- **Scale.** There is no rate limiter shared across processes, so N workers × `max_concurrency=16`
  will exceed vendor quotas. *Fix:* a Redis token bucket per (vendor, model, key).
- **Maintainability.** The default `gemini-2.0-flash` may be retired [U]. Model IDs should come from
  a model registry with deprecation dates, not code defaults.

### L3 — `services/prompt_engine/*`
- **AI quality.** False spelling corrections [P]. *Fix:* use a real English wordlist (≥50k words)
  as the protected set, not the 150-word `COMMON_WORDS`. Correct only tokens that aren't English
  words. Require frequency-ratio evidence. Log every correction for review.
- **AI quality.** Substring cue matching [P]. *Fix:* token- or phrase-boundary matching everywhere
  (`\b` regex or token n-gram sets).
- **Unvalidated assumption.** The lexicon weights (0.3–1.6) and the confidence formula
  `share × (1−e^(−top/1.2))` were invented, not fit. Nothing shows 0.7 means 70% correct (§B).
- **AI quality.** The LLM's self-reported `confidence` is trusted (only discounted ×0.85 when
  uncorroborated). LLM verbalized confidence is known to be poorly calibrated. *Fix:* per-model,
  per-field calibration maps (§B), or ignore verbalized confidence entirely and use agreement plus
  historical accuracy.
- **Coverage.** English only. A Hindi, Spanish or Portuguese prompt gets *all defaults* with no
  "unsupported language" signal. *Fix:* language detection; flag it in `degraded`; route to the LLM;
  add per-language lexicons.
- **Security.** The raw prompt is sent to the LLM without delimiting or an injection policy, and LLM
  `reasoning` is echoed back to clients. Schema validation limits the damage but doesn't prevent
  output manipulation (e.g. forcing `pace=slow`). *Fix:* wrap the user text in explicit delimiters,
  add an instruction hierarchy, and make an adversarial prompt suite part of the regression set.
- **Reliability.** No end-to-end deadline (finding 8).
- **Evaluation.** There is no labeled prompt corpus, so extraction accuracy is unknown (§I).
- **Maintainability.** The 400-cue lexicon lives in Python code with no telemetry loop.
  `corrections` and `missing` are produced but never collected.

### L4 — `services/context_composer/*`
- **Security / privacy.** Other creators' records in `previous_successful_projects` [C]. *Fix:* keep
  only aggregate, anonymized signals (pattern and element statistics) in the pack; never include
  other users' IDs.
- **Scale.** `NarrativeLearner.priors` runs two unbounded `GROUP BY` scans per request, so every edit
  hits the whole table. *Fix:* a rollup table refreshed on a schedule, plus a TTL cache of priors per
  genre (priors change slowly).
- **Reliability / observability.** `_timed` catches `Exception`, so a `TypeError` from a code bug is
  reported as "source unavailable" forever. *Fix:* catch only I/O and timeout exceptions; let
  programming errors raise in non-production and alert in production.
- **AI quality.** Preference inference treats ratings as causal (a pace rated highly means the pace is
  preferred). It is confounded by content and platform. It's acceptable as a prior only if measured (§G).
- **Data integrity.** `enrich_with_footage` does not re-run `apply_preferences` after genre revision,
  so the pack can mix pre- and post-revision assumptions.
- **Missing test.** Priors refreshed under a store failure during enrichment.

### L5 — `orchestration/agents/director/*`
- **Reliability.** Orphaned subprocesses [P] and uncancellable threads [C]. *Fix:* in `_run`, catch
  `BaseException` → `proc.kill()` → `await proc.wait()` → re-raise. Run signals in a
  `ProcessPoolExecutor` with a cooperative abort flag, or make the timeout layer own the process.
- **Reliability.** Retrying deterministic `design_story` (2 attempts) is pointless; the same input
  fails the same way.
- **Reliability.** The event sink is awaited inline, so a slow websocket stalls scheduling. *Fix:*
  a bounded queue with drop-oldest and a background publisher.
- **Production.** No persistence, resume, idempotency or user cancellation. A worker crash re-runs
  everything, and the intelligence cache is per-process, so every clip is paid for again. *Fix:*
  persist `TaskResult` and outputs keyed by `(plan_id, task_id)`; resume skips succeeded tasks;
  add a cancellation token.
- **Product risk.** With `min_dependency_success_ratio=0.5`, a story can be built from half the
  footage while `PipelineResult.report.status="degraded"` sits in a field no UI reads. *Missing
  schema:* a user-facing `EditDiagnostics` (dropped clips + reasons + "retry analysis" action).
- **Unvalidated.** `estimated_latency_s`/`estimated_model_calls` use constants (12 s/clip), so
  admission control built on them would be fiction. *Fix:* fit them from traced latencies (§F).

### L6 — `orchestration/agents/intelligence/*`
- **AI quality / unvalidated assumption.** Every threshold in `signals.py` (camera motion, lighting,
  quality, energy) was tuned on synthetic noise textures. Phase correlation cannot represent zoom or
  rotation. Real phone footage has rolling shutter, autoexposure pumping and compression noise that
  inflate "motion". **Nothing shows energy or quality correlate with human judgment.**
- **AI quality.** There is no audio or speech analysis. Podcast, tutorial, interview and wedding-vows
  stories depend on *what is said*. The podcast pattern fails validation on realistic energies [P],
  and roles like "insight" and "punchline" can't be detected from pixels.
- **AI quality.** Vision sees at most 5 still frames, so short actions between samples are missed
  and motion semantics ("lockout") are inferred from stills.
- **Data integrity.** No duplicate or near-duplicate detection. The same take uploaded twice (or two
  near-identical takes) can fill two slots. *Fix:* perceptual hash over keyframes plus the
  fingerprint; cluster, then keep the best per cluster.
- **Security.**
  - The FFmpeg input is not protocol-restricted, and symlinks are followed (`os.path.isfile`).
    *Fix:* `-protocol_whitelist file,pipe`, resolve and verify the path is under the storage root,
    reject symlinks.
  - No enforcement of max duration, resolution or bitrate, so hostile or huge inputs are possible.
    *Fix:* enforce limits at probe time using `shared/constants.py`.
  - Keyframes of creators (and bystanders) go to third-party vendors, and `people` stores personal
    descriptions. *Fix:* a data-processing review, per-region vendor routing, a vendor retention
    policy, and making `people` coarse (counts and roles, not appearance).
- **Security.** The intelligence cache is global across tenants, keyed by a partial-content
  fingerprint (size + first/last MiB). A crafted collision could poison another user's analysis
  [U, low likelihood]. *Fix:* full-content hash computed at upload (Layer 2 already streams the
  file), and tenant-scoped keys for anything containing vision output.
- **Scale.** The cache is per-process LRU, so N workers means N× vision spend. *Fix:* shared store.
- **Unverified.** Energy may depend on clip length through the fps adaptation [U]. Needs the
  real-footage fixture (§E).

### L7 — `orchestration/agents/story/*`
- **Product / AI quality.** One segment per clip [P]. *Fix:* **sub-clip segmentation** — split
  long clips at detected cuts and energy-curve peaks into candidate moments, each with its own energy
  window, and let the assigner choose moments, not files.
- **AI quality.** The validator is pattern-agnostic [P]. *Fix:* move `ValidationConfig` into
  `StoryPattern` (per-pattern low-energy threshold, run length, spike limit), with global defaults.
- **AI quality.** Segment `energy` is clip-level `energy_score`, not the energy of the chosen source
  window (`assembly.py:202`). `energy_curve` exists but isn't used, so spike and progression checks
  test the wrong number.
- **Evaluation validity.** The scores are circular [C]. `story_score`/`emotion_score`/`pacing_score`
  are internal *objective functions*, not quality measurements, and must not be reported as quality.
  The refiner acceptance rule (`architect.py:142`) makes the LLM unable to express taste the
  heuristic doesn't share. *Fix:* adopt LLM refinements based on an independent judge (§D) or on
  online A/B results, never on the heuristic's own score.
- **Unvalidated.** Selection weights `0.5/0.35/0.15` (`selection.py:81`), fit weights, confidence
  weights (`architect.py:83`), the ×1.5 hero multiplier, `BASE_CUT_S` — all invented.
- **Missing rules.** Same clip in consecutive cuts (jump cut), shot-type monotony (≥3 close-ups in a
  row), identical source window reused, hero placed before any setup on short-form, caption-safe
  hero duration.
- **Graph integration (Critical).** Finding 1. *Fix:* until Layer 12 exists, `should_retry` must
  return `render` unless `critic_feedback` explicitly names a story defect. Retries must keep the
  **best-so-far** plan and ship it if a retry scores lower on the independent evaluator.

### Narrative memory — `memory/narrative_memory.py`
- **Data integrity.** Forced `user_rating` [C]. *Fix:* make `user_rating`/`completion_rate`/
  `engagement_score` optional, and compute `success_index` only over the signals present, with
  per-signal availability flags.
- **Data integrity / reliability.** The DELETE+INSERT upsert races [C]. *Fix:* `INSERT … ON CONFLICT
  (record_id) DO UPDATE` (dialect-specific `insert` from `sqlalchemy.dialects.postgresql`/`sqlite`).
- **AI quality (bias).** `engagement_score` is raw engagement, so large creators dominate "success"
  and patterns win through audience size, not story quality. *Fix:* normalize engagement against each
  creator's trailing baseline (a z-score or percentile of their own last N posts).
- **AI quality (feedback loop).** Priors pick winners, winners get more data, and alternatives never
  get tested (popularity lock-in). *Fix:* exploration — Thompson sampling over the posterior, with
  exploration rate capped per creator.
- **Unvalidated.** Nothing shows memory improves outcomes (§G).

---

## A. Real model validation — **absent**

| Item | Status |
|---|---|
| Real Qwen / Qwen-VL / Gemini calls | **None.** All tests use `httpx.MockTransport` or `ScriptedProvider`. |
| Failure / timeout / retry behavior | Unit-tested against simulated errors only. Real vendor error bodies, 429 semantics, and `Retry-After` formats are unverified. |
| Failover | Unit-tested ordering only; never exercised against a real outage. |
| Schema compliance rate | Unknown — how often does each model emit valid `VisionObservation` on the first try? |
| Cost / latency | Unknown per call, per clip, per edit. |

**Design: provider contract suite** (`tests/providers_live/`, marker `@pytest.mark.live`, nightly and
on provider-config change, never on PRs):
1. **Contract tests** per (vendor, model): text, JSON mode, 1 and 6 images, max-size image, empty
   response handling, safety block, 4xx with a malformed request, invalid key → `ProviderAuthError`,
   forced low timeout → `ProviderTimeoutError`.
2. **Schema-compliance benchmark.** For 200 frozen prompts and 200 frozen keyframe sets, record the
   first-attempt validity rate, repair success rate, p50/p95 latency and tokens. Gate: first-attempt
   validity ≥ 95%, and within 99.5% after one repair.
3. **Fault injection.** A `ChaosProvider` wrapper (latency, 5xx, 429 with Retry-After, truncated JSON)
   in staging, with assertions on the end-to-end deadline and degradation reporting.
4. **Failover drill.** Staging runs with the primary key revoked. Assert that edits complete, the
   fallback share metric reaches 100%, and an alert fires within 5 minutes.
5. **Recorded fixtures.** Real responses captured (redacted) into `tests/fixtures/providers/<vendor>/`
   so that offline tests replay *real* shapes instead of hand-written ones.

---

## B. Confidence calibration — **absent**

Is 0.9 reliable? **Unknown.** Is it measurable? **Not currently.** No outcomes are recorded. Is it
tracked historically? **No.**

**Design:**
```
decision emitted ──► DecisionLog (append-only)
        │                 {decision_id, component, field, value, raw_confidence,
        │                  calibrated_confidence, component_versions, model_ids, ts}
        ▼
outcome arrives ───► OutcomeLog {decision_id, outcome: correct|incorrect|partial,
                                 source: human_label | user_override | implicit_accept, ts}
        ▼
nightly job ──► per (component, field, version): reliability diagram, ECE, Brier,
                → fit isotonic regression (Platt if n < 500) → CalibrationMap v{n}
        ▼
runtime ──► calibrated = CalibrationMap[component, field].apply(raw)   (raw kept for audit)
```

**Outcome sources:**
- For intent fields: a human label on the prompt corpus (§I). The user changing the field in the
  UI counts as incorrect; the user keeping it and exporting counts as weakly correct.
- For clip fields: human labels on the clip set.
- For `story_confidence`: whether the story was exported without re-edit, and the pairwise-judge
  outcome (§D).

**Missing schemas:** `DecisionRecord`, `OutcomeRecord`, `CalibrationMap{component, field, version,
method, bins, fitted_at, n}`.
**Missing code:** `shared/calibration/` (fit/apply/store), with an `Explained.raw_confidence` field
added (the existing `confidence` becomes calibrated).
**Acceptance:**
- ECE ≤ 0.05 per core field on held-out data.
- A decision labeled 0.9 is correct 85–95% of the time (reliability bin check).
- Recalibration runs weekly.
- An ECE regression of more than 0.03 alerts.

---

## C. Benchmarking framework — **absent**

MONTA currently can't be compared with human editors, previous MONTA versions or alternative models.

**Design (`benchmarks/`):**
```
benchmarks/
  suites/{golden,regression,stress}.yaml     # project ids + expected properties
  runners/pipeline_runner.py                  # runs a MONTA build/config over a suite
  runners/reference_loader.py                 # loads human-editor reference edits
  metrics/{intent,clip,story,cost}.py
  report.py                                   # BenchmarkReport JSON + HTML diff vs baseline
```
- **Arms:** `monta@<git-sha>` × `config` (providers, weights, refiner on/off) vs `human_reference`
  vs `monta@baseline`.

**Metrics:**

| Stage | Metric |
|---|---|
| Intent | Per-field accuracy / macro-F1 vs labels; missing-field recall; conflict-detection precision and recall |
| Clip | Activity F1, shot-type accuracy, Spearman correlation of energy and quality vs human 0–10 ratings, role top-1/top-3 accuracy |
| Story (reference-based) | Clip-selection precision/recall vs the human edit; Kendall τ of order; hero agreement; act-boundary agreement (±1 cut); duration error |
| Story (preference) | Pairwise human preference vs baseline and vs human edit, with Bradley–Terry scores and 95% CI |
| Operational | p50/p95 latency per stage, model calls, tokens, $ per edit, degradation rate |

- **Gates:** no metric regresses beyond its CI versus baseline; any gated metric drop fails CI for
  changes under `services/prompt_engine`, `orchestration/agents/**` or `shared/providers`.
- **Model bake-off:** the same suite run with `MONTA_VISION_PROVIDERS=qwen_vl` vs `gemini` vs both,
  reporting quality and cost per edit.

---

## D. Story quality evaluation — **not objectively measurable today**

The current scores are optimization targets (finding 11). **Design — a four-tier evaluator:**

1. **Structural** (exists; must become pattern-aware): the validator.
2. **Reference-based** (needs golden projects): the §C story metrics against 1–3 human edits per
   project.
3. **Rubric judge.** An LLM judge from a *different* model family than any refiner, given the
   storyboard (keyframes per cut + prompt + pattern), scores 1–5 on anchored rubrics:

   | Dimension | Anchor (5) |
   |---|---|
   | Narrative coherence | Each cut follows from the previous; acts are recognizable |
   | Emotional progression | Intended arc (setup → payoff) is felt; ending lands |
   | Pacing quality | Cut rhythm matches request; no dead air or whiplash |
   | Clip relevance | Every cut earns its place; no off-topic or duplicate shots |
   | Story completeness | Setup, escalation, payoff all present; hero moment unmistakable |

   The judge is **trusted only after** it reaches Spearman ρ ≥ 0.6 with human ratings on 200 labeled
   storyboards, and it is re-validated each time the judge model changes.
4. **Human and online.** A weekly pairwise human panel (≥3 raters, measured inter-rater agreement,
   Krippendorff's α ≥ 0.6). Online signals: export rate, re-edit rate, manual clip swaps, completion
   rate and rewatch rate of published edits.

**Missing schemas:** `StoryEvaluation{plan_id, evaluator, evaluator_version, dimension_scores,
rationale, ts}`, `PairwiseJudgment{a_plan_id, b_plan_id, winner, rater_id, dimension}`.

---

## E. Failure catalog — **partial, and mostly synthetic**

| Scenario | Current coverage | Gap |
|---|---|---|
| Blurry clip | Synthetic flat frame (`tests/test_intelligence.py`) | Real optical and motion blur never measured |
| Dark clip | Synthetic ×0.12 luma | Real low-light noise (which inflates "motion") untested |
| Corrupted clip | Fake backend raises | Truncated MP4, bad moov atom, zero-byte, wrong extension through real FFmpeg — untested |
| Duplicate clips | **None** | No dedup logic exists |
| Missing clip | `probe` file-not-found | File deleted *after* probe (between L4 and L6) untested |
| Contradictory prompt | Lexical tests | LLM path under contradiction untested |
| Ambiguous prompt | Lexical tests | No labeled set measuring ambiguity-detection precision |
| Empty project | Raises `PipelineError` | UI contract for the error untested |
| Large project | Story-only probe: 300 clips in 0.03 s | Pipeline-level 200 clips / 60 min untested (memory, cost, state size ≈ 7 KB/clip per checkpoint) |
| Not yet catalogued | — | VFR, rotated / mixed orientation, 4K HEVC 10-bit, audio-only file, 0.3 s and 60 min clips, non-English prompt, emoji-only prompt, prompt-injection prompt, all clips unusable, vendor total outage, memory store down |

**Framework (`tests/failure_catalog/`):**
- `catalog.yaml`: `{id, category, generator, prompt, expected: {status, degraded_contains, clip_decisions, validation_rules}}`.
- `generate_media.py`: builds real files with FFmpeg lavfi — `testsrc` plus `gblur`, `eq=brightness`,
  `noise`, `setpts` (VFR), `transpose`, truncation by byte cut, a duplicated file, and a stored 1-hour
  synthetic file.
- `test_catalog.py`: parametrized over the catalog; runs the real `FFmpegMediaBackend` in the CI
  Docker image (FFmpeg present); asserts the expected outcomes.
- Every production incident adds a catalog row before the fix merges.

---

## F. Production monitoring — **absent**

**Current state:** 8 log calls, unstructured, with no project, plan or task IDs. No metrics. No
traces. Model token counts are discarded.

**Design:**
```
request ─► contextvars: project_id, user_id(hash), plan_id, task_id, deadline
   │
   ├── OpenTelemetry traces: edit → stage(L3..L7) → task → provider.call → structured.attempt
   │      attrs: model_id, tokens_in/out, latency, attempt, outcome, cache_hit
   ├── Prometheus metrics (below)
   ├── JSON logs (structlog) with the same IDs; prompt text never logged (length + hash only)
   └── DecisionLog / OutcomeLog (§B)  → warehouse → dashboards, calibration, eval
```

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `monta_stage_duration_seconds` | histogram | stage | p95 over SLO |
| `monta_task_total` | counter | agent, status | failure rate > 2% |
| `monta_provider_calls_total` | counter | provider, model, outcome | error rate > 5% (5 min) |
| `monta_provider_latency_seconds` | histogram | provider, model | — |
| `monta_provider_tokens_total` | counter | provider, model, direction | daily budget |
| `monta_structured_attempts` | histogram | schema, model | first-try validity < 95% |
| `monta_circuit_state` | gauge | provider | open > 1 min |
| `monta_fallback_used_total` | counter | from, to | > 10% |
| `monta_vision_degraded_ratio` | gauge | — | > 5% (**catches finding 13**) |
| `monta_intent_default_field_ratio` | gauge | field | drift vs 7-day baseline |
| `monta_intent_llm_disagreement_ratio` | gauge | field | drift |
| `monta_story_validation_failed_total` | counter | rule, pattern | > 3% |
| `monta_story_pattern_selected_total` | counter | pattern, genre | distribution drift |
| `monta_story_confidence` | histogram | pattern | — |
| `monta_cache_hit_ratio` | gauge | cache | < 30% at steady state |
| `monta_cost_per_edit_usd` | histogram | — | p95 over budget |
| `monta_memory_source_status_total` | counter | source, status | unavailable > 1% |

- **Agent performance:** per-agent success rate, latency, retries, and output-quality proxies
  (validation failures; judge score by agent version).
- **Model performance:** per-model schema validity, latency, cost, field accuracy against labels,
  and calibration ECE — on a single dashboard.

---

## G. Narrative memory validation — **not measurable today**

**Design:**
1. **Offline counterfactual.** On the golden and eval suites, run with priors enabled vs neutral
   priors. Measure the pattern-selection flip rate and, for flips only, the judge and human-preference
   delta.
2. **Online holdout.** 10% of creators (stable hash) get neutral priors. Primary metric:
   creator-baseline-normalized completion rate. Secondary: export rate, re-edit rate. Minimum
   detectable effect is computed up front; run until a power of 0.8.
3. **Logging.** Record the selection with and without the prior on every edit (cheap, since both
   are deterministic), so flip analysis needs no replay.
4. **Guardrails.** Exploration share, pattern-diversity entropy per genre, and a check against a
   creator-size-normalized success index.

**Acceptance:** memory stays on only if the holdout shows a statistically significant lift of
≥ +2% normalized completion, with no loss of pattern-diversity entropy.

---

## H. Multi-model arbitration — **minimal**

**Today:**
- Layer 3 merges *one* LLM with the lexicon (rule: higher self-reported confidence wins).
- `FallbackProvider` is **failover**, not arbitration: it never consults a second model when the
  first answers.
- Layer 6 uses a single vision model.
- Disagreement between Qwen and Gemini is not resolvable because they are never both asked.

**Design: `shared/arbitration/`:**
```
cascade:  ask cheapest reliable model M1
          if calibrated_conf(M1, field) ≥ τ_field  → accept
          else ask M2 (different family)
               agree            → accept, conf = combined calibrated
               disagree         → weighted vote by historical per-field accuracy
                                   (from DecisionLog/OutcomeLog, Beta posterior per model×field)
               margin < δ        → escalate: tie-breaker model OR mark Ambiguity + policy default
record ArbitrationRecord {field, candidates[(model, value, raw, calibrated)], rule, winner, margin}
```
- Weights come from **measured** per-model, per-field accuracy, never from self-reported confidence.
- Cost bound: M2 is called only below τ; τ is tuned to hit a target quality/cost point on the
  benchmark.
- It applies to the L3 fields, L6 fields (shot_type, emotion, roles) and the L7 refiner/judge (the
  judge must be a different family from the refiner).
- **Missing schemas:** `ModelReliability{model_id, component, field, alpha, beta, updated_at}`,
  `ArbitrationRecord`.

---

## I. Dataset strategy — **none exists**

| Dataset | Purpose | Size (initial → target) | Labels | Refresh |
|---|---|---|---|---|
| **Golden projects** | Release gate; human-editor reference | 60 → 150 projects (10 per pattern, stratified by platform and footage condition) | Prompt intent, per-clip labels, 2 human reference edits, rubric scores | Frozen; versioned; additions only |
| **Regression set** | Never re-break a fixed failure | Grows with every incident and catalog item | Expected behavior assertions | Continuous |
| **Evaluation set** | Model and algorithm comparison | 1,000 → 5,000 projects | Intent labels + judge-validated story scores | Quarterly, new sample |
| **Prompt corpus** | L3 accuracy and calibration | 3,000 real (anonymized) prompts, ≥10% non-English, ≥10% adversarial | Per-field value, ambiguity/conflict flags; 2 annotators + adjudication | Monthly sample from production |
| **Clip label set** | L6 accuracy and calibration | 8,000 clips | Activity, shot type, lighting, quality 0–10, energy 0–10, roles; 3 annotators; report Krippendorff α | Quarterly |
| **Pairwise preferences** | D-tier 4 and judge validation | 2,000 pairs/quarter | Winner per dimension | Weekly panel |

- **Governance:**
  - Explicit creator consent for evaluation use; PII scrubbing (faces blurred for vendor-evaluated
    subsets where required).
  - Retention limits.
  - Storage: object storage plus a manifest with content hashes (DVC or lakeFS). Splits are
    assigned by creator ID to prevent leakage.
- **Labeling:** written guidelines with anchored examples, qualification tests for annotators, 10%
  gold-question audit, and disagreement adjudication.
- **Missing code:** `datasets/manifest.py`, `datasets/loaders.py`, `datasets/schema.py`
  (`GoldenProject`, `ClipLabel`, `PromptLabel`, `ReferenceEdit`).

---

## 3. Missing tests (consolidated)

- Live provider contract suite (§A); recorded real-response replay tests.
- Python 3.11 CI job; a real-FFmpeg job in the Docker image (currently skipped).
- Normalizer: an English-wordlist no-false-correction property test (10k common words → 0 corrections).
- Token-boundary matching tests for pattern keywords and footage cues.
- Contract integrity: every `model_copy(update=)` path re-validates (a property test with invalid
  updates → raises).
- Cancellation: a Director timeout leaves no child processes (`psutil` child count == 0).
- End-to-end deadline: a slow provider at every layer and the edit returns within budget as degraded.
- Retry-budget test: the maximum number of provider calls per clip ≤ configured cap.
- Master graph: the Critic stub must not cause retries; best-so-far retained across retries.
- Podcast, tutorial and interview stories on realistic low energies pass pattern-aware validation.
- Long-take segmentation (after implementing): 3×180 s → a duration within ±15% of the target.
- Duplicate and near-duplicate clip handling.
- Concurrent narrative-memory upserts on PostgreSQL (testcontainers).
- Cross-tenant: the ContextPack contains no foreign `user_id`.
- Prompt injection: an adversarial prompt suite cannot force out-of-policy fields or leak system
  prompts into `reasoning`.
- The failure catalog (§E); a large-project pipeline test (200 clips): memory ceiling, state size,
  latency.

## 4. Missing schemas (consolidated)

`ComponentVersions` · `DecisionRecord` / `OutcomeRecord` · `CalibrationMap` · `StoryEvaluation` ·
`PairwiseJudgment` · `ModelReliability` · `ArbitrationRecord` · `EditDiagnostics` (user-facing) ·
`BenchmarkReport` · `GoldenProject` / `ClipLabel` / `PromptLabel` / `ReferenceEdit` ·
`CandidateMoment` (sub-clip segments) · a per-pattern `ValidationConfig` on `StoryPattern` ·
`Explained.raw_confidence`.

## 5. Missing validation (consolidated)

Re-validation on every update · bounded free-text · FFmpeg path/protocol/limits · a max prompt
token budget per model · per-pattern timeline rules · window-level segment energy · jump-cut and
shot-monotony rules · duplicate-clip rules · memory outcome ranges when signals are partial ·
startup validation that configured model IDs exist (a live `models.list` probe).

---

## 6. Roadmap

### PHASE 1 — Critical blockers (target: 3 weeks, 3 engineers)
**Goal:** the system never ships a worse output than it computed, never corrupts or leaks data,
never leaks processes, and has been proven against real models and real footage at least once.

| Work | Files |
|---|---|
| Disable Critic-driven retries until L12 exists; keep best-so-far | `orchestration/graphs/master_graph.py` (`should_retry`, `build_story_node`) |
| Token-boundary matching; wordlist-protected spelling correction | `orchestration/agents/story/selection.py:65`, `services/prompt_engine/engine.py:242`, `services/prompt_engine/normalizer.py`, new `services/prompt_engine/data/english_words.txt` |
| Revalidating update helper; ban `model_copy(update=)` in L3–7 | `shared/contracts/explain.py` (helper), `services/prompt_engine/engine.py`, `services/context_composer/{composer,preferences}.py`, `orchestration/agents/intelligence/team.py` |
| Kill subprocess on cancellation; process-pool signals | `orchestration/agents/intelligence/media.py:_run`, `team.py` |
| End-to-end deadline budget; single-owner retries | new `shared/deadline.py`; `shared/providers/{resilience,structured}.py`; `orchestration/agents/director/{executor,planner}.py`; `services/prompt_engine/engine.py` |
| FFmpeg sandbox + input limits | `orchestration/agents/intelligence/media.py`, `shared/constants.py` |
| Remove cross-tenant records from ContextPack; bound free text | `services/context_composer/composer.py`, `shared/contracts/{context,explain,clip}.py` |
| Upsert via ON CONFLICT; optional outcome signals | `memory/narrative_memory.py`, `shared/contracts/memory.py`, new Alembic revision `0002` |
| Live provider contract suite (Qwen, Qwen-VL, Gemini) + recorded fixtures | new `tests/providers_live/`, `tests/fixtures/providers/` |
| CI: Python 3.11 + FFmpeg Docker job | new `.github/workflows/layers-3-7.yml` (or equivalent) |
| Minimum observability: correlation IDs, structured logs, token/cost counters, vision-degraded alert | new `shared/observability/{context,metrics,logging}.py`; instrument `shared/providers/http.py`, `executor.py`, `team.py` |

- **Tests to add:** graph no-retry and best-so-far; wordlist property test; boundary-matching tests;
  invalid-update property tests; no-orphan-process test; deadline test; retry-cap test; FFmpeg
  protocol rejection; cross-tenant test; concurrent upsert (testcontainers PostgreSQL); live suite
  (nightly).
- **Metrics:** `monta_provider_calls_total`, `…_tokens_total`, `monta_vision_degraded_ratio`,
  `monta_task_total`, `monta_stage_duration_seconds`.
- **Acceptance criteria:**
  - The graph output equals the pipeline's best plan on the golden gym fixture.
  - 0 false corrections over the 10k-word list.
  - 0 child processes after a cancelled analysis.
  - Worst-case provider calls per clip ≤ 3.
  - Edits return within their deadline under chaos latency.
  - The live suite is green against all three models: first-attempt schema validity measured and
    recorded, with results in the repo.
  - A 3.11 CI run is green, and the FFmpeg test runs (not skipped).
  - An alert fires when vision degradation exceeds 5% in staging.

### PHASE 2 — Production hardening (target: 5 weeks)
**Goal:** reliable under real traffic and real failures, with every degradation visible to operators
and users.

| Work | Files |
|---|---|
| Full OpenTelemetry tracing + metrics catalog (§F) + dashboards | `shared/observability/*`, all agents |
| DecisionLog / OutcomeLog pipeline | new `shared/decisions/`, Alembic `0003`, hooks in `engine.py`, `fusion.py`, `architect.py` |
| Task persistence, resume, cancellation, idempotency | `orchestration/agents/director/executor.py`, new `orchestration/state/task_store.py` |
| Shared intelligence cache (tenant-scoped, full-content hash from L2) | `orchestration/agents/intelligence/team.py`, new `…/redis_cache.py`, `services/media_gateway/upload.py` (hash) |
| Shared rate limiter + fleet circuit breaker + auth-error disablement | `shared/providers/resilience.py`, new `shared/providers/ratelimit.py` |
| Failure catalog with real generated media (§E) | new `tests/failure_catalog/` |
| Duplicate/near-duplicate detection | new `orchestration/agents/intelligence/dedup.py`, `assembly.py` |
| User-facing `EditDiagnostics` | `shared/contracts/director.py`, `orchestration/pipeline.py`, `backend/app/api/v1/*` |
| Prior rollups + TTL cache | `memory/narrative_memory.py`, Alembic `0004` |
| API auth + per-user rate limits on `/prompts/analyze` | `backend/app/api/v1/prompts.py`, `backend/app/dependencies.py` |
| Vendor data-processing review; region routing; `people` minimization | `shared/providers/registry.py`, `shared/contracts/clip.py`, `orchestration/agents/intelligence/vision.py` |

- **Tests:** every catalog row; resume after simulated crash; cancellation; rate-limit fairness;
  auth-disable behavior; dedup; diagnostics surfaced through the API.
- **Metrics:** the full §F table; SLOs: L3 p95 < 1.5 s, L6 p95 per clip < 20 s (vision) and < 4 s
  (signals), L7 p95 < 500 ms, edit success ≥ 99.5%.
- **Acceptance criteria:**
  - All catalog rows pass in CI with real FFmpeg.
  - A worker kill mid-plan resumes without re-running succeeded tasks.
  - The 24 h staging soak at target RPS meets the SLOs with zero orphaned processes.
  - Every degraded edit shows diagnostics in the API response.

### PHASE 3 — Quality optimization (target: 8–10 weeks, including labeling)
**Goal:** replace every invented constant with a measured one, and prove quality against humans.

| Work | Files |
|---|---|
| Datasets: golden, prompt corpus, clip labels, pairwise (§I) | new `datasets/`, labeling guidelines in `docs/labeling/` |
| Benchmark framework + CI gates (§C) | new `benchmarks/` |
| Four-tier story evaluator + validated judge (§D) | new `evaluation/`, `shared/contracts/evaluation.py` |
| Confidence calibration (§B) | new `shared/calibration/`, `shared/contracts/explain.py` (`raw_confidence`), all emitters |
| Re-fit signal thresholds and fusion weights on clip labels; add audio energy | `orchestration/agents/intelligence/{signals,fusion}.py`, new `audio.py` |
| Speech/transcript understanding (Whisper) for podcast, tutorial, vows | new `orchestration/agents/intelligence/speech.py`, `shared/contracts/clip.py` |
| Sub-clip moment segmentation | new `orchestration/agents/intelligence/moments.py`, `shared/contracts/clip.py` (`CandidateMoment`), `orchestration/agents/story/assembly.py` |
| Per-pattern validation config; window-level energy; jump-cut and monotony rules | `shared/contracts/story.py`, `orchestration/agents/story/{patterns,validator,assembly}.py` |
| Fit selection and fit weights from benchmark (grid/Bayesian search, held-out) | `orchestration/agents/story/{selection,assembly,architect}.py` → a `weights.yaml` versioned in `ComponentVersions` |
| Refiner accepted by independent judge, not self-score | `orchestration/agents/story/architect.py:_refine` |
| Multi-language lexicons + language detection | `services/prompt_engine/{lexicon,normalizer,lexical_extractor}.py` |

- **Tests:** benchmark regression gates; judge-validation test (ρ ≥ 0.6 on the held-out set);
  calibration ECE test; podcast/tutorial golden projects; long-take golden projects.
- **Metrics:**
  - Intent per-field macro-F1.
  - Clip energy/quality Spearman ρ vs humans.
  - Story pairwise win rate vs baseline and vs human.
  - ECE per field; export and re-edit rates.
- **Acceptance criteria:**
  - Intent macro-F1 ≥ 0.85 on core fields.
  - Energy ρ ≥ 0.6 and quality ρ ≥ 0.6 vs human labels.
  - ECE ≤ 0.05.
  - Judge ρ ≥ 0.6 with humans.
  - MONTA's story wins or ties ≥ 45% against human editor references on the golden set, and ≥ 55%
    against the previous release.
  - Podcast and tutorial golden projects pass validation.
  - Long-take projects are within ±15% of the requested duration.

### PHASE 4 — Scale readiness (target: 6 weeks)
**Goal:** the cost, latency and learning loop hold at millions of creators.

| Work | Files |
|---|---|
| Distributed execution: agents as Celery tasks behind the same registry | `orchestration/agents/director/executor.py`, `workers/` |
| Multi-model arbitration cascade (§H) | new `shared/arbitration/`, `services/prompt_engine/engine.py`, `orchestration/agents/intelligence/vision.py` |
| Narrative-memory holdout + exploration (§G) | `memory/narrative_memory.py`, `orchestration/agents/story/selection.py`, experiment config |
| Creator-baseline normalization of engagement | `memory/narrative_memory.py`, `shared/contracts/memory.py`, Alembic `0005` |
| Checkpoint slimming: store ContextPack/StoryPlan by reference (object store) instead of inline state | `orchestration/graphs/master_graph.py`, `orchestration/state/{graph_state,checkpoints}.py` |
| Vendor-native structured output; prompt/schema token reduction | `shared/providers/{gemini,openai_compatible,structured}.py`, `vision.py` |
| Cost-aware admission control from fitted latency/cost models | `orchestration/agents/director/planner.py` |
| Load test: 500 clips/project, 10× peak RPS | new `benchmarks/load/` |

- **Tests:** load and soak; arbitration correctness on disagreement fixtures; exploration-rate
  bounds; checkpoint size ceiling (≤ 50 KB/state regardless of clip count).
- **Metrics:** cost per edit p50/p95; vision calls per edit; arbitration escalation rate;
  memory-holdout lift; pattern-diversity entropy; checkpoint bytes.
- **Acceptance criteria:**
  - Cost per edit is within budget at p95.
  - The arbitration cascade matches best-single-model quality at ≤ 60% of dual-model cost.
  - The memory holdout shows a significant lift, or memory is disabled.
  - A 500-clip project completes within the SLO without an OOM.
  - Horizontal scaling is linear to 10× the load-test RPS.

---

## 7. Launch decision matrix

| Stage | Allowed when |
|---|---|
| Internal dogfood | Phase 1 complete |
| Closed beta (≤1k creators) | Phase 2 complete, plus the golden-set benchmark run once with results published |
| General availability | Phase 3 acceptance met, and Phase 4 load and cost criteria met for the forecast traffic tier |
