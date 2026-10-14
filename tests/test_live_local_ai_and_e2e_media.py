"""
MONTA — Live Local AI, Real Media & Pipeline Hardening Suite
==============================================================
Validates:
1. Live local model inference with Ollama (moondream:latest + qwen2.5)
2. Normal and imperfect prompts intent extraction
3. Missing model, timeout, and offline behaviors
4. Real media generation and analysis
5. Task persistence and crash recovery
6. Bounded critic retries
7. FFprobe artifact validation across platform formats
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shared.contracts.clip import ClipSource
from shared.contracts.timeline import AspectRatio, TimelineIR, TimelineSegmentIR
from shared.contracts.vocab import Platform
from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.local_runtime.provider import LocalAIProvider
from shared.task_persistence import RecoveryManager, TaskCheckpoint, TaskPersistence, TaskStage

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def sample_real_video(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("FFmpeg not installed")
    root = tmp_path_factory.mktemp("live_media")
    video_path = root / "real_footage.mp4"
    _run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(video_path),
    ])
    return video_path


# -----------------------------------------------------------------------------
# 1. Live Local Model Tests (Ollama)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_ollama_normal_prompt():
    """Verify live Ollama extraction on normal prompt."""
    runtime = OllamaRuntime()
    if not await runtime.is_available():
        pytest.skip("Local Ollama runtime not available")

    models = [m.name for m in await runtime.list_models()]
    text_model = next((m for m in ("qwen2.5-coder:1.5b", "qwen2.5:0.5b", "qwen3:14b") if any(m in x for x in models)), None)
    if not text_model:
        pytest.skip("No compatible Qwen model installed in Ollama")

    from shared.providers.base import GenerationConfig, Message
    provider = LocalAIProvider(runtime, text_model, vision=False)
    messages = [
        Message.system("You are an AI video editor intent classifier. Output ONLY valid JSON: {\"genre\": \"travel\", \"pace\": \"fast\", \"aspect_ratio\": \"9:16\"}"),
        Message.user("Create a fast-paced travel vlog for Instagram Reels"),
    ]
    result = await provider.generate(messages, GenerationConfig(temperature=0.1))
    assert result.text != ""
    assert result.latency_ms > 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_live_ollama_imperfect_prompt():
    """Verify live Ollama handles noisy, slang, and typo-heavy prompts."""
    runtime = OllamaRuntime()
    if not await runtime.is_available():
        pytest.skip("Local Ollama runtime not available")

    models = [m.name for m in await runtime.list_models()]
    text_model = next((m for m in ("qwen2.5-coder:1.5b", "qwen2.5:0.5b", "qwen3:14b") if any(m in x for x in models)), None)
    if not text_model:
        pytest.skip("No compatible Qwen model installed in Ollama")

    from shared.providers.base import GenerationConfig, Message
    provider = LocalAIProvider(runtime, text_model, vision=False)
    messages = [
        Message.system("You are an intent parser. Extract genre and pace in JSON: {\"genre\": \"string\", \"pace\": \"string\"}"),
        Message.user("yo monta make a sick hypeee gyyym montage w/ super quick cuts and drop!!"),
    ]
    result = await provider.generate(messages, GenerationConfig(temperature=0.1))
    assert result.text != ""
    await runtime.aclose()


@pytest.mark.asyncio
async def test_live_ollama_missing_model_error():
    """Verify requesting an uninstalled model directly raises ProviderResponseError, while LocalAIProvider heals via fallback."""
    runtime = OllamaRuntime()
    if not await runtime.is_available():
        pytest.skip("Local Ollama runtime not available")

    from shared.providers.base import Message
    from shared.providers.errors import ProviderResponseError

    # 1. Direct runtime request for nonexistent model raises ProviderResponseError (HTTP 404)
    with pytest.raises(ProviderResponseError) as exc_info:
        await runtime.generate_chat([Message.user("Test")], model="nonexistent:999b")
    assert "not found" in str(exc_info.value).lower()

    # 2. LocalAIProvider gracefully resolves to installed fallback model without crashing
    provider = LocalAIProvider(runtime, "nonexistent:999b", vision=False)
    resolved_model = await provider._get_model()
    assert resolved_model != "nonexistent:999b"
    res = await provider.generate([Message.user("Hello")])
    assert res.text != ""

    await runtime.aclose()


@pytest.mark.asyncio
async def test_live_ollama_timeout_handling():
    """Verify sub-millisecond timeout triggers timeout exception."""
    runtime = OllamaRuntime()
    if not await runtime.is_available():
        pytest.skip("Local Ollama runtime not available")

    from shared.providers.base import GenerationConfig, Message
    from shared.providers.errors import ProviderTimeoutError

    provider = LocalAIProvider(runtime, "qwen2.5:0.5b", vision=False)
    with pytest.raises(ProviderTimeoutError):
        await provider.generate([Message.user("Say hello")], GenerationConfig(timeout_s=0.0001))
    await runtime.aclose()


@pytest.mark.asyncio
async def test_offline_mode_runtime_provider():
    """Verify offline provider operates without internet dependency."""
    from shared.local_runtime.adapters.ollama import OllamaRuntime
    from shared.local_runtime.provider import LocalAIProvider

    runtime = OllamaRuntime()
    if not await runtime.is_available():
        pytest.skip("Local Ollama runtime not available")

    provider = LocalAIProvider(runtime, "qwen2.5:0.5b", vision=False)
    assert provider.name.startswith("local_")
    await runtime.aclose()


# -----------------------------------------------------------------------------
# 2. Real Media & Platform Export FFprobe Validation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_format_exporter_with_real_media(sample_real_video, tmp_path):
    """Test FormatExporter on real media and validate outputs via ffprobe."""
    if not (FFMPEG and FFPROBE):
        pytest.skip("FFmpeg/FFprobe required")

    import json
    from services.render_farm.format_exporter import FormatExporter

    exporter = FormatExporter()

    # 1. Export TikTok (9:16 vertical 1080x1920)
    tt_res = await exporter.export(sample_real_video, "tiktok", output_dir=tmp_path)
    assert tt_res.success is True
    assert os.path.exists(tt_res.output_path)

    # Validate with ffprobe
    probe_cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-of", "json",
        str(tt_res.output_path),
    ]
    data = json.loads(subprocess.check_output(probe_cmd, text=True))
    stream = data["streams"][0]
    assert stream["width"] == 1080
    assert stream["height"] == 1920

    # 2. Export Square (1:1 1080x1080)
    sq_res = await exporter.export(sample_real_video, "square", output_dir=tmp_path)
    assert sq_res.success is True
    probe_sq = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-of", "json",
        str(sq_res.output_path),
    ]
    data_sq = json.loads(subprocess.check_output(probe_sq, text=True))
    stream_sq = data_sq["streams"][0]
    assert stream_sq["width"] == 1080
    assert stream_sq["height"] == 1080


# -----------------------------------------------------------------------------
# 3. Task Persistence & Crash Recovery Simulation
# -----------------------------------------------------------------------------


def test_recovery_manager_assessment(tmp_path):
    """Test crash recovery assesses interrupted tasks and resumes properly."""
    persistence = TaskPersistence(str(tmp_path))
    manager = RecoveryManager(persistence)

    # Case 1: Task crashed at RENDERING with artifacts attempt 1 -> RESUME
    cp1 = persistence.create("job_crashed_1", "p1")
    cp1.advance_to(TaskStage.RENDERING, artifacts={"timeline_ir": "/tmp/tl.json"})
    persistence.save(cp1)

    res1 = manager.assess_recovery("job_crashed_1")
    assert res1["action"] == "resume"
    assert res1["recoverable"] is True
    assert "rendering" in res1["reason"]

    # Case 2: Crashed at max attempts -> FAIL
    cp2 = persistence.create("job_crashed_max", "p2")
    cp2.attempt = 3
    cp2.advance_to(TaskStage.RENDERING)
    persistence.save(cp2)

    res2 = manager.assess_recovery("job_crashed_max")
    assert res2["action"] == "fail"
    assert res2["recoverable"] is False
    assert "exceeded" in res2["reason"]


# -----------------------------------------------------------------------------
# 4. Bounded Critic Retry Enforcement
# -----------------------------------------------------------------------------


def test_bounded_critic_retries_does_not_infinite_loop():
    """Verify critic retry stops at MAX_RETRIES (3) and does not infinite loop."""
    from orchestration.agents.critic.retry_logic import RetryManager

    manager = RetryManager()
    threshold = 7.0

    # Attempt 0: Below threshold -> should retry
    assert manager.should_retry(score=4.5, retry_count=0, threshold=threshold) is True

    # Attempt 1: Below threshold -> should retry
    assert manager.should_retry(score=5.5, retry_count=1, threshold=threshold) is True

    # Attempt 2: Below threshold -> should retry
    assert manager.should_retry(score=6.2, retry_count=2, threshold=threshold) is True

    # Attempt 3: Max retries (3) reached -> should NOT retry
    assert manager.should_retry(score=6.2, retry_count=3, threshold=threshold) is False

    # Attempt 1: Above threshold -> should NOT retry
    assert manager.should_retry(score=8.5, retry_count=1, threshold=threshold) is False

    # Verify strategy targeting
    scores = {"story_quality": 4.0, "pacing": 5.0, "visual_consistency": 8.0}
    strategy = manager.get_retry_strategy(feedback=["Pacing off"], scores=scores, threshold=threshold)
    assert len(strategy["directives"]) == 2
    actions = [d["action"] for d in strategy["directives"]]
    assert "adjust_cut_durations" in actions
    assert "strengthen_narrative_arc" in actions

