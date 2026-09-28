# MONTA — Layers 3–7: Intelligence Architecture

This document records the production rebuild of Layers 3–7 (Prompt
Intelligence → Context Composer → Director → Video Intelligence → Story
Architect). For each layer it covers the weakness that was found, why it was
dangerous, what replaced it, and how to migrate and operate the new code.

```
 raw prompt ──► L3 IntentEngine ─────────────┐        (lexical ∥ LLM → merged IntentAnalysis)
                                             ▼
 clip files ──► probe (ffprobe) ──► L4 ContextComposer ──► ContextPack (immutable, versioned)
                                             │
                                             ▼
                               L5 DirectorAgent: plan DAG → execute
             ┌───────────────────────────────┼───────────────────────────────┐
   analyze_clip:c1 … analyze_clip:cN (L6, parallel, non-critical)            │
             └──────────────► reconcile_intent (L3/L4: footage revises intent, refreshes priors)
                                             ▼
                               design_story (L7: pattern → adapt → assign → repair → [LLM refine])
                                             ▼
                               validate_story (L7 validator, independent re-check)
                                             ▼
                     StoryPlan ──► Layers 8–13 ──► Critic ──► (retry: redesign excluding pattern)
                                             ▲
                     Narrative Memory (L14 store) ── priors & preferences ──┘
```

Code map:

| Concern | Location |
|---|---|
| Contracts (all layers) | `shared/contracts/` (`vocab`, `explain`, `intent`, `clip`, `context`, `director`, `story`, `memory`) |
| Model providers | `shared/providers/` (`base`, `gemini`, `openai_compatible`, `resilience`, `structured`, `registry`) |
| L3 | `services/prompt_engine/` (`lexicon`, `normalizer`, `lexical_extractor`, `llm_extractor`, `engine`) |
| L4 | `services/context_composer/` (`composer`, `preferences`, `platform_rules`) |
| L5 | `orchestration/agents/director/` (`planner`, `executor`, `agent`) |
| L6 | `orchestration/agents/intelligence/` (`media`, `signals`, `vision`, `fusion`, `team`) |
| L7 | `orchestration/agents/story/` (`patterns`, `selection`, `assembly`, `validator`, `scoring`, `refiner`, `architect`) |
| Narrative memory | `memory/narrative_memory.py`, Alembic `backend/alembic/versions/0001_narrative_memory.py` |
| Composition root | `orchestration/pipeline.py`, `orchestration/graphs/master_graph.py` |
| Tests | `tests/` — run `make test` |

---

## Cross-cutting: explainability contract

**Weakness.** Layers returned bare strings and floats (`{"genre": "cinematic"}`,
`{"score": 0.0}`) with no provenance.
**Danger.** The Critic could not tell a confident decision from a default, a
retry could not target the actual cause, and nothing could be audited.
**Fix.** `shared/contracts/explain.py`:
- `Explained[T]` — value + confidence ∈ [0,1] + non-empty reasoning + evidence spans + `is_default`.
- `Score` — 0–10 value + confidence + reasoning.
- Every contract is a frozen pydantic model with a `schema_version`. Enrichment
  goes through `shared.contracts.base.revalidate`, which re-runs all validation.
  `model_copy(update=)` skipped validation and is banned by a static test. Dict fields
  are `FrozenDict`, so no layer can change another layer's output in place.

**What the Critic can inspect:**

| Question | Where |
|---|---|
| Why was a clip selected or rejected? | `StoryPlan.reasoning.clip_decisions[*].reasons` |
| Why does an act exist? | `StoryPlan.reasoning.acts[*].why_exists` / `purpose` |
| Why this pacing? | `StoryPlan.pace` (Explained) + `reasoning.pacing` + `reasoning.adaptations` |
| Why this emotion? | `StoryPlan.emotion` + `IntentAnalysis.emotion.evidence` |
| Why this pattern? | `reasoning.pattern_selection` + every `candidates[*]` score breakdown |
| What was repaired or refined? | `reasoning.repairs`, `reasoning.refinement` |
| What was degraded? | `IntentAnalysis.degraded`, `ContextPack.provenance`, `ClipIntelligence.provenance.degraded` |

---

## Cross-cutting: model provider abstraction

**Weakness.** Model access was stubbed per call site (`Qwen3Inference.generate` returned `""`),
and nothing described Qwen-VL or Gemini integration.
**Danger.** Vendor details would have leaked into business logic. There was also no retry,
rate-limit, timeout or failover story, and nothing checked what the models returned.
**Fix.**
- `ModelProvider` ABC with capability flags (`TEXT`, `VISION`, `JSON_MODE`). The
  request is checked before any network call (a text-only model rejects images).
- `GeminiProvider` (REST `generateContent`, inline images, native JSON mode, safety-block handling).
- `OpenAICompatibleProvider` covers Qwen and Qwen-VL on DashScope, self-hosted
  Qwen2.5-VL or Llama 3 behind vLLM/SGLang/TGI, and future models.
- A closed error taxonomy with `retryable` flags. HTTP status codes map to it in one place.
- `ResilientProvider`: bounded concurrency, full-jitter backoff that honours
  `Retry-After`, and a circuit breaker. `FallbackProvider`: ordered vendor failover.
- `generate_structured`: extract JSON → schema-validate → semantic-validate →
  feed the exact errors back → repair. **No unvalidated model output crosses a
  layer boundary.**
- `registry.build_providers` is the only place that knows which vendors exist.
  Misconfiguration raises at startup.

**Adding a model:** implement `generate()` in a new `ModelProvider` subclass and add one
key to `registry._build_one`. No other code changes.

---

## Layer 3 — Prompt Intelligence Engine

**Weakness.** `PromptParser.parse` returned empty strings. `GenreDetector` always returned
`"cinematic"`, `EmotionMapper` always returned `"neutral"`, and the API returned `"genre": "placeholder"`.
There were no confidence scores, no reasoning, and no handling of typos, slang, ambiguity or conflicts.
**Danger.** Every edit would have silently had the same style. The system could not tell
"I don't know" from "the creator asked for this", and would have fed defaults downstream as if they were facts.

**Fix.** A two-extractor ensemble (`IntentEngine`):
1. **Lexical extractor** (deterministic, always runs, microseconds):
   - Normalization: NFKC, symbol expansion (`b&w`, `w/`), clause boundaries (punctuation, *then*, *but*).
   - Slang expansion: *insta → instagram*, *vid → video*, *gonna*, *af*.
   - Spelling correction toward the domain vocabulary using optimal-string-alignment distance,
     so *nkie → nike*, *agressive → aggressive*, *fsat → fast*. Any valid English word or inflection
     (a 210k-word list) is never corrected, so *ride*, *pride* and *storm* stay as written.
   - Greedy longest-phrase matching over a versioned lexicon (`lexicon.v1`, ~400 cues).
     Brand references expand into whole styles (*nike* → sports, inspiring, Nike captions…).
   - Modifiers: intensifiers (*huge*, *super*, postfix *af*), negation (*not too fast* → medium),
     and a confidence penalty for spelling-corrected tokens.
   - **Arc detection:** *slow start*, *huge motivation ending* become `pacing_arc` beats,
     so they are not treated as conflicting with *aggressive cuts*.
   - Raw confidence: `share(top, incl. compatible values) × (1 − e^(−top/1.2))`. It becomes a
     calibrated confidence only once `shared.calibration` maps are fitted from labeled outcomes
     (`raw_confidence` keeps the original).
   - Ambiguity (close, incompatible candidates), hard conflicts (global slow vs fast
     with no timing qualifier), soft cross-dimension conflicts (calm + aggressive cuts),
     missing core fields (default applied, `is_default`, clarifying question), and vague-prompt detection.
2. **LLM extractor** (optional). Schema-bound through `generate_structured`; values
   outside the vocabulary are repaired, never passed through.
3. **Merge:** agreement combines confidences as `1−(1−a)(1−b)`. An LLM-only value is
   discounted ×0.85. On disagreement the more confident side wins, its confidence is
   reduced, and an `Ambiguity` is recorded. If the LLM fails, the result is lexical-only
   and marked `degraded`.
4. **`reconcile_with_footage`** (runs after Layer 6): revises a low-confidence or default genre
   and a default emotion from the vision evidence. This is how *"make it cool"* over gym footage
   becomes `fitness`. It never overrides a confident prompt, and never guesses from signal-only analysis.

**Schema.** `shared/contracts/intent.py::IntentAnalysis` (`intent.v1`). Core fields are always
present, optional fields appear only with evidence, and `needs_clarification` is a property.
**Validation.** Enums from `shared/contracts/vocab.py`, pydantic ranges, and non-empty reasoning.
**Tests.** `tests/test_prompt_engine.py`: flagship prompt, typos and slang, negation, arcs, hard and
soft conflicts, ambiguity, vague and empty prompts, decades vs durations, LLM
agree/disagree/fail/repair, footage reconciliation. `tests/test_api_prompts.py`.
**Migration.**
- `EditingIntent` (5 strings) → `IntentAnalysis`. Mapping: `pacing → pace.value`,
  `color → color_grade.value`, `target → target_platform.value`.
- The old `genre` values `transformation`/`hype`/`montage`/`vlog` are now story patterns
  or emotions, not genres. Read the pattern from `StoryPlan.story_pattern`.
- `POST /prompts/analyze` now returns `{project_id, intent, needs_clarification}`. It no longer
  accepts `target_platform`; the platform is inferred or filled from preferences.
- The backend process needs the repository root on `PYTHONPATH` (Docker images already copy
  `services/` and `shared/`).

**Production notes.**
- The lexicon is English-only. Add languages as separate lexicon tables and select by
  detected language; LLM extraction covers the gap meanwhile.
- Grow the lexicon from telemetry: log `corrections`, `missing` and LLM-only fields.
- Keep `temperature=0` for extraction.

---

## Layer 4 — Context Composer

**Weakness.** It returned a loose dict. `PreferenceEngine` and `UserContextProvider` returned
constants, and downstream agents read raw inputs directly.
**Danger.** There was no single source of truth. Personalization was a no-op, and a slow
database would have stalled or crashed every edit.

**Fix.** `ContextPack` (`context.v1`) is the **only** input to Layers 5–7. It contains: the user
prompt, the intent, `UserPreferences` (explicit + inferred, each explained), historical edits,
previous successful projects, relevant style presets (ranked with reasoning), story pattern
priors from narrative memory, the platform spec, technical video metadata, clip intelligence
(attached after Layer 6), and per-source `provenance`.
- Sources are fetched concurrently, each with its own timeout. A failure becomes
  `status="unavailable"` in provenance rather than an exception.
- Preference precedence: **the prompt → explicit settings → inferred from rated history → defaults**.
  `apply_preferences` only fills fields the prompt left unspecified.
- `enrich_with_footage` attaches Layer 6 results and, if footage changed the genre, refreshes
  the genre-dependent priors and successful projects.

**Tests.** `tests/test_memory_and_context.py`: complete pack, degradation under timeout and
exception, immutability, preference inference and precedence, prior refresh.
**Migration.** Replace `context["user_style"]`/`context["intent"]` reads with `ContextPack`
fields. `PLATFORM_RULES` is now `PLATFORM_SPECS: dict[Platform, PlatformSpec]`, adding
`youtube` and `ideal_duration_s`.
**Production notes.** `source_timeout_s` defaults to 1.5s; tune it against p99 latency from
the memory store. The history limit is 50 records per user.

---

## Layer 5 — Director Agent

**Weakness.** It returned a hard-coded list of five pending tasks. The director graph was a linear
chain of no-op nodes, and there was no dependency tracking, scheduling, retries or failure handling.
**Danger.** Nothing actually ran. When real work was wired in, one bad clip or one slow model
call would have failed the whole edit, with no visibility into why.

**Fix.**
- `DirectorPlanner` builds a typed DAG from the ContextPack: one `analyze_clip` per clip that
  isn't already analyzed (parallel, non-critical), then `reconcile_intent` (critical; runs if
  ≥50% of clips succeeded), then `design_story` (critical), then `validate_story` (critical,
  an independent check). It also produces an explained complexity estimate, a model-call and
  latency estimate, and a confidence value with its reasoning.
- `ExecutionPlan` validates itself: unique ids, known dependencies, edges consistent with
  `depends_on`, and no cycles (Kahn's algorithm).
- `TaskGraphExecutor`:
  - Runs up to `max_concurrency` ready tasks at once, with a timeout per task.
  - Retries retryable errors with backoff according to each task's `RetryPolicy`.
  - Skips a task whose dependencies fall below its tolerated success ratio.
  - When a critical task fails, it aborts the rest of the graph with explicit skip reasons.
  - Emits `TaskEvent`s for websocket progress.
  - The report status is `succeeded`, `degraded` or `failed`, with reasoning.
- The Director is deterministic by design: it only coordinates. Creative judgement stays in
  Layers 3, 6 and 7, so orchestration is reproducible and free.

**Output.** `ExecutionPlan{execution_plan, dependencies, estimated_complexity, confidence, …}`,
`ExecutionReport`.
**Tests.** `tests/test_director.py`: DAG shape, cache-aware planning, cycle and unknown-dependency
rejection, bounded parallelism, retry vs no-retry, timeouts, partial-failure tolerance,
critical abort, events, unregistered agents.
**Migration.** `director_graph.py` was removed; the master graph's `director` node runs the plan.
The `director_task_*` state keys were removed.
**Production notes.**
- The executor is in-process. To go multi-host, give the registry agents that enqueue Celery
  tasks and await their results. The DAG, policies and report don't change.

---

## Layer 6 — Video Intelligence Team

**Weakness.** The four agents (scene, action, quality, emotion) returned empty structures.
There was no provider integration, and the graph ran them sequentially despite claiming fan-out.
**Danger.** The Story Architect would have been choosing from nothing, and "quality" and
"energy" would have been invented. Adding models would have scattered vendor logic across agents.

**Fix.** For each clip: cache check, then one FFmpeg decode pass to low-resolution luma, then signals
(computed off the event loop), then keyframes, then vision (optional), then fusion, then the cache.
- **Signals** (`signals.py`, pure numpy, model-free):
  - exposure, contrast and clipping
  - Laplacian-variance sharpness
  - frame-difference motion
  - phase-correlation global camera motion: speed, jitter and direction consistency
  - hard-cut detection and the motion peak
  These drive quality, energy, lighting and camera motion.
- **Vision** (`vision.py`): any vision-capable `ModelProvider` receives time-ordered keyframes,
  and the result is validated against the `VisionObservation` schema (roles are checked for
  duplicates and emptiness). There is no vendor code in this module.
- **Fusion** (`fusion.py`), with explicit authority rules:
  - quality comes from signals, minus a penalty per issue the vision model sees
  - energy is 0.65 × signals + 0.35 × vision
  - camera motion and lighting come from signals only
  - semantics and story roles come from vision
  Without vision, roles are derived from measurements at low confidence and the reasoning
  says so.
- **Failure policy:** a decode failure raises, and the Director isolates the clip. A vision
  failure returns a signal-only result, recorded in `provenance.degraded` and **not cached**,
  so the next run retries vision.
- **Cache:** content-addressed by (fingerprint, signal version, fusion version, vision model).
  The fingerprint is sha256 over size + first and last MiB, which is O(1) I/O.

**Schema.** `shared/contracts/clip.py::ClipIntelligence` carries every field in the spec, plus
`quality_confidence`, `energy_confidence`, `peak_time_s`, `energy_curve`, `usable` and `provenance`.
**Tests.** `tests/test_intelligence.py`: motion, lighting and quality classification on synthetic
footage, cut and peak detection, ffprobe parsing with rotation, fusion with and without vision,
issue penalties, unusable clips, cache hit across re-uploads, vision failure not cached, repair of
invalid vision output, LRU eviction, and a real-FFmpeg end-to-end test (skipped when FFmpeg is absent).
**Migration.** `scene_agent`, `action_agent`, `quality_agent`, `emotion_agent` and
`intelligence_graph.py` were removed. `clip_profiles`, `scene_tags`, `actions`, `quality_scores`
and `emotions` are replaced by `ContextPack.clip_intelligence[clip_id]`.
**Production notes.**
- Signal thresholds are calibrated for 320px luma at ≤4 fps. Re-fit them on a labelled clip set
  before changing sampling, and bump `SIGNAL_VERSION`; it is part of the cache key.
- `LRUIntelligenceCache` is per process. Across workers, implement `IntelligenceCache` over
  Redis or Postgres, keyed by the same string.
- Signals are CPU-bound. At scale, run `analyze_clip` on dedicated media workers
  (the Dockerfile.worker image has FFmpeg).
- Audio energy (loudness, onsets) is not yet part of the energy score. Add it as a second
  signal family and bump the version.

---

## Layer 7 — Story Architect

**Weakness.** `design_narrative` returned `[]` and `ActBuilder` produced acts with no clips. My
earlier prototype asked an LLM to write the whole story from scratch and "validated" only
basic continuity.
**Danger.** Stories would be non-deterministic and expensive for every request, unauditable,
and structurally fragile, with nothing to learn from.

**Fix.** Selection over a pattern library, followed by deterministic assembly:
1. **Story Pattern Library** (`patterns.py`): transformation, fitness reel, motivational reel,
   travel, event, product launch, wedding, podcast highlight, tutorial and cinematic montage.
   Each pattern has typed `acts` (purpose, energy range, direction, preferred roles and emotions,
   runtime share), `required_emotions`, `pacing_curve` and `hero_moment_position`. Patterns are
   validated at import time.
2. **Select:** intent fit (0.5) + footage fit (0.35, "can these clips fill these roles?") +
   narrative-memory prior (0.15) + a creator-preference bonus. Disliked and Critic-rejected
   patterns are excluded.
3. **Adapt:**
   - the creator's arc overrides per-act pace and energy ("slow start")
   - acts merge when footage is scarce, and the hero act is never dropped
   - the target duration comes from the prompt, else the platform's sweet spot, bounded by
     the platform maximum and the available footage
4. **Assign:**
   - reject unusable and low-quality clips (relaxed when footage is scarce)
   - choose exactly one hero clip
   - fill the scarcest act first
   - distribute the rest by fit within each act's capacity
   - order clips within each act by energy direction
5. **Timeline:** cut length comes from pace × pattern curve, ×1.5 for the hero, then scaled to
   the target and clamped to the source clip. Source windows are centred on the measured motion
   peak. Crowded acts are trimmed to the target.
6. **Repair:** greedy local search (drop, swap in an unused clip, or move a clip to a neighbouring
   act). An edit is accepted only if it strictly reduces validation errors. An unfixable violation
   is **reported, not hidden**, and it lowers `story_confidence`.
7. **Optional LLM refiner** ("the model proposes, the validator disposes"): an elite-editor prompt
   proposes a new assignment, which is schema-checked, rebuilt and re-validated. It is adopted only
   if it has no more errors and the combined score doesn't drop.
8. **Score:** `story_score`, `emotion_score` and `pacing_score` are `Score` objects, with
   confidence tied to evidence quality (vision-backed vs signal-only).

**Timeline validation** (`validator.py`) checks every rule in the spec: no gaps, no overlaps,
no invalid clips (unknown, unusable or rejected), exactly one hero, no abrupt energy spikes
(threshold depends on pace: 3.0 slow → 5.5 aggressive), at most 3 low-energy cuts in a row,
per-act emotional progression (direction plus a difference from the previous act), cuts within
source duration, and no empty acts. It produces a `ValidationReport` with the rule list,
violations (segment indices and clip ids) and a summary.

**Output.** `StoryPlan` (`story.v1`): `story_pattern`, `story_confidence`, `act_assignments`,
`timeline`, `reasoning`, `story_score`, `emotion_score`, `pacing_score`, `validation`, `hero_clip_id`.
The spec shows scores as plain numbers; they are `Score` objects here to satisfy "every score
must contain confidence".
**Tests.** `tests/test_story.py`: library invariants, the flagship story (valid and fully explained),
determinism, pattern selection for wedding and travel, memory flipping a close decision, avoided and
disliked patterns, scarce footage, a single clip, no usable clips, low-quality admission, target
duration, every validator rule firing, pace-dependent spike limits, repair bridging a spike, an
unfixable spike reported honestly, and refiner adopted/rejected/unavailable.
**Migration.**
- `story_graph.py`, `act_builder.py`, `narrative.py` and the prototype `prompts.py`/`schema.py`
  were removed. Their prompt now lives in `refiner.py` as a guarded refinement pass.
- The graph state keeps `story_acts` (same shape plus `act_id`) for Layers 8–9 and the
  `timelines.story_acts` column. Layer 7's timeline now lives in **`story_timeline`**; Layer 9
  owns `timeline`. This fixes a bug where the Layer 9 stub erased the story.

---

## Narrative Memory

**Weakness.** None existed. `LearningEngine` and `UserPreferencesStore` were no-ops.
**Danger.** Every story would be generated from zero, forever. Without memory the pattern
library can't improve.

**Fix.**
- `NarrativeMemoryRecord`: `project_type`, `story_pattern`, `engagement_score`, `completion_rate`,
  `user_rating` and `successful_elements`, plus ids, pace, emotion and timestamp. The derived
  `success_index` is 0.4·engagement + 0.3·completion + 0.3·rating.
- Two stores behind one protocol: in-memory, and SQL (PostgreSQL in production, SQLite in dev).
  Tests run the same contract against both.
- `NarrativeLearner`: hierarchical Bayesian smoothing. The type-specific mean shrinks toward the
  pattern's cross-genre mean, which shrinks toward 0.5, with k=5 and confidence n/(n+k). The Story
  Architect weights priors by that confidence, so thin history can't dominate.
- Preference inference reads the same records (rating-weighted pace, the `caption:`/`music:`/`color:`
  element convention, liked and disliked patterns).

**Database migration.** `backend/alembic/versions/0001_narrative_memory.py` is additive and online-safe:
two new tables, range check constraints and three indexes. I verified upgrade and downgrade on SQLite
with zero drift from the code's schema. I added `backend/alembic/env.py` (async, reads `DATABASE_URL`),
because the repository had no Alembic environment. Run: `cd backend && alembic upgrade head`.
**Production notes.**
- Records should be written by Layer 14 once metrics settle (e.g. 72h after publish); upserting by
  `record_id` supports progressive updates.
- `pattern_stats` is an indexed GROUP BY. Past ~10M rows, maintain a `(project_type, story_pattern)`
  rollup table or materialized view on a schedule and serve priors from it.
- Priors are shared across a pattern's versions on purpose. Structural changes get a new `pattern_id`.

---

## Configuration

See `.env.example`. The key variables:

| Variable | Meaning |
|---|---|
| `MONTA_TEXT_PROVIDERS` / `MONTA_VISION_PROVIDERS` | Ordered fallback chains, e.g. `qwen,gemini` and `qwen_vl,gemini`. Empty disables that path (deterministic operation). |
| `MONTA_MEMORY_DSN` | Async SQLAlchemy DSN for narrative memory. Unset means in-memory, with a startup warning. |
| `MONTA_STORY_REFINER` | Enables the LLM refinement pass in Layer 7. |
| `MONTA_MAX_PARALLEL_CLIPS` | Director concurrency. |

## Scaling notes (millions of creators)

- **Stateless compute.** All state is in ContextPack/StoryPlan contracts or external stores,
  so any worker can run any stage.
- **Cost control.** The deterministic paths (lexical intent, signals, pattern assembly) run with
  zero model calls. Model calls are bounded per edit (≤1 text call + 1 vision call per uncached
  clip + an optional refiner call) and are listed in `ExecutionPlan.estimated_model_calls` for
  admission control.
- **Backpressure.** Provider semaphores and circuit breakers shed load from a failing vendor within
  seconds. Put a shared rate limiter (Redis token bucket) behind `ResilientProvider` once several
  processes share one API quota.
- **Checkpointing.** `orchestration/state/checkpoints.py` (outside Layers 3–7) is still a stub.
  The state is already JSON-serializable, so a LangGraph Postgres or Redis checkpointer can be
  attached directly.
- **Known limits.** The lexicon is English-only; signal thresholds are heuristic until calibrated
  on labelled data; the Critic (Layer 12) and Layers 8–13 are still stubs, so the graph currently
  exercises every story retry.

---

## Launch cycle 1 (2026-09-22)

This cycle fixed the critical findings and added the evaluation stack. Full report and acceptance
status: `docs/reviews/2026-09-22-launch-cycle-1.md`.

| Capability | Location | Entry point |
|---|---|---|
| Independent Story Judge | `evaluation/story_judge/` | used by the master graph for best-of retries |
| Golden dataset (14 projects, 7 categories) | `datasets/golden_projects/` | `datasets.loader.load_golden()` |
| Golden comparison | `evaluation/golden/` | `run_golden(projects, stack)` |
| Benchmark runner | `benchmarks/` | `python -m benchmarks.run --baseline benchmarks/baselines/architect.v2.json` |
| Layer 6 evaluation | `evaluation/video_intelligence/` | `python -m evaluation.video_intelligence.run --manifest …` |
| Calibration | `shared/calibration/` | `Calibrator.refit(store)` → `MONTA_CALIBRATION_PATH` |
| Arbitration | `shared/arbitration/` | `MONTA_ARBITRATION=consensus` |
| Observability | `shared/observability/` | `/api/v1/metrics`, `configure_logging()` |
| Memory experiment | `evaluation/memory_experiment/` | `MONTA_MEMORY_CONTROL_SHARE`, `analyze_online()` |
| Live provider tests | `tests/providers_live/` | `python -m pytest -m live tests/providers_live` |

New configuration: `MONTA_ARBITRATION`, `MONTA_JUDGE_PROVIDERS`, `MONTA_LLM_JUDGE_WEIGHT`,
`MONTA_MEDIA_ROOTS`, `MONTA_CALIBRATION_PATH`, `MONTA_MEMORY_CONTROL_SHARE`,
`MONTA_MEMORY_EXPERIMENT_SALT` (see `.env.example`).

### Alerts (thresholds on the metric catalog)

| Alert | Condition |
|---|---|
| Vision silently degraded | `rate(monta_vision_degraded_total{reason!="disabled"}) / rate(monta_clip_analysis_total) > 5%` over 10 min |
| Provider failing | `monta_provider_calls_total{outcome!="ok"}` share > 5% over 5 min, or `monta_circuit_open == 1` for > 1 min |
| Fallback overuse | `monta_fallback_used_total` > 10% of calls |
| Schema compliance drop | median `monta_structured_attempts` > 1 for a (schema, model) |
| Story validity drop | `monta_story_validation_total{passed="false"}` share > 3% |
| Duration misses | p95 of `monta_story_duration_deviation_ratio` > 0.05 |
| Quality drift | p50 of `monta_story_judge_score` drops > 0.3 vs 7-day baseline |
| Memory store degraded | `monta_context_source_total{status="unavailable"}` share > 1% |
| Cost | tokens per request p95 above budget (see `benchmarks/pricing.json`) |
