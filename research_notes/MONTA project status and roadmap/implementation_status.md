# MONTA Project — Implementation Status and Completion Level

## Overall Completion: ~45-50%

MONTA is a 14-layer AI video editing platform. The intelligence core (Layers 3-7) is substantially implemented with 199 passing tests, but Layers 8-13 remain largely stubbed in the master graph, and several peripheral modules (vision backends, speech, local models, memory/Qdrant) are placeholder-only. The project is suitable for internal alpha, not production.

---

## Git History and Development Progress

### Commit Timeline (6 total commits)

| Commit | Date | Description |
|--------|------|-------------|
| `e2bf4d1` | Latest | fix(intent): sanitize small model LLM outputs + human evaluation agreement tests |
| `422ddaf` | -- | feat(pipeline): Milestone 3 -- End-to-end pipeline, story-to-timeline, render orchestrator, preflight checks, real test suite |
| `aa12246` | -- | feat(media): Milestone 2 -- FFmpeg media engine, Timeline IR, EditPlanValidator, OutputValidator |
| `6db0651` | -- | feat(runtime): Milestone 1 -- Local AI Runtime, HardwareDetector, Ollama adapter, offline mode |
| `ff7b576` | -- | Update: refactor services, add datasets, benchmarks, evaluation, tests, orchestration agents |
| `153a511` | -- | Initial scaffold: 14-layer MONTA AI video editing platform |

**Pattern:** Three milestones of bottom-up infrastructure work (runtime -> media -> pipeline), followed by a large refactor commit and one bug-fix commit. Active development with recent fixes for critical issues found in launch-readiness review.

---

## Module-by-Module Status

### Layer 1 -- Experience Layer (Frontend) -- ~85% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Next.js web app | **Functional** | `frontend/web/src/app/page.tsx` (165 lines) -- project creation, prompt input, platform selection, project list |
| API client | **Functional** | `frontend/web/src/lib/api.ts` (312 lines) -- full typed API client with error handling, X-Request-ID tracking |
| Upload component | **Functional** | `frontend/web/src/components/Uploader.tsx` |
| WebSocket progress | **Functional** | `frontend/web/src/hooks/useProgressSocket.ts` |
| Project detail page | **Functional** | `frontend/web/src/app/projects/[id]/page.tsx` |
| Desktop app (Electron) | **Not started** | `frontend/desktop/` contains only a README |

### Layer 2 -- Media Gateway -- ~80% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Upload API | **Functional** | `backend/app/api/v1/upload.py` |
| Upload service | **Functional** | `backend/app/services/upload_service.py` |
| Validation | **Functional** | `services/media_gateway/validation.py` |
| Storage | **Functional** | `services/media_gateway/storage.py` -- local storage with incoming sweep |
| Metadata/probe | **Functional** | `services/media_gateway/metadata.py` |
| Config | **Functional** | `services/media_gateway/config.py` |

### Layer 3 -- Prompt Intelligence Engine -- ~75% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| IntentEngine (lexical + LLM ensemble) | **Functional** | `services/prompt_engine/engine.py` |
| Lexicon (~400 cues) | **Functional** | `services/prompt_engine/lexicon.py` |
| Normalizer (NFKC, slang, spelling) | **Functional** | `services/prompt_engine/normalizer.py` -- 210k-word English wordlist |
| Lexical extractor | **Functional** | `services/prompt_engine/lexical_extractor.py` |
| LLM extractor | **Functional** | `services/prompt_engine/llm_extractor.py` -- schema-bound via `generate_structured` |
| API endpoint `/prompts/analyze` | **Functional** | `backend/app/api/v1/prompts.py` |
| Multi-language support | **Not started** | English-only; no language detection |
| Telemetry loop for lexicon growth | **Not started** | `corrections`/`missing` produced but never collected |

### Layer 4 -- Context Composer -- ~80% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| ContextComposer | **Functional** | `services/context_composer/composer.py` -- concurrent source fetch with per-source timeout |
| Platform rules | **Functional** | `services/context_composer/platform_rules.py` -- YouTube, Instagram, TikTok, YouTube Short |
| Preferences | **Functional** | `services/context_composer/preferences.py` -- explicit + inferred, precedence rules |
| ContextPack contract | **Functional** | `shared/contracts/context.py` -- immutable, versioned, provenance per source |
| Cross-tenant data leak | **Known issue** | `previous_successful_projects` contains other creators' IDs (privacy risk, documented in review) |

### Layer 5 -- Director Agent -- ~75% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| DirectorPlanner | **Functional** | `orchestration/agents/director/planner.py` -- typed DAG from ContextPack |
| TaskGraphExecutor | **Functional** | `orchestration/agents/director/executor.py` -- bounded concurrency, retries, events |
| ExecutionPlan validation | **Functional** | Cycle detection (Kahn's), unique IDs, dependency checking |
| Agent registry | **Functional** | `orchestration/agents/director/agent.py` -- 4 registered agents |
| Persistence/resume | **Not started** | No task persistence, no resume after crash, no cancellation token |
| Estimate accuracy | **Placeholder** | `estimated_latency_s`/`estimated_model_calls` use constants (12s/clip) |

### Layer 6 -- Video Intelligence Team -- ~70% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| FFmpeg media backend | **Functional** | `orchestration/agents/intelligence/media.py` -- probe, luma decode, keyframe extraction |
| Signal analysis (numpy) | **Functional** | `orchestration/agents/intelligence/signals.py` -- exposure, contrast, sharpness, motion, camera motion (phase correlation), cut detection |
| Vision analyzer | **Functional** | `orchestration/agents/intelligence/vision.py` -- keyframe-based, schema-validated |
| Fusion engine | **Functional** | `orchestration/agents/intelligence/fusion.py` -- authority rules, degradation tracking |
| Intelligence cache | **Functional** | `orchestration/agents/intelligence/team.py` -- LRU, content-addressed |
| FFmpeg process cleanup | **Functional** | SIGTERM->SIGKILL group kill, orphan prevention (fixed in launch cycle 1) |
| Audio/speech analysis | **Not started** | No loudness/onsets; podcast/tutorial/vows depend on audio |
| Duplicate detection | **Not started** | No perceptual hashing or near-duplicate detection |
| Shared cache (Redis) | **Not started** | Per-process LRU only |

### Layer 7 -- Story Architect -- ~75% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Story Pattern Library | **Functional** | `orchestration/agents/story/patterns.py` -- 10 patterns, validated at import |
| Selection | **Functional** | `orchestration/agents/story/selection.py` -- intent fit (0.5) + footage fit (0.35) + memory prior (0.15) |
| Assembly | **Functional** | `orchestration/agents/story/assembly.py` -- adapt, assign, timeline build, repair |
| Timeline Validator | **Functional** | `orchestration/agents/story/validator.py` -- per-pattern validation config |
| Story Scoring | **Functional** | `orchestration/agents/story/scoring.py` -- story/emotion/pacing as `Score` objects |
| LLM Refiner | **Functional** | `orchestration/agents/story/refiner.py` -- guarded, schema-checked, adopted only if improved |
| Architect | **Functional** | `orchestration/agents/story/architect.py` -- orchestrates the full design pass |
| Sub-clip segmentation | **Not started** | One cut per clip; long takes nearly unusable (documented as critical finding) |
| Selection weights | **Placeholder** | 0.5/0.35/0.15 invented, not fit from data |

### Layer 8 -- Style Engine -- ~30% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Style presets | **Partial** | `services/style_engine/presets.py` -- basic preset matching |
| Style mapper | **Stub** | `services/style_engine/mapper.py` -- preset matching only; LLM path is TODO |
| Master graph node | **Stub** | `orchestration/graphs/master_graph.py:197` -- `apply_style_node` returns `{"style_config": {}}` |

### Layer 9 -- Timeline Generator -- ~60% Complete (service), 0% (graph)

| Component | Status | Evidence |
|-----------|--------|----------|
| StoryToTimelineTranslator | **Functional** | `services/timeline_generator/translator.py` (169 lines) -- deterministic StoryPlan -> TimelineIR |
| Clip selector | **Exists** | `services/timeline_generator/clip_selector.py` |
| Sequencer | **Exists** | `services/timeline_generator/sequencer.py` |
| Transition planner | **Exists** | `services/timeline_generator/transition_planner.py` |
| Master graph node | **Stub** | `orchestration/graphs/master_graph.py:203` -- `generate_timeline_node` returns `{"timeline": []}` |

### Layer 10 -- Audio Studio -- ~5% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Music generator | **Stub** | `services/audio_studio/music_generator.py` -- returns `""` with TODO |
| DJ remix | **Stub** | `services/audio_studio/dj_remix.py` -- TODO |
| Mashup | **Stub** | `services/audio_studio/mashup.py` -- TODO |
| Sound FX | **Stub** | `services/audio_studio/sound_fx.py` -- TODO |
| Tempo matcher | **Stub** | `services/audio_studio/tempo_matcher.py` -- TODO |
| Voiceover | **Stub** | `services/audio_studio/voiceover.py` -- TODO |
| Beat extension | **Stub** | `services/audio_studio/beat_extension.py` -- TODO |
| Master graph node | **Stub** | `orchestration/graphs/master_graph.py:209` -- `process_audio_node` returns `{"audio_config": {}}` |
| API endpoints | **Stub** | `backend/app/api/v1/audio.py` -- all 5 endpoints are TODO |

### Layer 11 -- Edit Executor -- ~85% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| FFmpeg Compiler | **Functional** | `services/edit_executor/compiler.py` (315 lines) -- full filtergraph, transitions, xfade, hardware encoders |
| FFmpeg Executor | **Functional** | `services/edit_executor/executor.py` (163 lines) -- process groups, progress parsing, timeout, cancellation |
| Edit Plan Validator | **Functional** | `services/edit_executor/validator.py` (236 lines) -- path containment, time ranges, continuity, transitions |
| Color grading | **Partial** | `services/edit_executor/color_grading.py` -- exists but minimal |
| Caption engine | **Stub** | `services/edit_executor/caption_engine.py` -- TODO |
| Audio sync | **Stub** | `services/edit_executor/audio_sync.py` -- TODO |
| Transitions | **Partial** | `services/edit_executor/transitions.py` -- exists |
| Master graph node | **Stub** | `orchestration/graphs/master_graph.py:215` -- `execute_edits_node` returns `{"edit_result": {}}` |

### Layer 12 -- Critic System -- ~10% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Critic evaluator | **Stub** | `orchestration/agents/critic/evaluator.py` -- TODO: LLM evaluation |
| Critic scoring | **Stub** | `orchestration/agents/critic/scoring.py` -- TODO: weighted average |
| Critic retry logic | **Stub** | `orchestration/agents/critic/retry_logic.py` -- TODO: targeted retry plan |
| Critic graph | **Stub** | `orchestration/graphs/critic_graph.py` -- all TODO |
| Master graph node | **Stub** | `orchestration/graphs/master_graph.py:221` -- `evaluate_node` returns `{"critic_score": 0.0}` |
| Story Judge (independent) | **Functional** | `evaluation/story_judge/` -- heuristic, LLM, composite judges with independence test |

### Layer 13 -- Render Farm -- ~70% Complete (service), 0% (graph)

| Component | Status | Evidence |
|-----------|--------|----------|
| MediaRenderer | **Functional** | `services/render_farm/renderer.py` (121 lines) -- hardware-aware, CPU fallback, progress |
| FFmpeg Compiler | **Functional** | Shared with Layer 11 |
| FFmpeg Executor | **Functional** | Shared with Layer 11 |
| Output Validator | **Functional** | `services/render_farm/output_validator.py` (245 lines) -- ffprobe validation |
| Format exporter | **Partial** | `services/render_farm/format_exporter.py` -- exists |
| Multi-res | **Partial** | `services/render_farm/multi_res.py` -- exists |
| Master graph node | **Stub** | `orchestration/graphs/master_graph.py:227` -- `render_node` returns `{"render_output": {}}` |

### Layer 14 -- Learning Layer -- ~30% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Narrative memory store | **Functional** | `memory/narrative_memory.py` -- SQL + in-memory backends, Bayesian smoothing |
| Preference inference | **Functional** | Reads narrative records, rating-weighted pace, element convention |
| Qdrant vector store | **Stub** | `memory/qdrant_client.py` -- all TODO |
| Embeddings | **Stub** | `memory/embeddings.py` -- TODO: sentence-transformers |
| Feedback loop | **Stub** | `memory/feedback_loop.py` -- TODO |
| Learning engine | **Stub** | `memory/learning_engine.py` -- TODO |
| User preferences store | **Stub** | `memory/user_preferences.py` -- TODO: Qdrant vector + PostgreSQL structured |

### Shared Infrastructure -- ~70% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Contracts (10 modules) | **Functional** | `shared/contracts/` -- frozen pydantic models with `schema_version`, `Explained[T]`, `FrozenDict` |
| Model provider abstraction | **Functional** | `shared/providers/` -- Gemini, OpenAI-compatible, ResilientProvider, FallbackProvider |
| Hardware detector | **Functional** | `shared/hardware/detector.py` -- Apple/NVIDIA/Intel detection |
| Calibration | **Functional** | `shared/calibration/` -- isotonic/Platt fitting, ECE/MCE/Brier |
| Arbitration | **Functional** | `shared/arbitration/` -- concurrent multi-model, Beta posterior reliability |
| Observability | **Functional** | `shared/observability/` -- 30 metrics, tracing, JSON logs, correlation IDs |
| Local AI runtime | **Functional** | `shared/local_runtime/` -- Ollama, llama.cpp, MLX adapters, preflight |

### Vision Modules -- ~10% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| OpenCV scene detection | **Stub** | `vision/opencv/scene_detection.py` -- TODO |
| OpenCV motion analysis | **Stub** | `vision/opencv/motion_analysis.py` -- TODO |
| OpenCV quality scoring | **Stub** | `vision/opencv/quality_scoring.py` -- TODO |
| Qwen-VL inference | **Stub** | `vision/qwen_vl/inference.py` -- TODO |
| Qwen-VL model | **Stub** | `vision/qwen_vl/model.py` -- TODO |
| Gemini Vision client | **Stub** | `vision/gemini_vision/client.py` -- TODO |

### Speech Modules -- ~5% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Whisper transcriber | **Stub** | `speech/transcriber.py` -- TODO |
| Alignment | **Stub** | `speech/alignment.py` -- TODO |
| Caption generator | **Stub** | `speech/caption_generator.py` -- TODO |

### Local Model Inference -- ~10% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Qwen 3 inference | **Stub** | `models/qwen3/inference.py` -- TODO |
| Llama 3 inference | **Stub** | `models/llama3/inference.py` -- TODO |
| Model manager | **Stub** | `models/model_manager.py` -- TODO: dynamic loading, routing |
| Ollama adapter | **Functional** | `shared/local_runtime/adapters/ollama.py` |

### Backend API -- ~65% Complete

| Endpoint | Status | Evidence |
|----------|--------|----------|
| `/health` | **Functional** | `backend/app/api/v1/health.py` |
| `/upload` | **Functional** | `backend/app/api/v1/upload.py` |
| `/projects` | **Functional** | `backend/app/api/v1/projects.py` |
| `/prompts/analyze` | **Functional** | `backend/app/api/v1/prompts.py` |
| `/timeline` | **Partial** | `backend/app/api/v1/timeline.py` -- rejects retired stub endpoint |
| `/audio` | **Stub** | `backend/app/api/v1/audio.py` -- 5 TODO endpoints |
| `/render` | **Stub** | `backend/app/api/v1/render.py` -- TODO: Celery dispatch |
| `/export` | **Stub** | `backend/app/api/v1/export.py` -- TODO: platform formatting, presigned URL |
| `/metrics` | **Functional** | `backend/app/api/v1/metrics.py` -- Prometheus |
| WebSocket progress | **Functional** | `backend/app/api/websockets/progress.py` |
| Render service | **Stub** | `backend/app/services/render_service.py` -- returns `"render_placeholder"` |

### Workers (Celery) -- ~75% Complete

| Component | Status | Evidence |
|-----------|--------|----------|
| Pipeline task | **Functional** | `workers/tasks/pipeline_tasks.py` (354 lines) -- full L3->L7 + timeline + render |
| Render task | **Functional** | `workers/tasks/render_tasks.py` -- MediaRenderer via Celery |
| Export task | **Unknown** | `workers/tasks/export_tasks.py` -- not examined in detail |
| Incoming sweep | **Functional** | `workers/celery_app.py` -- hourly cleanup task |

---

## TODO/FIXME/STUB Marker Summary

| Category | Count | Key Locations |
|----------|-------|---------------|
| TODO comments | ~60+ | Concentrated in Layers 8-10, 12, backend services, vision, speech, memory |
| Stub functions | ~25+ | `master_graph.py` (6 nodes), `render_service.py`, `audio.py`, `export.py` |
| NotImplemented patterns | ~15+ | `vision/`, `speech/`, `models/`, `memory/` (Qdrant/embeddings) |

**Critical TODO locations (master graph):**
- `master_graph.py:197` -- `apply_style_node`: returns empty dict
- `master_graph.py:203` -- `generate_timeline_node`: returns empty timeline
- `master_graph.py:209` -- `process_audio_node`: returns empty config
- `master_graph.py:215` -- `execute_edits_node`: returns empty result
- `master_graph.py:221` -- `evaluate_node`: returns `critic_score: 0.0`
- `master_graph.py:227` -- `render_node`: returns empty output

---

## Test Coverage Status

### Test Suite: 199 tests passing (+ 9 live-provider tests deselected)

| Test Module | Coverage Area |
|-------------|---------------|
| `test_prompt_engine.py` | L3: flagship prompt, typos, slang, negation, arcs, conflicts, ambiguity, LLM paths |
| `test_api_prompts.py` | L3 API endpoint |
| `test_memory_and_context.py` | L4: ContextPack, degradation, immutability, preferences |
| `test_director.py` | L5: DAG, caching, cycles, parallelism, retries, timeouts, partial-failure |
| `test_intelligence.py` | L6: motion, lighting, quality, cuts, peaks, fusion, cache, vision failure |
| `test_story.py` | L7: patterns, determinism, selection, validator rules, repair, refiner |
| `test_story_judge.py` | Evaluation: judge independence, heuristic/LLM/composite |
| `test_timeline_ir.py` | L9: Timeline IR contracts |
| `test_story_to_timeline.py` | L9: StoryPlan -> TimelineIR translation |
| `test_ffmpeg_compiler.py` | L11: FFmpeg command compilation |
| `test_edit_plan_validator.py` | L11: Timeline IR validation |
| `test_media_execution_real.py` | L6/L11: Real FFmpeg end-to-end |
| `test_golden_and_benchmark.py` | Golden dataset regression + benchmark runner |
| `test_providers.py` | Providers: Gemini, OpenAI-compatible, resilience, structured |
| `test_calibration.py` | Calibration: isotonic/Platt fitting, ECE |
| `test_arbitration.py` | Arbitration: multi-model consensus |
| `test_observability.py` | Metrics catalog, tracing |
| `test_local_ai_runtime.py` | Local AI: Ollama adapter, preflight, hardware |
| `test_offline_mode.py` | Offline mode: deterministic operation without providers |
| `test_pipeline.py` | End-to-end L3->L7 pipeline |
| `test_m3_end_to_end_real.py` | M3: Full pipeline with real render |
| `test_m3_golden_scenarios.py` | M3: Golden project scenarios |
| `test_m3_failures_and_offline.py` | M3: Failure handling, offline mode |
| `test_video_eval.py` | L6 evaluation suite |
| `test_memory_experiment.py` | Memory experiment: offline/online analysis |
| `test_process_cleanup.py` | FFmpeg process lifecycle |
| `test_text_normalization.py` | L3: normalization, spelling correction |
| `test_duration_and_profiles.py` | L7: duration fitting, pattern profiles |
| `test_contract_integrity.py` | Static test: bans `model_copy(update=)` |
| `test_hardware_detector.py` | Hardware detection |

**Test coverage gaps (documented in launch readiness review):**
- No live provider tests executed (no credentials)
- No Python 3.11 CI (Docker uses 3.11, tests ran on 3.14)
- No FFmpeg in CI Docker (real-FFmpeg test skipped)
- No prompt injection tests
- No cross-tenant data tests
- No concurrent upsert tests
- No cancellation/orphan-process tests (added later)
- No large-project (200+ clip) tests
- No duplicate clip tests

---

## Benchmark and Evaluation Results

### Golden Dataset (14 projects, 7 categories)

| Measure | Result |
|---------|--------|
| Pass rate | 71% (10/14) -- up from 50% before launch cycle 1 fixes |
| Judge overall | 7.68 -- up from 6.94 |
| Validity | 93% -- up from 79% |
| Duration check | 100% |
| Hero check | 100% |

**Open failures (4 projects):**
- `cinematic_a24_mood`: energy spike
- `cinematic_trailer_style`: pattern ends on resolve act instead of hero payoff
- `travel_tokyo_night`: genre inferred as cinematic
- `wedding_romantic_film`: pace inferred as medium; mood confidence below threshold

### Layer 6 Synthetic Evaluation (6 clips, real FFmpeg 7.0)

| Measure | Result |
|---------|--------|
| Lighting accuracy | 5/5 |
| Camera-motion accuracy | 6/6 |
| Quality Spearman vs labels | 0.87 |
| Re-encode consistency MAE | 0.11 |

**Not yet measured:** activity, emotion, scene, roles (need human-labeled real footage + vision provider)

### Benchmark Runner

- Baseline: `benchmarks/baselines/architect.v2.json` (git sha `153a511-dirty`)
- Regression gates: story quality (>0.20 drop), alignment (>0.25), pass rate (>5pt), p95 runtime (>50%+0.5s)
- Status: Functional but baseline needs regeneration on clean commit

---

## Maturity Scores (from Launch Readiness Review)

Scale: 0=absent, 1=stub/prototype, 2=works on fixtures, 3=validated on real inputs, 4=monitored in production, 5=continuously evaluated

| Component | Score | Remaining Effort |
|-----------|--------|------------------|
| Shared contracts | 2.5 | 1 eng-wk |
| Provider abstraction | 1.5 | 2 eng-wks |
| L3 Prompt Intelligence | 2.0 | 3 eng-wks |
| L4 Context Composer | 2.0 | 1.5 eng-wks |
| L5 Director | 2.0 | 2 eng-wks |
| L6 Video Intelligence | 1.5 | 5 eng-wks |
| L7 Story Architect | 2.0 | 4 eng-wks |
| Narrative memory | 1.0 | 3 eng-wks |
| Graph integration | 1.0 | 0.5 eng-wk |
| Observability | 0.5 | 3 eng-wks |
| Evaluation/benchmark/calibration | 0 (pre-cycle-1) -> now ~1.5 | 8-12 eng-wks (was) |

**Total estimated to production-grade (at review time):** 33-40 engineer-weeks + labeling budget

**After Launch Cycle 1:** Many Phase 1 items addressed. Layers 3-7 are at ~2.0-2.5 maturity (work on fixtures, partially validated). Layers 8-13 remain at 0-1.0.

---

## End-to-End Pipeline Status

The Milestone 3 commit added an end-to-end pipeline that runs Layers 3->7->timeline->render in the Celery worker (`workers/tasks/pipeline_tasks.py`). This is a **functional real pipeline** that:

1. Loads project + clips from DB
2. Runs L3 intent analysis
3. Runs L4 context composition
4. Runs L5 Director (which executes L6 Video Intelligence + L7 Story Architect)
5. Persists intent, story plan, judge verdict, per-clip intelligence
6. Generates Timeline IR (L9 translator)
7. Compiles FFmpeg execution plan (L11 compiler)
8. Renders final MP4 via MediaRenderer (L13)
9. Validates output with ffprobe
10. Updates project status to COMPLETE

**However**, the master graph (`orchestration/graphs/master_graph.py`) still has stubs for Layers 8-13. The end-to-end path uses the pipeline + worker directly, not the master graph. The master graph is wired but would produce empty outputs for Layers 8-13.

---

## Key Architectural Findings

1. **Two parallel execution paths exist:** The master graph (LangGraph, stubbed L8-13) and the Celery worker pipeline (functional L3->render). The worker is the real production path.

2. **Layers 3-7 are the mature core:** These have been through a launch-readiness review, a launch cycle with 7 critical bug fixes, and have 199 passing tests with golden dataset regression.

3. **Service-layer code exists for L8-L13 but is disconnected:** Style engine, timeline generator, edit executor, and render farm have real implementations, but the master graph nodes that should call them are stubs.

4. **Vision/speech/local-model modules are almost entirely placeholder:** These are the `vision/`, `speech/`, and `models/` directories. The production path uses `shared/providers/` (Gemini, OpenAI-compatible) instead.

5. **Backend API is partially wired:** Upload, projects, prompts, health, metrics, and WebSocket progress are functional. Audio, render, and export are stubs.

6. **Frontend is functional for the core loop:** Create project -> upload clips -> view progress -> see results. Desktop (Electron) is not started.

---

## Gaps and Unknowns

1. **Live provider integration never executed:** All provider tests use mocks. No real Qwen, Qwen-VL, or Gemini API call has been made. Schema compliance, latency, and cost are unknown.

2. **No human evaluation data:** Story judge validity, memory impact, and calibration ECE require human ratings that do not exist yet.

3. **No production deployment evidence:** No Docker images built, no CI/CD pipeline, no staging environment evidence.

4. **Signal thresholds uncalibrated on real footage:** L6 signal analysis was tuned on synthetic noise textures. Real phone footage may behave differently.

5. **Sub-clip segmentation not implemented:** Long takes (most common upload type) produce single cuts, making them nearly unusable for short-form content.

6. **Audio pipeline entirely missing:** No music generation, no speech-to-text, no beat matching, no audio analysis. This makes podcast/tutorial/wedding patterns non-functional despite having story patterns defined.
