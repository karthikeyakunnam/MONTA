"""Unit tests for LocalVisionPipeline (Layer 6 offline vision intelligence)."""

import json
import pytest

from orchestration.agents.intelligence.local_vision import LocalVisionPipeline, MAX_VLM_FRAMES
from orchestration.agents.intelligence import VideoIntelligenceTeam
from shared.contracts.clip import ClipSource, TechnicalMetadata
from shared.contracts.vocab import CameraType, Emotion, ShotType, StoryRole
from shared.providers.base import Capability, ImagePart, ModelProvider, ModelResponse
from shared.providers.errors import CapabilityError, ProviderResponseError
from tests.conftest import ScriptedProvider, SyntheticMediaBackend, synthetic_frames


SAMPLE_VLM_DESCRIPTION = (
    "A fitness enthusiast is performing heavy deadlifts in an indoor gym. "
    "Medium shot filmed on a mobile phone camera. The lighting is focused on the athlete. "
    "The atmosphere is energetic and intense."
)

SAMPLE_STRUCTURED_JSON = json.dumps({
    "activities": ["deadlift", "weightlifting"],
    "objects": ["barbell", "plates"],
    "people": ["athlete"],
    "camera_type": "phone",
    "shot_type": "medium",
    "scene_type": "gym",
    "emotion": "intense",
    "emotion_confidence": 0.85,
    "energy_estimate": 8.0,
    "story_role_candidates": [
        {"role": "peak", "confidence": 0.9},
        {"role": "intensity", "confidence": 0.8},
    ],
    "visual_tags": ["weights", "gym floor"],
    "quality_issues": [],
    "reasoning": "Athlete performs deadlift with high intensity.",
})


def _meta(clip_id: str = "test_clip") -> TechnicalMetadata:
    return TechnicalMetadata(
        clip_id=clip_id,
        path=f"/{clip_id}.mp4",
        duration_s=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264",
        bitrate_kbps=5000,
        has_audio=True,
        file_size_bytes=1024,
        fingerprint="fp123",
    )


def test_local_vision_requires_proper_capabilities():
    text_provider = ScriptedProvider([SAMPLE_STRUCTURED_JSON], vision=False)
    vlm_provider = ScriptedProvider([SAMPLE_VLM_DESCRIPTION], vision=True)

    # VLM missing vision capability should fail
    with pytest.raises(CapabilityError):
        LocalVisionPipeline(vlm_provider=text_provider, text_provider=text_provider)

    # Valid initialization
    pipeline = LocalVisionPipeline(vlm_provider=vlm_provider, text_provider=text_provider)
    assert pipeline.model_id == "local_vision:scripted:test-model+scripted:test-model"


async def test_two_stage_pipeline_execution():
    vlm_provider = ScriptedProvider([SAMPLE_VLM_DESCRIPTION], vision=True)
    text_provider = ScriptedProvider([SAMPLE_STRUCTURED_JSON], vision=False)

    pipeline = LocalVisionPipeline(vlm_provider=vlm_provider, text_provider=text_provider)
    frames = [ImagePart(data=b"\xff\xd8\xff\xe0" + b"\x00" * 10) for _ in range(3)]
    times = [0.0, 1.5, 3.0]

    obs = await pipeline.observe(_meta(), frames, times)

    assert list(obs.activities) == ["deadlift", "weightlifting"]
    assert obs.camera_type == CameraType.PHONE
    assert obs.shot_type == ShotType.MEDIUM
    assert obs.emotion == Emotion.INTENSE
    assert obs.energy_estimate == 8.0
    assert len(obs.story_role_candidates) == 2
    assert obs.story_role_candidates[0].role == StoryRole.PEAK
    assert len(vlm_provider.requests) == 1
    assert len(text_provider.requests) == 1


async def test_frame_bounding_to_max_vlm_frames():
    vlm_provider = ScriptedProvider([SAMPLE_VLM_DESCRIPTION], vision=True)
    text_provider = ScriptedProvider([SAMPLE_STRUCTURED_JSON], vision=False)

    pipeline = LocalVisionPipeline(vlm_provider=vlm_provider, text_provider=text_provider)
    # Pass 8 frames; local Ollama's default context only fits two keyframes.
    frames = [ImagePart(data=b"\xff\xd8\xff\xe0" + b"\x00" * 10) for _ in range(8)]
    times = [float(i) for i in range(8)]

    await pipeline.observe(_meta(), frames, times)

    # Check that VLM received at most MAX_VLM_FRAMES images
    vlm_request = vlm_provider.requests[0]
    user_msg = vlm_request[0]
    image_parts = [p for p in user_msg.parts if isinstance(p, ImagePart)]
    assert len(image_parts) <= MAX_VLM_FRAMES
    assert len(image_parts) == 2


async def test_vlm_empty_description_raises_error():
    vlm_provider = ScriptedProvider(["   "], vision=True)
    text_provider = ScriptedProvider([SAMPLE_STRUCTURED_JSON], vision=False)

    pipeline = LocalVisionPipeline(vlm_provider=vlm_provider, text_provider=text_provider)
    frames = [ImagePart(data=b"\xff\xd8\xff\xe0")]

    with pytest.raises(ProviderResponseError, match="VLM returned empty description"):
        await pipeline.observe(_meta(), frames, [0.0])


async def test_observe_with_arbitration_compatibility():
    vlm_provider = ScriptedProvider([SAMPLE_VLM_DESCRIPTION], vision=True)
    text_provider = ScriptedProvider([SAMPLE_STRUCTURED_JSON], vision=False)

    pipeline = LocalVisionPipeline(vlm_provider=vlm_provider, text_provider=text_provider)
    frames = [ImagePart(data=b"\xff\xd8\xff\xe0")]

    obs, summary = await pipeline.observe_with_arbitration(_meta(), frames, [0.0])
    assert summary is None
    assert obs.shot_type == ShotType.MEDIUM


async def test_video_intelligence_team_integration():
    vlm_provider = ScriptedProvider([SAMPLE_VLM_DESCRIPTION], vision=True)
    text_provider = ScriptedProvider([SAMPLE_STRUCTURED_JSON], vision=False)
    pipeline = LocalVisionPipeline(vlm_provider=vlm_provider, text_provider=text_provider)

    media = SyntheticMediaBackend({"clip1": "static"})
    team = VideoIntelligenceTeam(media, vision=pipeline)

    ci = await team.analyze(await media.probe(ClipSource(clip_id="clip1", path="/clip1.mp4")))
    assert ci.shot_type == ShotType.MEDIUM
    assert list(ci.activities) == ["deadlift", "weightlifting"]
    assert ci.energy_score > 0
    assert ci.provenance.vision_model == "local_vision:scripted:test-model+scripted:test-model"


@pytest.mark.live
async def test_live_local_vision_with_moondream_and_qwen():
    from io import BytesIO
    from PIL import Image
    from shared.local_runtime.adapters.ollama import OllamaRuntime
    from shared.local_runtime.provider import LocalAIProvider

    runtime = OllamaRuntime()
    if not await runtime.is_available():
        pytest.skip("Local Ollama runtime not available")

    models = [m.name for m in await runtime.list_models()]
    if not any("moondream" in m for m in models):
        pytest.skip("moondream not installed in local Ollama")

    text_model = (
        "qwen2.5-coder:1.5b"
        if any("qwen2.5-coder:1.5b" in m for m in models)
        else ("qwen2.5:0.5b" if any("qwen2.5:0.5b" in m for m in models) else None)
    )
    if not text_model:
        pytest.skip("No lightweight text model installed in local Ollama")

    vlm = LocalAIProvider(runtime, "moondream:latest")
    text = LocalAIProvider(runtime, text_model)
    pipeline = LocalVisionPipeline(vlm_provider=vlm, text_provider=text)

    # Generate a sample frame: green scene
    buf = BytesIO()
    img = Image.new("RGB", (256, 256), color=(34, 139, 34))
    img.save(buf, format="JPEG")
    frames = [ImagePart(data=buf.getvalue())]

    meta = _meta("live_test")
    obs = await pipeline.observe(meta, frames, [0.0])

    assert isinstance(obs.energy_estimate, float)
    assert len(obs.story_role_candidates) >= 1
    assert obs.reasoning != ""
    await runtime.aclose()

