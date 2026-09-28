"""
MONTA — Metric Catalog (Layers 3–7)
=====================================
Every production metric, declared once. Dashboards and alerts reference these
names; ``docs/layers-3-7.md#observability`` lists the alert thresholds.
"""

from shared.observability.metrics import RATIO_BUCKETS, REGISTRY, SCORE_BUCKETS

# ---- pipeline / stages
STAGE_DURATION = REGISTRY.histogram("monta_stage_duration_seconds", "Duration of a pipeline stage", ["stage", "outcome"])
REQUESTS = REGISTRY.counter("monta_requests_total", "Layer 3–7 pipeline runs", ["outcome"])

# ---- providers (models)
PROVIDER_CALLS = REGISTRY.counter("monta_provider_calls_total", "Model provider HTTP calls", ["provider", "model", "outcome"])
PROVIDER_LATENCY = REGISTRY.histogram("monta_provider_latency_seconds", "Model provider call latency", ["provider", "model"])
PROVIDER_TOKENS = REGISTRY.counter("monta_provider_tokens_total", "Tokens consumed", ["provider", "model", "direction"])
PROVIDER_RETRIES = REGISTRY.counter("monta_provider_retries_total", "Provider-level retries", ["provider", "model", "error"])
CIRCUIT_OPEN = REGISTRY.gauge("monta_circuit_open", "1 when the provider circuit breaker is open", ["provider", "model"])
FALLBACK_USED = REGISTRY.counter("monta_fallback_used_total", "Calls served by a non-primary provider", ["from_model", "to_model"])
STRUCTURED_ATTEMPTS = REGISTRY.histogram("monta_structured_attempts", "Attempts to obtain schema-valid output",
                                         ["schema", "model"], buckets=(1, 2, 3, 4, 5))
STRUCTURED_FAILURES = REGISTRY.counter("monta_structured_failures_total", "Schema/semantic validation exhausted", ["schema", "model"])
ARBITRATIONS = REGISTRY.counter("monta_arbitrations_total", "Multi-model arbitration outcomes", ["schema", "outcome"])

# ---- director
TASKS = REGISTRY.counter("monta_task_total", "Director tasks by final status", ["agent", "status"])
TASK_DURATION = REGISTRY.histogram("monta_task_duration_seconds", "Director task duration", ["agent", "status"])
TASK_RETRIES = REGISTRY.counter("monta_task_retries_total", "Director-level task retries", ["agent", "error"])
PLANS = REGISTRY.counter("monta_plans_total", "Execution plans by status", ["status"])

# ---- layer 3
INTENT_FIELD_DEFAULT = REGISTRY.counter("monta_intent_default_field_total", "Core intent fields left at policy default", ["field"])
INTENT_CONFIDENCE = REGISTRY.histogram("monta_intent_confidence", "Calibrated confidence of core intent fields", ["field"],
                                       buckets=RATIO_BUCKETS)
INTENT_ISSUES = REGISTRY.counter("monta_intent_issues_total", "Ambiguities, conflicts and degradations", ["kind"])

# ---- layer 4
CONTEXT_SOURCE = REGISTRY.counter("monta_context_source_total", "Context source fetches", ["source", "status"])

# ---- layer 6
CLIP_ANALYSIS = REGISTRY.counter("monta_clip_analysis_total", "Clip analyses", ["outcome"])
VISION_DEGRADED = REGISTRY.counter("monta_vision_degraded_total", "Clips analyzed without vision (failure or disabled)", ["reason"])
INTEL_CACHE = REGISTRY.counter("monta_intel_cache_total", "Clip intelligence cache lookups", ["result"])
MEDIA_TOOL = REGISTRY.histogram("monta_media_tool_seconds", "ffmpeg/ffprobe run time", ["tool", "outcome"])

# ---- layer 7
PATTERN_SELECTED = REGISTRY.counter("monta_story_pattern_selected_total", "Story patterns chosen", ["pattern"])
STORY_VALIDATION = REGISTRY.counter("monta_story_validation_total", "Story validation results", ["profile", "passed"])
STORY_VIOLATIONS = REGISTRY.counter("monta_story_violations_total", "Validation violations", ["rule", "severity"])
STORY_DURATION_DEVIATION = REGISTRY.histogram("monta_story_duration_deviation_ratio", "|total-target|/target", [],
                                              buckets=RATIO_BUCKETS)
JUDGE_SCORE = REGISTRY.histogram("monta_story_judge_score", "Independent Story Judge overall score", ["judge"],
                                 buckets=SCORE_BUCKETS)
STORY_RETRY = REGISTRY.counter("monta_story_retry_total", "Critic-driven story retries", ["result"])

# ---- layer 2: media gateway
UPLOAD_BYTES = REGISTRY.counter("monta_upload_bytes_total", "Bytes streamed into storage")
UPLOAD_ACCEPTED = REGISTRY.counter("monta_upload_accepted_total", "Clips accepted", ["resolution_class"])
UPLOAD_REJECTED = REGISTRY.counter("monta_upload_rejected_total", "Clips rejected by the validation engine", ["reason"])
UPLOAD_DEDUPE = REGISTRY.counter("monta_upload_dedupe_total", "Uploads matched to an existing SHA-256", ["scope"])
UPLOAD_DURATION = REGISTRY.histogram("monta_upload_seconds", "Wall time of one ingest", ["outcome"])
PROBE_DURATION = REGISTRY.histogram("monta_probe_seconds", "ffprobe wall time", ["outcome"])

# ---- queue / jobs
JOBS_SUBMITTED = REGISTRY.counter("monta_jobs_submitted_total", "Pipeline jobs handed to the queue", ["kind", "outcome"])
JOB_STATE = REGISTRY.counter("monta_job_state_total", "Job state transitions", ["kind", "state"])
PROGRESS_EVENTS = REGISTRY.counter("monta_progress_events_total", "Progress events published", ["stage"])
WS_CONNECTIONS = REGISTRY.gauge("monta_ws_connections", "Open progress websockets")

# ---- memory experiment
MEMORY_ARM = REGISTRY.counter("monta_memory_experiment_assignments_total", "Narrative-memory experiment arm assignments", ["arm"])
