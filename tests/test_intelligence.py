"""Layer 6 — signals, fusion, vision abstraction, caching, failure isolation, ffmpeg integration."""

import json
import shutil
import subprocess

import numpy as np
import pytest

from orchestration.agents.intelligence import FFmpegMediaBackend, LRUIntelligenceCache, VideoIntelligenceTeam, VisionAnalyzer
from orchestration.agents.intelligence.fusion import fuse
from orchestration.agents.intelligence.media import MediaToolError, display_size, keyframe_times, parse_probe
from orchestration.agents.intelligence.signals import (
    camera_motion_from_signals,
    compute_signals,
    energy_from_signals,
    lighting_from_signals,
    quality_from_signals,
)
from shared.contracts.clip import ClipSource
from shared.contracts.vocab import CameraMotion, Emotion, Lighting, ShotType, StoryRole
from shared.providers.errors import CapabilityError, ProviderUnavailableError
from tests.conftest import ScriptedProvider, SyntheticMediaBackend, synthetic_frames

VISION_JSON = json.dumps({
    "activities": ["deadlift"], "objects": ["barbell"], "people": ["athlete in black shirt"], "camera_type": "phone",
    "shot_type": "medium", "scene_type": "gym", "emotion": "intense", "emotion_confidence": 0.8, "energy_estimate": 8.5,
    "story_role_candidates": [{"role": "peak", "confidence": 0.8}, {"role": "intensity", "confidence": 0.7}],
    "visual_tags": ["chalk", "low light"], "quality_issues": [], "reasoning": "Athlete locks out a heavy deadlift.",
})


def signals(profile):
    return compute_signals(synthetic_frames(profile), 4.0)


# ---------------------------------------------------------------- signals

@pytest.mark.parametrize("profile,motion", [
    ("static", CameraMotion.STATIC), ("pan", CameraMotion.PAN), ("shaky", CameraMotion.SHAKY),
])
def test_camera_motion_classification(profile, motion):
    assert camera_motion_from_signals(signals(profile))[0] == motion


def test_lighting_and_quality_respond_to_defects():
    good, dark, flat = signals("static"), signals("dark"), signals("blur")
    assert lighting_from_signals(good)[0] == Lighting.GOOD
    assert lighting_from_signals(dark)[0] == Lighting.LOW
    assert lighting_from_signals(flat)[0] == Lighting.FLAT
    q_good, q_dark, q_flat = (quality_from_signals(m)[0] for m in (good, dark, flat))
    assert q_good > 7 and q_dark < q_good - 3 and q_flat < q_good - 3


def test_energy_orders_static_below_motion():
    e = {p: energy_from_signals(signals(p), 4.0)[0] for p in ("static", "pan", "action")}
    assert e["static"] < 1 < min(e["pan"], e["action"])


def test_cut_detection_and_peak():
    frames = np.concatenate([synthetic_frames("static", n=8), synthetic_frames("dark", n=8)])
    m = compute_signals(frames, 4.0)
    assert m.cut_count == 1
    assert m.peak_time_s == pytest.approx(2.0, abs=0.5)
    assert len(m.energy_curve) <= 24


def test_compute_signals_rejects_empty():
    with pytest.raises(ValueError):
        compute_signals(np.zeros((0, 4, 4), np.uint8), 4.0)


# ---------------------------------------------------------------- media helpers

def test_parse_probe_and_errors():
    src = ClipSource(clip_id="c1", path="/x.mp4")
    data = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
                         "codec_name": "h264", "side_data_list": [{"rotation": -90}]}, {"codec_type": "audio"}],
            "format": {"duration": "12.5", "size": "999"}}
    m = parse_probe(src, data, file_size=1, fingerprint="f")
    assert m.fps == pytest.approx(29.97, abs=0.01) and m.rotation == 270 and m.has_audio and m.is_vertical
    assert display_size(m, 320) == (320, 568)
    with pytest.raises(MediaToolError):
        parse_probe(src, {"streams": [{"codec_type": "audio"}]}, file_size=1, fingerprint="f")


def test_keyframe_times_include_peak_and_stay_in_bounds():
    ts = keyframe_times(10.0, 7.3, 4)
    assert 7.3 in ts and all(0 <= t < 10 for t in ts) and ts == sorted(ts)


# ---------------------------------------------------------------- fusion

def _meta(clip_id="c1", duration=6.0):
    from shared.contracts.clip import TechnicalMetadata
    return TechnicalMetadata(clip_id=clip_id, path="/x", duration_s=duration, fps=30, width=1920, height=1080, codec="h264",
                             has_audio=True, file_size_bytes=1, fingerprint="f")


def test_fusion_without_vision_is_explicit_and_usable():
    ci = fuse(_meta(), signals("action"), None, vision_model=None, degraded=("no vision provider configured",))
    assert ci.usable and ci.activities == () and ci.emotion is None
    assert ci.story_role_candidates and all(r.confidence <= 0.35 for r in ci.story_role_candidates)
    assert "No vision analysis" in ci.reasoning and ci.provenance.vision_model is None


def test_fusion_with_vision_blends_energy_and_penalizes_issues():
    from shared.contracts.clip import VisionObservation
    obs = VisionObservation.model_validate({**json.loads(VISION_JSON), "quality_issues": ["motion blur", "watermark"]})
    sig = signals("static")
    ci = fuse(_meta(), sig, obs, vision_model="qwen_vl:qwen-vl-max")
    q_sig = quality_from_signals(sig)[0]
    e_sig = energy_from_signals(sig, 6.0)[0]
    assert ci.quality_score == pytest.approx(q_sig - 1.0, abs=0.01)
    assert ci.energy_score == pytest.approx(0.65 * e_sig + 0.35 * 8.5, abs=0.01)
    assert ci.camera_motion == CameraMotion.STATIC, "camera motion comes from signals, not the model"
    assert ci.shot_type == ShotType.MEDIUM and ci.emotion == Emotion.INTENSE
    assert ci.role_confidence(StoryRole.PEAK) == 0.8


def test_fusion_marks_tiny_clips_unusable():
    ci = fuse(_meta(duration=0.3), signals("static"), None, vision_model=None)
    assert not ci.usable and "duration" in ci.unusable_reason


# ---------------------------------------------------------------- team

async def test_team_with_vision_and_cache_hit():
    media = SyntheticMediaBackend({"a": "action"})
    provider = ScriptedProvider([VISION_JSON])
    cache = LRUIntelligenceCache()
    team = VideoIntelligenceTeam(media, vision=VisionAnalyzer(provider), cache=cache)
    meta = await media.probe(ClipSource(clip_id="a", path="/a.mp4"))

    first = await team.analyze(meta)
    assert first.activities == ("deadlift",) and first.provenance.vision_model == "scripted:test-model"
    assert provider.requests[0][1].has_images

    renamed = meta.model_copy(update={"clip_id": "a-reupload"})
    second = await team.analyze(renamed)
    assert second.provenance.cache_hit and second.clip_id == "a-reupload"
    assert media.luma_calls == ["a"] and len(provider.requests) == 1


async def test_vision_failure_degrades_and_is_not_cached():
    media = SyntheticMediaBackend({"a": "pan"})
    provider = ScriptedProvider([ProviderUnavailableError("down"), VISION_JSON])
    cache = LRUIntelligenceCache()
    team = VideoIntelligenceTeam(media, vision=VisionAnalyzer(provider), cache=cache)
    meta = await media.probe(ClipSource(clip_id="a", path="/a.mp4"))
    degraded = await team.analyze(meta)
    assert degraded.usable and degraded.provenance.vision_model is None
    assert "vision failed" in degraded.provenance.degraded[0]
    recovered = await team.analyze(meta)
    assert recovered.provenance.vision_model is not None and not recovered.provenance.cache_hit


async def test_vision_requires_capability():
    with pytest.raises(CapabilityError):
        VisionAnalyzer(ScriptedProvider([], vision=False))


async def test_invalid_vision_output_is_repaired():
    bad = json.dumps({**json.loads(VISION_JSON), "shot_type": "super_wide", "story_role_candidates": []})
    provider = ScriptedProvider([bad, VISION_JSON])
    media = SyntheticMediaBackend({"a": "static"})
    team = VideoIntelligenceTeam(media, vision=VisionAnalyzer(provider))
    ci = await team.analyze(await media.probe(ClipSource(clip_id="a", path="/a.mp4")))
    assert ci.shot_type == ShotType.MEDIUM and len(provider.requests) == 2


def test_lru_cache_evicts_oldest():
    import asyncio
    cache = LRUIntelligenceCache(max_entries=2)
    ci = fuse(_meta(), signals("static"), None, vision_model=None)

    async def run():
        for k in "abc":
            await cache.put(k, ci)
        return await cache.get("a"), await cache.get("c")
    a, c = asyncio.run(run())
    assert a is None and c is not None


# ---------------------------------------------------------------- real ffmpeg

@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
async def test_ffmpeg_backend_end_to_end(tmp_path):
    path = tmp_path / "test.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    backend = FFmpegMediaBackend(timeout_s=60)
    meta = await backend.probe(ClipSource(clip_id="t", path=str(path)))
    assert meta.duration_s == pytest.approx(3.0, abs=0.1) and meta.width == 640
    frames, fps = await backend.sample_luma(meta, max_frames=40, width=160)
    assert frames.shape[1:] == (90, 160) and len(frames) >= 8
    stills = await backend.keyframes(meta, [0.5, 2.0], width=320)
    assert all(s.data[:2] == b"\xff\xd8" for s in stills)
    ci = await VideoIntelligenceTeam(backend).analyze(meta)
    assert ci.usable


async def test_ffmpeg_backend_reports_missing_file():
    with pytest.raises(MediaToolError, match="not found"):
        await FFmpegMediaBackend().probe(ClipSource(clip_id="x", path="/definitely/missing.mp4"))
