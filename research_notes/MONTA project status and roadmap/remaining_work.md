# MONTA Project — Remaining Work Items, Known Gaps, and Roadmap

## What features are planned but not yet implemented?

### Takeaway
Layers 3–7 (Prompt Intelligence, Context Composer, Director, Video Intelligence, Story Architect) are implemented and tested. Layers 8–13 (Style Engine, Timeline Generator, Audio Studio, Edit Executor, Critic System, Render Farm) are stubs with TODO placeholders. Layer 14 (Learning Layer) has two parallel implementations: a production narrative memory system and a separate stub-based memory module. The master graph wires all 14 layers but Layers 8–13 nodes return empty dicts.

### Cited Findings
- **Layers 8–13 are entirely stubs.** `orchestration/graphs/master_graph.py:195-228` — `apply_style_node`, `generate_timeline_node`, `process_audio_node`, `execute_edits_node`, `evaluate_node`, and `render_node` all contain `# TODO: Call <service>_service` and return empty dicts. — [Source](orchestration/graphs/master_graph.py)
- **Layer 12 Critic returns 0.0 always.** `orchestration/agents/critic/evaluator.py:37` — `# TODO: Multi-dimensional evaluation using LLM`; returns `{"overall_score": 0.0, "scores": {}, "feedback": [], "should_retry": False}`. — [Source](orchestration/agents/critic/evaluator.py)
- **Layer 12 retry logic is a stub.** `orchestration/agents/critic/retry_logic.py:19` — `# TODO: Analyze feedback to create targeted retry plan`; returns empty focus/skip areas. — [Source](orchestration/agents/critic/retry_logic.py)
- **Checkpointing is unimplemented.** `orchestration/state/checkpoints.py:7-8` — `# TODO: Implement Redis-backed checkpointing for graph state` and `# TODO: Allow resuming failed pipelines from last checkpoint`. — [Source](orchestration/state/checkpoints.py)
- **Layer 10 Audio Studio is entirely stubbed.** All modules in `services/audio_studio/` contain TODO comments: `music_generator.py` (TODO: Integrate with music generation model), `dj_remix.py` (TODO: Beat detection, loop creation, FX application), `mashup.py` (TODO: Beat-align tracks, crossfade, blend), `voiceover.py` (TODO: TTS model integration), `beat_extension.py` (TODO: Detect loop points, extend seamlessly), `sound_fx.py` (TODO: Mix FX into audio; AI sound effect generation), `tempo_matcher.py` (TODO: Detect BPM, analyze cut points, adjust tempo; Beat detection algorithm). — [Source](services/audio_studio/)
- **Layer 11 Edit Executor is entirely stubbed.** `services/edit_executor/ffmpeg_runner.py` has 6 TODOs (run ffmpeg -ss, concat file, xfade filter, audio merge, LUT application, scale filter). `transitions.py` (TODO: Implement various transition types), `caption_engine.py` (TODO: Whisper transcription, style-to-font mapping, FFmpeg drawtext/ASS), `color_grading.py` (TODO: Apply LUT via FFmpeg, FFmpeg curves filter), `audio_sync.py` (TODO: Beat detection + cut point alignment). — [Source](services/edit_executor/)
- **Layer 13 Render Farm is stubbed.** `services/render_farm/format_exporter.py` (TODO: Crop/resize for aspect ratio, Trim to max duration, Re-encode for platform specs), `multi_res.py` (TODO: FFmpeg resize and encode), `output_validator.py` (pass). — [Source](services/render_farm/)
- **Layer 8 Style Engine is a stub.** `services/style_engine/mapper.py:27` — `# TODO: Use LLM for custom style generation`. — [Source](services/style_engine/mapper.py)
- **Layer 9 Timeline Generator is a stub.** `services/timeline_generator/clip_selector.py:9` — `# TODO: Score clips against theme using emotion/scene/action tags`; `transition_planner.py:11` — `# TODO: Use style config and mood changes to assign transitions`. — [Source](services/timeline_generator/)
- **Backend API stubs.** `backend/app/services/render_service.py` — all 4 methods are TODO stubs returning placeholders. `backend/app/api/v1/audio.py` — all 5 endpoints are TODO stubs. `backend/app/api/v1/export.py` — both endpoints are TODO stubs. — [Source](backend/app/services/render_service.py)
- **Worker stubs.** `workers/tasks/export_tasks.py:13-15` — TODO: Apply platform-specific settings, Resize/crop for aspect ratio, Enforce duration limits. — [Source](workers/tasks/export_tasks.py)
- **memory/ directory contains a parallel stub Layer 14 implementation.** `memory/learning_engine.py` — all methods are TODO stubs (pass). `memory/feedback_loop.py` — TODO stub. `memory/user_preferences.py` — TODO stubs returning empty defaults. `memory/qdrant_client.py` — TODO stubs (no real Qdrant integration). `memory/embeddings.py` — TODO stubs returning empty lists. These appear to be an older/parallel implementation superseded by the production `memory/narrative_memory.py`. — [Source](memory/)
- **Speech module stubs.** `speech/transcriber.py`, `speech/alignment.py`, `speech/caption_generator.py` — minimal implementations. — [Source](speech/)
- **Vision module stubs.** `vision/gemini_vision/`, `vision/qwen_vl/`, `vision/opencv/` — directory structure exists but implementations are minimal. — [Source](vision/)
- **Local model stubs.** `models/qwen3/`, `models/llama3/` — directory structure exists. `models/model_manager.py` — minimal. — [Source](models/)
- **Planned datasets not yet created.** `datasets/README.md` — Prompt corpus (3,000 prompts), Clip label set (8,000 clips), Judge-validation ratings (≥30 to trust a judge, 2,000 pairs/quarter), Memory history. Schemas exist but data must come from production with consent. — [Source](datasets/README.md)
- **Planned evaluation components not yet built.** From the launch readiness review: Failure catalog (`tests/failure_catalog/`), Live provider contract suite (`tests/providers_live/` — exists but never run with real credentials), Four-tier story evaluator (partially built — heuristic + LLM + composite judges exist, but human validation not measured), Confidence calibration (mechanism built, real ECE unknown), Multi-model arbitration (built). — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Planned infrastructure.** `orchestration/state/checkpoints.py` — Redis-backed checkpointing. `shared/providers/ratelimit.py` — shared rate limiter. `orchestration/agents/intelligence/dedup.py` — duplicate detection. `orchestration/agents/intelligence/speech.py` — speech/transcript understanding. `orchestration/agents/intelligence/moments.py` — sub-clip moment segmentation. `orchestration/agents/intelligence/redis_cache.py` — shared intelligence cache. `orchestration/state/task_store.py` — task persistence. `shared/decisions/` — DecisionLog/OutcomeLog pipeline. `shared/arbitration/` — multi-model arbitration cascade (partially built). — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)

### Inferences
- The project has a clear bifurcation: Layers 3–7 are production-grade with 199 passing tests, while Layers 8–13 are architectural placeholders. The master graph "works" end-to-end but produces empty outputs for style, timeline, audio, edit, critic, and render stages.
- The `memory/` directory contains a dead/parallel code path. The production narrative memory system is in `memory/narrative_memory.py` with SQL stores, Alembic migrations, and Bayesian learning. The other files (`learning_engine.py`, `feedback_loop.py`, `user_preferences.py`, `qdrant_client.py`, `embeddings.py`) are stubs that should either be removed or integrated.
- The evaluation stack (golden dataset, story judge, benchmarks, calibration, observability, arbitration) was built in Launch Cycle 1 and is functional but lacks real-world validation data.

### Gaps
- No GitHub issues are accessible (gh CLI not authenticated, no remote configured).
- No project boards found.
- The `.aider.chat-history.md` file was listed in the root directory but could not be read (may have been deleted or moved).

---

## What are the known bugs or issues?

### Takeaway
The launch readiness review (2026-09-21) documented 18 findings. Launch Cycle 1 (2026-09-22) fixed the critical bugs. Remaining known issues are: 4 golden test failures, unvalidated real-model integration, uncalibrated confidence scores, and several production-hardening gaps.

### Cited Findings
- **4 golden test failures remain.** `evaluation/README.md` — `cinematic_a24_mood` (energy spike), `cinematic_trailer_style` (pattern ends on resolve act instead of hero payoff; genre label debatable), `travel_tokyo_night` (genre inferred as cinematic), `wedding_romantic_film` (pace inferred as medium; mood confidence below inference threshold). Pass rate is 71% (10/14). — [Source](evaluation/README.md)
- **Real model integration never executed.** `docs/reviews/2026-09-22-launch-cycle-1.md` — Criterion 2 "Real model integration tested" is **Not met** (built; unexecuted). No Qwen, DashScope, or Gemini credentials in the environment. The live suite (`tests/providers_live/`) has 9 tests that skip without credentials. — [Source](docs/reviews/2026-09-22-launch-cycle-1.md)
- **LLM judge validity not measured.** `evaluation/README.md` — Judge validity requires ≥30 human ratings with Spearman ρ ≥ 0.6. No human ratings exist yet. The LLM judge is currently untrusted. — [Source](evaluation/README.md)
- **Confidence calibration ECE unknown.** `docs/reviews/2026-09-22-launch-cycle-1.md` — Calibration mechanism works (ECE drops from 0.17 to ≤0.05 on synthetic overconfident data), but real ECE per field is unknown until labeled outcomes accumulate. — [Source](docs/reviews/2026-09-22-launch-cycle-1.md)
- **Narrative memory impact not measured.** `docs/reviews/2026-09-22-launch-cycle-1.md` — Memory experiment mechanism exists but no real outcome data. Current decision: collect. Memory holdout (`MONTA_MEMORY_CONTROL_SHARE=0.1`) not yet enabled. — [Source](docs/reviews/2026-09-22-launch-cycle-1.md)
- **Signal thresholds uncalibrated on real footage.** `docs/layers-3-7.md` — "Signal thresholds are calibrated for 320px luma at ≤4 fps. Re-fit them on a labelled clip set before changing sampling." — [Source](docs/layers-3-7.md)
- **Audio energy not part of energy score.** `docs/layers-3-7.md` — "Audio energy (loudness, onsets) is not yet part of the energy score. Add it as a second signal family and bump the version." — [Source](docs/layers-3-7.md)
- **Intelligence cache is per-process.** `docs/layers-3-7.md` — "LRUIntelligenceCache is per process. Across workers, implement IntelligenceCache over Redis or Postgres." — [Source](docs/layers-3-7.md)
- **No duplicate/near-duplicate clip detection.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — "No dedup logic exists." — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Cross-tenant data in ContextPack.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — `previous_successful_projects` holds other creators' user_id/project_id/elements. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Forced user_rating in narrative memory.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — `user_rating` is required, so unrated projects must invent a rating. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Upsert race condition.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — DELETE+INSERT upsert races under concurrency on PostgreSQL. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **FFmpeg input not sandboxed.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — No `-protocol_whitelist`, symlinks followed, max duration not enforced. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **No end-to-end deadline.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — Intent extraction can take ~75s with no overall deadline. Retry amplification: worst case 12 vision calls per clip. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Story scores are self-graded.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — `story_score` averages the fit values the assigner just maximized. The refiner can never overrule the heuristic's taste. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **One cut per clip limitation.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — Three 180s takes and a 30s request produced a 10.7s edit with 3 cuts. (Partially addressed in Launch Cycle 1 with long-take segmentation.) — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Benchmark baseline records dirty sha.** `benchmarks/README.md` — "baselines/architect.v2.json is this release on dataset 6115d2ed… (git sha 153a511-dirty, because the work was uncommitted). Regenerate it after committing." — [Source](benchmarks/README.md)
- **Placeholder values in .env.example.** `GEMINI_API_KEY=your-gemini-api-key-here`, `QWEN_API_KEY=`, `OPENAI_COMPAT_API_KEY=`, `OPENAI_COMPAT_BASE_URL=`, `OPENAI_COMPAT_MODEL=`, `LANGCHAIN_API_KEY=your-langchain-api-key`, `SECRET_KEY=change-me-in-production`. — [Source](.env.example)
- **2 tests skipped when FFmpeg absent.** `tests/test_intelligence.py:187` and `tests/test_video_eval.py:52` — `@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")`. — [Source](tests/test_intelligence.py)

### Inferences
- The most critical remaining bug is the lack of real model validation — the entire provider abstraction layer has never been tested against real Qwen, Qwen-VL, or Gemini endpoints.
- The 4 golden failures are known and documented but not fixed; they represent edge cases in genre inference, pace inference, and energy spikes.
- The narrative memory system has data integrity issues (forced ratings, upsert races) that need fixing before production traffic.

### Gaps
- No GitHub issues are accessible to check for additional bug reports.
- The `.aider.chat-history.md` file could not be read.

---

## What infrastructure or deployment work remains?

### Takeaway
Docker Compose infrastructure exists for all services. Kubernetes manifests are minimal (README only). CI/CD pipelines are not implemented. The benchmark baseline needs regeneration on a clean commit. Several production-hardening infrastructure components are planned but not built.

### Cited Findings
- **Docker Compose is defined but incomplete.** `docker-compose.yml` — defines postgres, redis, qdrant, backend, worker, frontend. No monitoring stack (Prometheus, Grafana). No CI/CD pipeline. — [Source](docker-compose.yml)
- **Kubernetes manifests are placeholder-only.** `infra/kubernetes/` — contains only a `README.md` (369 bytes). No actual deployment manifests. — [Source](infra/kubernetes/)
- **No CI/CD pipeline.** The launch readiness review mentions `new .github/workflows/layers-3-7.yml (or equivalent)` as a Phase 1 task. No `.github/` directory exists. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **No monitoring stack.** Prometheus metrics endpoint exists (`/api/v1/metrics`) but no Prometheus/Grafana infrastructure is configured in docker-compose. — [Source](docker-compose.yml)
- **No shared rate limiter.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — "There is no rate limiter shared across processes, so N workers × max_concurrency=16 will exceed vendor quotas." Planned: `shared/providers/ratelimit.py`. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **No shared intelligence cache.** `docs/layers-3-7.md` — "LRUIntelligenceCache is per process. Across workers, implement IntelligenceCache over Redis or Postgres." — [Source](docs/layers-3-7.md)
- **No task persistence or resume.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — "No persistence, resume, idempotency or user cancellation. A worker crash re-runs everything." — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **No distributed execution.** `docs/layers-3-7.md` — "The executor is in-process. To go multi-host, give the registry agents that enqueue Celery tasks and await their results." — [Source](docs/layers-3-7.md)
- **No checkpoint persistence.** `orchestration/state/checkpoints.py` — Redis-backed checkpointing not implemented. — [Source](orchestration/state/checkpoints.py)
- **Benchmark baseline needs regeneration.** `benchmarks/README.md` — baseline records dirty git sha `153a511-dirty`. Must be regenerated on a clean commit. — [Source](benchmarks/README.md)
- **No API authentication.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — "API auth + per-user rate limits on /prompts/analyze" is a Phase 2 task. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **No vendor data-processing review.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — "Keyframes of creators (and bystanders) go to third-party vendors... a data-processing review, per-region vendor routing, a vendor retention policy" is needed. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)

### Inferences
- The project is at the "works on my machine" stage for Layers 3–7. Production deployment requires: CI/CD, monitoring, shared caches, rate limiting, task persistence, and Kubernetes manifests.
- The Docker Compose setup is sufficient for local development but lacks operational tooling for production.

### Gaps
- No GitHub Actions or CI configuration found.
- No Terraform or infrastructure-as-code beyond Docker Compose.
- The `infra/kubernetes/README.md` content was not read (only 369 bytes).

---

## What documentation is missing?

### Takeaway
Core architecture docs exist for Layers 3–7. Missing docs include: implementation plan (referenced but not found), labeling guidelines, API authentication docs, deployment/operations runbooks, and documentation for Layers 8–13.

### Cited Findings
- **`docs/architecture.md` references `implementation_plan.md` which does not exist.** "See [implementation_plan.md] for the complete 14-layer architecture diagram and folder mapping." — [Source](docs/architecture.md)
- **No labeling guidelines.** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — Phase 3 task: "labeling guidelines in `docs/labeling/`". — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **No deployment/operations runbook.** No `docs/operations/` or `docs/deployment/` directory exists. — [Source](docs/)
- **No API authentication documentation.** `docs/api.md` documents endpoints but not auth mechanisms. — [Source](docs/api.md)
- **No documentation for Layers 8–13.** `docs/layers.md` only covers Layers 3–7. Layers 8–13 have no architecture or implementation docs. — [Source](docs/layers.md)
- **No `datasets/README.md` for prompt corpus or clip labels.** Only `datasets/README.md` for golden projects exists. — [Source](datasets/)
- **No `evaluation/README.md` for story judge internals.** The evaluation README covers usage but not design rationale. — [Source](evaluation/README.md)
- **CONTRIBUTING.md is minimal.** `docs/CONTRIBUTING.md` — 18 lines, no testing instructions, no PR process, no code review checklist. — [Source](docs/CONTRIBUTING.md)
- **No CHANGELOG.** No changelog file found. — [Source](docs/)
- **No ROADMAP file.** The roadmap is embedded in the launch readiness review (`docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` §6) but not in a standalone file. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)

### Inferences
- Documentation is strongest for the layers that are implemented (3–7) and weakest for the stub layers (8–13) and operational concerns.
- The missing `implementation_plan.md` is a significant gap since `architecture.md` references it as the primary architecture document.

### Gaps
- The `implementation_plan.md` file referenced in `architecture.md` could not be found anywhere in the repository.
- No `docs/labeling/` directory exists.

---

## What are the next milestones or priorities?

### Takeaway
The project follows a 4-phase roadmap from the launch readiness review. Phase 1 (critical blockers) is mostly complete. The immediate next steps before GA are: run live provider tests, label real clips, collect human story ratings, accumulate calibration data, enable memory holdout, and regenerate the benchmark baseline.

### Cited Findings
- **Immediate next steps before GA (from Launch Cycle 1):** `docs/reviews/2026-09-22-launch-cycle-1.md` — 6 required steps: (1) Run `tests/providers_live` with real Qwen/Qwen-VL/Gemini keys, (2) Label ~200 real clips and run L6 evaluation, (3) Collect ≥30 human story ratings and run judge agreement, (4) Accumulate calibration outcomes and refit, (5) Enable memory holdout at 10%, (6) Regenerate benchmark baseline on clean sha and add as CI check. — [Source](docs/reviews/2026-09-22-launch-cycle-1.md)
- **Phase 1 — Critical blockers (target: 3 weeks, 3 engineers):** `docs/reviews/2026-09-21-layers-3-7-launch-readiness.md` — Mostly complete. Remaining: live provider contract suite execution, CI with Python 3.11 + FFmpeg, minimum observability. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Phase 2 — Production hardening (target: 5 weeks):** Full OpenTelemetry tracing, DecisionLog/OutcomeLog pipeline, task persistence/resume/cancellation, shared intelligence cache, shared rate limiter, failure catalog, duplicate detection, user-facing EditDiagnostics, prior rollups, API auth, vendor data-processing review. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Phase 3 — Quality optimization (target: 8–10 weeks, including labeling):** Datasets (golden, prompt corpus, clip labels, pairwise), benchmark framework + CI gates, four-tier story evaluator + validated judge, confidence calibration, re-fit signal thresholds, speech/transcript understanding, sub-clip moment segmentation, per-pattern validation config, fit selection weights from benchmark, refiner accepted by independent judge, multi-language lexicons. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Phase 4 — Scale readiness (target: 6 weeks):** Distributed execution (Celery), multi-model arbitration cascade, narrative-memory holdout + exploration, creator-baseline normalization, checkpoint slimming, vendor-native structured output, cost-aware admission control, load testing (500 clips/project, 10× peak RPS). — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Launch decision matrix:** Internal dogfood at Phase 1 complete; Closed beta (≤1k creators) at Phase 2 complete + golden benchmark; GA at Phase 3 acceptance + Phase 4 load/cost criteria. — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)
- **Total estimated remaining effort:** ~33–40 engineer-weeks plus labeling budget (from launch readiness review). — [Source](docs/reviews/2026-09-21-layers-3-7-launch-readiness.md)

### Inferences
- The project is at the Phase 1 → Phase 2 transition. Layers 3–7 are built but not validated against real models or real traffic.
- The critical path is: real model validation → real footage labeling → human judgment validation → calibration → production hardening → scale.
- Layers 8–13 are not on the critical path for the current phase but are needed for a complete product.

### Gaps
- No project board or issue tracker is accessible to track progress against the roadmap.
- No timeline or sprint schedule is documented beyond the phase targets.

---

## Are there any open GitHub issues or project boards?

### Takeaway
GitHub issues and project boards are not accessible. The `gh` CLI is installed but not authenticated, and no git remote is configured.

### Cited Findings
- `gh` CLI returns: "To get started with GitHub CLI, please run: gh auth login. Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token." — [Source](bash command output)
- No git remote configured (`git remote get-url origin` returns nothing). — [Source](bash command output)

### Inferences
- The project may be local-only or hosted on a platform not configured in this environment.
- All tracking appears to be done through the `docs/reviews/` documents rather than GitHub issues.

### Gaps
- Cannot determine if open GitHub issues exist.
- Cannot determine if project boards exist.

---

## What does the memory/ directory contain in terms of plans or notes?

### Takeaway
The `memory/` directory contains two distinct implementations: (1) a production narrative memory system (`narrative_memory.py`) with SQL stores, Bayesian learning, and Alembic migrations, and (2) a set of stub files (`learning_engine.py`, `feedback_loop.py`, `user_preferences.py`, `qdrant_client.py`, `embeddings.py`) that represent an older or parallel Layer 14 design that was never completed.

### Cited Findings
- **Production narrative memory:** `memory/narrative_memory.py` — 286 lines, fully implemented. Contains `NarrativeMemoryStore` protocol, `InMemoryNarrativeStore`, `SqlNarrativeStore` (PostgreSQL/SQLite), `NarrativeLearner` with hierarchical Bayesian smoothing (k=5, neutral prior 0.5). Uses Alembic migration `0001_narrative_memory`. — [Source](memory/narrative_memory.py)
- **Stub learning engine:** `memory/learning_engine.py` — 21 lines. `LearningEngine` class with `learn_from_project` (TODO: Extract patterns from liked/rejected edits, Update preference model, Store learned patterns in Qdrant) and `suggest_improvements` (TODO: Compare current edit against user's preference model). Both return empty. — [Source](memory/learning_engine.py)
- **Stub feedback loop:** `memory/feedback_loop.py` — 11 lines. `FeedbackLoop.process_feedback` — TODO: Update user preference vectors, Adjust model weights for future predictions. — [Source](memory/feedback_loop.py)
- **Stub user preferences:** `memory/user_preferences.py` — 30 lines. `UserPreferencesStore` with `record_feedback` (TODO: Store in Qdrant as vector + PostgreSQL as structured data), `get_preferences` (TODO: Query and aggregate feedback; returns empty defaults), `find_similar_preferences` (TODO: Vector similarity search in Qdrant). — [Source](memory/user_preferences.py)
- **Stub Qdrant client:** `memory/qdrant_client.py` — 30 lines. `MontaQdrantClient` with `__init__` (TODO: Initialize qdrant_client.QdrantClient), `store_embedding` (TODO: Upsert to Qdrant), `search_similar` (TODO: Qdrant search; returns []), `setup_collection` (TODO: Create Qdrant collection). — [Source](memory/qdrant_client.py)
- **Stub embeddings:** `memory/embeddings.py` — 18 lines. `EmbeddingGenerator` with `__init__` (TODO: Load sentence-transformers model), `embed_text` (TODO: Run model inference; returns []), `embed_batch` (returns []). — [Source](memory/embeddings.py)
- **Dependencies:** `memory/requirements.txt` — `qdrant-client>=1.12.0`, `sentence-transformers>=3.3.0`. — [Source](memory/requirements.txt)

### Inferences
- The stub files in `memory/` appear to be an earlier design iteration that was superseded by the production `narrative_memory.py`. The stubs reference Qdrant and sentence-transformers, while the production system uses SQL stores with Bayesian learning.
- The stub files are not imported by any production code and can likely be removed to reduce confusion.
- The production narrative memory system is well-designed but has known issues (forced ratings, upsert races, cross-tenant data exposure) documented in the launch readiness review.

### Gaps
- It is unclear whether the stub files are intentionally kept for future reference or are dead code.
- No documentation explains the relationship between the stub memory files and the production narrative memory system.
