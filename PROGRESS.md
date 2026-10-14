# MONTA — M5 / M6 / M7 Progress Checklist

**Baseline:** 559 passed, 0 failed, 18 deselected (live-marked) — `python3 -m pytest tests/ -q --tb=no`

## M5 — AUDIO
- [✅] 5.1 Music generation (all styles) — `pytest tests/test_audio_studio.py`
- [✅] 5.2 SFX generation — `pytest tests/test_audio_studio.py`
- [✅] 5.3 Tempo BPM calc — `pytest tests/test_audio_studio.py::test_tempo_matcher_bpm`
- [✅] 5.4 Speech-aware ducking filtergraph — `pytest tests/test_audio_studio.py::test_compiler_generates_ducking_filtergraph`
- [✅] 5.5 E2E render with ducking + SFX — `pytest tests/test_audio_studio.py::test_audio_studio_end_to_end_render`
- [✅] 5.6 Loudness/clipping/sync objective validation (ffprobe checks) — `pytest tests/test_m5_audio_validation.py::test_music_generator_loudness_no_clipping`
- [✅] 5.7 Real-media audio: speech-heavy, music-only, silent, mixed — `pytest tests/test_m5_audio_validation.py`
- [✅] 5.8 Missing/corrupt audio asset error handling — `pytest tests/test_m5_audio_validation.py::test_corrupt_audio_asset_fails_gracefully`
- [✅] 5.9 Voiceover: documented as unsupported (no TTS model available) — `pytest tests/test_m5_audio_validation.py::test_voiceover_stub_documented_returns_empty`
- [✅] 5.10 DJRemix/BeatExtension/Mashup: documented as unsupported — `pytest tests/test_m5_audio_validation.py::test_dj_remix_stub_documented`

## M6 — PRODUCTIZATION
- [✅] 6.1 Hardware detection (CPU/GPU/accelerators) — `pytest tests/test_hardware_detector.py`
- [✅] 6.2 Encoder fallback — `pytest tests/test_hardware_detector.py::test_encoder_selection_by_platform`
- [✅] 6.3 Ollama model management: list/download/availability/delete — `pytest tests/test_m6_productization.py`
- [✅] 6.4 Model capability distinction (text vs vision) — `pytest tests/test_m6_productization.py::test_model_manager_list_and_capabilities`
- [✅] 6.5 Disk usage / corrupt model handling — `pytest tests/test_m6_productization.py::test_model_manager_disk_usage`
- [✅] 6.6 Config persistence — `pytest tests/test_m6_productization.py::test_config_persistence_lifecycle`
- [✅] 6.7 Upload security: path traversal, ext validation, size limits — `pytest tests/test_multi_res_and_export.py`
- [✅] 6.8 Job recovery (TaskPersistence) — `pytest tests/test_task_persistence.py`
- [✅] 6.9 Storage/tmp cleanup — `pytest tests/test_m6_productization.py::test_storage_cleanup_stale_uploads`
- [✅] 6.10 Models API routes — `pytest tests/test_m6_productization.py::test_api_models_list_endpoint`

## M7 — LEARNING
- [✅] 7.1 NarrativeMemory (InMemory + SQL) — `pytest tests/test_memory_and_context.py`
- [✅] 7.2 Bayesian pattern priors — `pytest tests/test_memory_and_context.py`
- [✅] 7.3 FeedbackLoop real impl — `pytest tests/test_m7_learning.py::test_feedback_loop_synchronization`
- [✅] 7.4 UserPreferencesStore real impl — `pytest tests/test_m7_learning.py::test_user_preferences_store_lifecycle`
- [✅] 7.5 Feedback→pipeline wiring — `pytest tests/test_m7_learning.py::test_feedback_loop_synchronization`
- [✅] 7.6 Feedback API endpoint — `pytest tests/test_m7_learning.py::test_feedback_api_endpoints`
- [✅] 7.7 Privacy/reset & GDPR export — `pytest tests/test_m7_learning.py::test_user_preferences_store_lifecycle`
- [✅] 7.8 Baseline dataset + before/after eval — `pytest tests/test_m7_learning.py::test_learning_engine_baseline_vs_after_evaluation`
