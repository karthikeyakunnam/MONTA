# MONTA Datasets

## `golden_projects/` — Layer 3–7 golden set (`golden.v1`)

14 projects, 2 per category: gym, travel, podcast, wedding, event, cinematic,
product launch. Each JSON file (validated by `datasets/schema.py::GoldenProject`)
contains:

| Field | Meaning |
|---|---|
| `prompt`, `platform` | The creator request and chosen destination |
| `clips[]` | Clip metadata at the **Layer 6 output level** (activities, shot, motion, quality, energy, emotion, story roles) |
| `expected.story_patterns` | Acceptable patterns, preferred first |
| `expected.genre / pace / emotion` | What Layer 3 should infer (after footage reconciliation) |
| `expected.emotional_arc` | `shape` (build / build_release / steady / hook_first), optional `start_pace`, `end_emotion` |
| `expected.hero_clip_ids`, `must_exclude` | Acceptable hero shots; clips an editor would never use |
| `expected.duration_s`, `min_judge_score` | Requested duration (±5% check); independent-judge floor |

**Provenance and limits (read before trusting numbers):**
- Expectations are expert editorial labels (`labeled_by`), v1, a single labeler. Inter-annotator
  agreement has not been measured yet; before a label drives a release decision, get a second
  label and adjudicate disagreements.
- Clip metadata is authored, not measured. The set evaluates Layers 3, 4 and 7 with Layer 6 held
  fixed. Layer 6 is evaluated separately on media (`evaluation/video_intelligence/`).
- Some labels are debatable: whether `travel_tokyo_night` is travel or cinematic, and whether
  `cinematic_trailer_style` is cinematic or travel. The current failures on them are reported,
  not hidden.

**Adding a project:** add `datasets/golden_projects/<project_id>.json`. The file name must equal
the id, and every referenced clip must exist (the loader enforces both). Additions change the
dataset hash that benchmark reports record. Never edit an existing project's expectations to
make a failing run pass; bump its `version` with a written reason instead.

## Planned datasets (schemas exist; data must come from production with consent)

| Dataset | Schema | Size target |
|---|---|---|
| Prompt corpus (L3 accuracy and calibration) | `DecisionRecord` / `OutcomeRecord` | 3,000 anonymized prompts, ≥10% non-English, ≥10% adversarial |
| Clip label set (L6) | `evaluation/video_intelligence/manifest.py::ManifestEntry` | 8,000 clips, 3 annotators, report Krippendorff α |
| Judge-validation ratings | `evaluation/story_judge/agreement.py::HumanRating` | ≥30 to trust a judge; 2,000 pairs per quarter |
| Memory history | `shared/contracts/memory.py::NarrativeMemoryRecord` | Exported from `narrative_memory` |
