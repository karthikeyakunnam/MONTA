"""Layer 6 evaluation suite — metrics, confusion matrices, failure analysis; real FFmpeg when available."""

import json
import shutil

import pytest

from evaluation.video_intelligence import evaluate, load_manifest
from evaluation.video_intelligence.evaluate import spearman
from evaluation.video_intelligence.generate_synthetic import generate
from orchestration.agents.intelligence import FFmpegMediaBackend, VideoIntelligenceTeam, VisionAnalyzer
from tests.conftest import ScriptedProvider, SyntheticMediaBackend

VISION = json.dumps({"activities": ["barbell deadlift"], "objects": [], "camera_type": "phone", "shot_type": "medium",
                     "scene_type": "gym", "emotion": "intense", "emotion_confidence": 0.8, "energy_estimate": 8,
                     "story_role_candidates": [{"role": "peak", "confidence": 0.8}, {"role": "intensity", "confidence": 0.6}],
                     "reasoning": "x"})


def write_manifest(tmp_path, rows):
    path = tmp_path / "manifest.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert spearman([1, 1], [1, 2]) is None


async def test_metrics_confusion_and_failures(tmp_path):
    rows = [
        {"clip_id": "a", "path": "a.mp4", "labels": {"activities": ["deadlift"], "emotion": "intense", "scene_type": "gym",
                                                      "shot_type": "medium", "story_roles": ["peak"], "camera_motion": "static"}},
        {"clip_id": "b", "path": "b.mp4", "labels": {"activities": ["surfing"], "emotion": "joyful", "scene_type": "beach",
                                                      "shot_type": "wide", "story_roles": ["hero"], "camera_motion": "pan"}},
        {"clip_id": "broken", "path": "broken.mp4", "labels": {"emotion": "calm"}},
    ]
    entries = load_manifest(write_manifest(tmp_path, rows))
    media = SyntheticMediaBackend({"a": "static", "b": "pan"}, fail={"broken"})
    team = VideoIntelligenceTeam(media, vision=VisionAnalyzer(ScriptedProvider([VISION, VISION])))
    report = await evaluate(entries, team)
    assert report.analyzed == 2 and any(f.kind == "analysis_error" and f.clip_id == "broken" for f in report.failures)
    assert report.activity["hit_rate"] == 0.5 and report.activity["recall"] == 0.5
    assert report.categorical["emotion"].accuracy == 0.5
    assert report.categorical["emotion"].confusion["joyful"] == {"intense": 1}
    assert report.categorical["camera_motion"].accuracy == 1.0
    assert report.roles["top1"] == 0.5 and report.roles["top3"] == 0.5
    assert any(w.kind == "activity_miss" and w.clip_id == "b" for w in report.worst)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
async def test_real_ffmpeg_synthetic_suite(tmp_path):
    manifest = generate(tmp_path / "set", seconds=3)
    report = await evaluate(load_manifest(manifest), VideoIntelligenceTeam(FFmpegMediaBackend()), consistency=True)
    assert report.analyzed == report.clips
    assert report.categorical["lighting"].accuracy == 1.0
    assert report.categorical["camera_motion"].accuracy >= 0.8
    assert report.quality.spearman is not None and report.quality.spearman > 0.5
    assert report.quality_consistency.mean_abs_error < 1.0
