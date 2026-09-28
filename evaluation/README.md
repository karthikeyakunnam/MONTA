# MONTA Evaluation

| Tool | Command / API | Measures |
|---|---|---|
| Story Judge | `evaluation.story_judge.HeuristicStoryJudge / LLMStoryJudge / CompositeStoryJudge` | overall, coherence, emotion, pacing, hook, ending, prompt alignment, clip relevance, and a confidence |
| Judge validity | `evaluation.story_judge.agreement.agreement(judge_id, pairs)` | Spearman correlation and pairwise accuracy vs human ratings. An LLM judge is trusted only at n≥30, ρ≥0.6 and pairwise ≥0.7 |
| Golden comparison | `evaluation.golden.run_golden(projects, stack)` | 12 checks per project (pattern, genre, pace, emotion, arc, start pace, end emotion, hero, exclusions, duration, validity, judge) |
| Layer 6 suite | `python -m evaluation.video_intelligence.run --manifest m.jsonl [--consistency] [--with-vision]` | activity, emotion, scene, shot, camera and lighting accuracy with confusion matrices; role top-1/top-3; quality/energy Spearman; re-encode consistency; failure analysis |
| Synthetic L6 set | `python -m evaluation.video_intelligence.generate_synthetic --out DIR` | Clips whose lighting, camera motion and blur are true by construction (real FFmpeg decoding) |
| Memory experiment | `evaluation.memory_experiment` | `run_offline` (paired judge deltas and flip rate), `analyze_online` (rating, completion and engagement by arm, bootstrap CI and permutation p-value). `decide` returns keep / disable / collect |

## Independence rule

The judge package never imports the Story Architect, and no evaluation module imports the
architect's scoring or assembly internals. `tests/test_story_judge.py` enforces this. The
architect's own `story_score` is an optimization target, not a quality measurement.
Only judge output may be reported as quality.

## Results recorded on 2026-09-22

Golden set, `current` variant:

| Measure | Result |
|---|---|
| Pass rate | 71% (10/14) |
| Judge overall | 7.68 |
| Validity | 93% |
| Duration check | 100% |
| Hero check | 100% |

Open failures:

| Project | Failure |
|---|---|
| `cinematic_a24_mood` | energy spike |
| `cinematic_trailer_style` | pattern ends on a resolve act instead of a hero payoff; the genre label is debatable |
| `travel_tokyo_night` | genre inferred as cinematic |
| `wedding_romantic_film` | pace inferred as medium; mood confidence is below the inference threshold |

Layer 6 synthetic set (6 clips, real FFmpeg 7.0):

| Measure | Result |
|---|---|
| Lighting accuracy | 5/5 |
| Camera-motion accuracy | 6/6, after the tiled global-motion fix; 5/6 before |
| Quality Spearman vs labels | 0.87 |
| Re-encode consistency MAE | 0.11 |

Semantic metrics are not yet measured: activity, emotion, scene and roles need
human-labeled real footage and a vision provider.
