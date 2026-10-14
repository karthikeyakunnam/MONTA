# MONTA — M5 / M6 / M7 Progress Checklist

**Baseline:** 528 passed, 18 deselected (live-marked) — `python3 -m pytest tests/ -q --tb=no`

## M5 — AUDIO
- [✅] 5.1 Music generation (all styles) — pytest tests/test_audio_studio.py
- [✅] 5.2 SFX generation — pytest tests/test_audio_studio.py
- [✅] 5.3 Tempo BPM calc — pytest tests/test_audio_studio.py::test_tempo_matcher_bpm
- [✅] 5.4 Speech-aware ducking filtergraph — pytest tests/test_audio_studio.py::test_compiler_generates_ducking_filtergraph
- [✅] 5.5 E2E render with ducking + SFX — pytest tests/test_audio_studio.py::test_audio_studio_end_to_end_render
- [🔴] 5.6 Loudness/clipping/sync objective validation (ffprobe checks) — IMPLEMENTING
- [🔴] 5.7 Real-media audio: speech-heavy, music-only, silent, mixed — IMPLEMENTING
- [🔴] 5.8 Missing/corrupt audio asset error handling — IMPLEMENTING
- [🔴] 5.9 Voiceover: documented as unsupported (no TTS model available)
- [🔴] 5.10 DJRemix/BeatExtension/Mashup: document as unsupported

## M6 — PRODUCTIZATION
- [✅] 6.1 Hardware detection (CPU/GPU/accelerators) — pytest tests/test_hardware_detector.py
- [✅] 6.2 Encoder fallback — pytest tests/test_hardware_detector.py
- [🔴] 6.3 Ollama model management: list/download/availability/delete — IMPLEMENTING
- [🔴] 6.4 Model capability distinction (text vs vision) — IMPLEMENTING
- [🔴] 6.5 Disk usage / disk-full / corrupt model handling — IMPLEMENTING
- [🔴] 6.6 Config persistence — stub only
- [✅] 6.7 Upload security: path traversal, ext validation, size limits — see test_multi_res_and_export.py
- [✅] 6.8 Job recovery (TaskPersistence) — pytest tests/test_task_persistence.py
- [🔴] 6.9 Storage/tmp cleanup

## M7 — LEARNING
- [✅] 7.1 NarrativeMemory (InMemory + SQL) — pytest tests/test_memory_and_context.py
- [✅] 7.2 Bayesian pattern priors — pytest tests/test_memory_and_context.py
- [🔴] 7.3 FeedbackLoop real impl — IMPLEMENTING
- [🔴] 7.4 UserPreferencesStore real impl — IMPLEMENTING
- [🔴] 7.5 Feedback→pipeline wiring — IMPLEMENTING
- [🔴] 7.6 Feedback API endpoint — IMPLEMENTING
- [🔴] 7.7 Privacy/reset — IMPLEMENTING
- [🔴] 7.8 Baseline dataset + before/after eval — IMPLEMENTING
