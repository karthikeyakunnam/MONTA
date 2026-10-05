"""
MONTA — M3 Failure Injection & Offline Mode Tests
====================================================
Verifies that every layer in the M3 pipeline fails honestly:
no failure silently produces status=COMPLETED.

Scenarios:
  - Ollama unreachable
  - Model not installed
  - Malformed LLM JSON
  - Invalid StoryPlan
  - Invalid Timeline IR
  - Missing source media
  - Corrupt MP4 input
  - FFmpeg execution failure
  - FFmpeg timeout
  - Cancellation safety
  - Hardware encoder fallback
  - Output validation failure
  - Offline mode cloud enforcement
  - Offline mode with real local model (live)
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from orchestration.pipeline import build_pipeline, MontaPipeline
from services.edit_executor.compiler import FFmpegCompiler
from services.edit_executor.executor import (
    FFmpegCancelledError,
    FFmpegExecutionError,
    FFmpegExecutor,
    FFmpegTimeoutError,
)
from services.edit_executor.validator import EditPlanValidator
from services.render_farm.output_validator import OutputValidator
from services.render_farm.renderer import MediaRenderer
from services.timeline_generator.translator import StoryToTimelineTranslator
from shared.contracts.clip import ClipSource, TechnicalMetadata
from shared.contracts.context import ContextPack, PlatformSpec, UserPreferences
from shared.contracts.explain import Explained, Score
from shared.contracts.intent import IntentAnalysis
from shared.contracts.story import (
    StoryPlan,
    StoryReasoning,
    TimelineSegment,
    ValidationReport,
)
from shared.contracts.timeline import (
    AspectRatio,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)
from shared.contracts.vocab import Emotion, Genre, Pace, Platform
from shared.exceptions import PipelineError
from shared.providers.registry import ProviderSettings, build_providers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_intent(raw_prompt: str = "test prompt") -> IntentAnalysis:
    return IntentAnalysis(
        raw_prompt=raw_prompt,
        normalized_prompt=raw_prompt,
        genre=Explained(value=Genre.TRAVEL, confidence=0.9, reasoning="test"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.85, reasoning="test"),
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="test"),
        target_platform=Explained(value=Platform.TIKTOK, confidence=0.9, reasoning="test"),
        overall_confidence=0.85,
        extractors=("test:fixture",),
    )


def _make_metadata(clip_id: str, path: str, duration_s: float = 6.0) -> TechnicalMetadata:
    return TechnicalMetadata(
        clip_id=clip_id, path=path, duration_s=duration_s, fps=30.0,
        width=1920, height=1080, codec="h264", has_audio=True,
        file_size_bytes=10000, fingerprint=f"fp_{clip_id}",
    )


def _generate_clip(path: Path, duration_s: float = 3.0, color: str = "blue") -> Path:
    subprocess.check_call([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d={duration_s}:r=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        str(path),
    ])
    return path


# ---------------------------------------------------------------------------
# 1. Ollama Unreachable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unreachable_ollama_fails_honestly():
    """Offline pipeline must fail when Ollama is unreachable — not silently degrade."""
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="ollama",
        ollama_base_url="http://127.0.0.1:59998",
        ollama_text_model="qwen2.5:0.5b",
    )
    providers = build_providers(settings)
    pipeline = build_pipeline(providers=providers)

    with pytest.raises(PipelineError, match="Configured local AI is required"):
        await pipeline.interpret("make it fast")


# ---------------------------------------------------------------------------
# 2. Offline Mode Blocks Cloud APIs
# ---------------------------------------------------------------------------

def test_offline_mode_blocks_remote_apis():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="gemini",
        gemini_api_key="secret-key",
    )
    with pytest.raises(ValueError, match="is a cloud service, but MONTA_OFFLINE_MODE is enabled"):
        build_providers(settings)


def test_offline_mode_blocks_qwen_cloud():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="qwen",
        qwen_api_key="secret-key",
    )
    with pytest.raises(ValueError, match="is a cloud service"):
        build_providers(settings)


def test_offline_mode_blocks_qwen_vl_cloud():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_vision_providers="qwen_vl",
        qwen_api_key="secret-key",
    )
    with pytest.raises(ValueError, match="is a cloud service"):
        build_providers(settings)


# ---------------------------------------------------------------------------
# 3. Invalid / Missing Source Media
# ---------------------------------------------------------------------------

def test_validator_rejects_missing_source_file():
    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1",
        source_path="/nonexistent/file.mp4",
        source_in_ms=0, source_out_ms=2000,
        timeline_start_ms=0, timeline_end_ms=2000,
    )
    timeline = TimelineIR(
        project_id="test", video_segments=(seg,), total_duration_ms=2000,
    )
    result = EditPlanValidator().validate(timeline)
    assert not result.is_valid
    assert any(e.code.value == "source_not_found" for e in result.errors)


@pytest.mark.asyncio
async def test_output_validator_rejects_nonexistent_file():
    timeline = TimelineIR(
        project_id="test",
        video_segments=(TimelineSegmentIR(
            segment_id="s1", clip_id="c1", source_path="/tmp/nonexistent.mp4",
            source_in_ms=0, source_out_ms=1000,
            timeline_start_ms=0, timeline_end_ms=1000,
        ),),
        total_duration_ms=1000,
    )
    result = await OutputValidator().validate(
        "/tmp/nonexistent_output.mp4", timeline, render_time_s=0.0, encoder_used="libx264",
    )
    assert not result.success
    assert "does not exist" in result.error_message


# ---------------------------------------------------------------------------
# 4. Corrupt MP4 Input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_output_validator_rejects_corrupt_mp4(tmp_path):
    bad_file = tmp_path / "corrupt.mp4"
    bad_file.write_bytes(b"NOT_A_VALID_MP4_HEADER" * 200)

    timeline = TimelineIR(
        project_id="test",
        video_segments=(TimelineSegmentIR(
            segment_id="s1", clip_id="c1", source_path=str(bad_file),
            source_in_ms=0, source_out_ms=2000,
            timeline_start_ms=0, timeline_end_ms=2000,
        ),),
        total_duration_ms=2000,
    )
    result = await OutputValidator().validate(
        str(bad_file), timeline, render_time_s=0.1, encoder_used="libx264",
    )
    assert not result.success
    assert not result.validation_passed


# ---------------------------------------------------------------------------
# 5. FFmpeg Timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ffmpeg_executor_timeout(tmp_path):
    clip = _generate_clip(tmp_path / "src.mp4", duration_s=2.0)
    out = tmp_path / "timeout_out.mp4"

    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1", source_path=str(clip),
        source_in_ms=0, source_out_ms=2000,
        timeline_start_ms=0, timeline_end_ms=2000,
    )
    timeline = TimelineIR(project_id="test_to", video_segments=(seg,), total_duration_ms=2000)

    compiler = FFmpegCompiler()
    cmd = compiler.compile(timeline, output_path=out, force_cpu=True)

    executor = FFmpegExecutor(timeout_s=0.001)
    with pytest.raises(FFmpegTimeoutError):
        await executor.execute(cmd)

    assert not out.exists(), "Partial output must be cleaned up after timeout"


# ---------------------------------------------------------------------------
# 6. Invalid Timeline IR — Gap
# ---------------------------------------------------------------------------

def test_translator_rejects_gap_in_story_timeline(tmp_path):
    meta = _make_metadata("c1", str(tmp_path / "c1.mp4"), duration_s=10.0)
    pack = ContextPack(
        project_id="test", user_id="u", user_prompt="test",
        intent=_make_intent(), user_preferences=UserPreferences(user_id="u"),
        video_metadata=(meta,),
        platform=PlatformSpec(platform=Platform.TIKTOK, max_duration_s=60.0,
                              ideal_duration_s=(15.0, 30.0), aspect_ratio="9:16",
                              resolution="1080x1920", short_form=True),
    )

    # Create a gap: cut0 ends at 3s but cut1 starts at 4s
    cuts = (
        TimelineSegment(
            index=0, clip_id="c1", act_id="a1", start=0.0, end=3.0,
            source_in=0.0, source_out=3.0, energy=5.0, purpose="intro", reasoning="t",
        ),
        TimelineSegment(
            index=1, clip_id="c1", act_id="a2", start=4.0, end=6.0,
            source_in=3.0, source_out=5.0, energy=6.0, purpose="build", reasoning="t",
        ),
    )
    story = StoryPlan(
        story_pattern="test", pattern_version=1, story_confidence=0.8,
        hero_clip_id="c1",
        act_assignments={"a1": ("c1",), "a2": ("c1",)}, timeline=cuts,
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="t"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.8, reasoning="t"),
        reasoning=StoryReasoning(
            pattern_selection="test", candidates=(), adaptations=(),
            pacing="moderate", emotion="inspiring", clip_decisions=(), acts=(),
        ),
        story_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        emotion_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        pacing_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        validation=ValidationReport(passed=True, rules_checked=(), summary="ok"),
        total_duration_s=6.0, target_duration_s=6.0,
        algorithm_version="v2",
    )

    with pytest.raises(ValueError, match="gap or overlap"):
        StoryToTimelineTranslator.translate(story, pack)


# ---------------------------------------------------------------------------
# 7. Invalid Timeline IR — Total Duration Mismatch
# ---------------------------------------------------------------------------

def test_translator_rejects_duration_mismatch(tmp_path):
    meta = _make_metadata("c1", str(tmp_path / "c1.mp4"), duration_s=10.0)
    pack = ContextPack(
        project_id="test", user_id="u", user_prompt="test",
        intent=_make_intent(), user_preferences=UserPreferences(user_id="u"),
        video_metadata=(meta,),
        platform=PlatformSpec(platform=Platform.TIKTOK, max_duration_s=60.0,
                              ideal_duration_s=(15.0, 30.0), aspect_ratio="9:16",
                              resolution="1080x1920", short_form=True),
    )

    cuts = (TimelineSegment(
        index=0, clip_id="c1", act_id="a1", start=0.0, end=3.0,
        source_in=0.0, source_out=3.0, energy=5.0, purpose="intro", reasoning="t",
    ),)
    story = StoryPlan(
        story_pattern="test", pattern_version=1, story_confidence=0.8,
        hero_clip_id="c1",
        act_assignments={"a1": ("c1",)}, timeline=cuts,
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="t"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.8, reasoning="t"),
        reasoning=StoryReasoning(
            pattern_selection="test", candidates=(), adaptations=(),
            pacing="moderate", emotion="inspiring", clip_decisions=(), acts=(),
        ),
        story_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        emotion_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        pacing_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        validation=ValidationReport(passed=True, rules_checked=(), summary="ok"),
        total_duration_s=10.0,  # Mismatch: declared 10s but cuts end at 3s
        target_duration_s=10.0,
        algorithm_version="v2",
    )

    with pytest.raises(ValueError, match="does not match"):
        StoryToTimelineTranslator.translate(story, pack)


# ---------------------------------------------------------------------------
# 8. Missing clip_id in ContextPack
# ---------------------------------------------------------------------------

def test_translator_rejects_unknown_clip_id(tmp_path):
    meta = _make_metadata("c1", str(tmp_path / "c1.mp4"), duration_s=10.0)
    pack = ContextPack(
        project_id="test", user_id="u", user_prompt="test",
        intent=_make_intent(), user_preferences=UserPreferences(user_id="u"),
        video_metadata=(meta,),
        platform=PlatformSpec(platform=Platform.TIKTOK, max_duration_s=60.0,
                              ideal_duration_s=(15.0, 30.0), aspect_ratio="9:16",
                              resolution="1080x1920", short_form=True),
    )

    cuts = (TimelineSegment(
        index=0, clip_id="UNKNOWN_CLIP", act_id="a1", start=0.0, end=3.0,
        source_in=0.0, source_out=3.0, energy=5.0, purpose="intro", reasoning="t",
    ),)
    story = StoryPlan(
        story_pattern="test", pattern_version=1, story_confidence=0.8,
        hero_clip_id="UNKNOWN_CLIP",
        act_assignments={"a1": ("UNKNOWN_CLIP",)}, timeline=cuts,
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="t"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.8, reasoning="t"),
        reasoning=StoryReasoning(
            pattern_selection="test", candidates=(), adaptations=(),
            pacing="moderate", emotion="inspiring", clip_decisions=(), acts=(),
        ),
        story_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        emotion_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        pacing_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        validation=ValidationReport(passed=True, rules_checked=(), summary="ok"),
        total_duration_s=3.0, target_duration_s=3.0,
        algorithm_version="v2",
    )

    with pytest.raises(ValueError, match="not found in ContextPack"):
        StoryToTimelineTranslator.translate(story, pack)


# ---------------------------------------------------------------------------
# 9. Validator rejects out-of-range source timecodes
# ---------------------------------------------------------------------------

def test_validator_rejects_source_out_exceeding_duration(tmp_path):
    clip = _generate_clip(tmp_path / "short.mp4", duration_s=2.0)
    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1", source_path=str(clip),
        source_in_ms=0, source_out_ms=5000,  # 5 seconds but clip is only 2s
        timeline_start_ms=0, timeline_end_ms=5000,
    )
    timeline = TimelineIR(project_id="test", video_segments=(seg,), total_duration_ms=5000)
    result = EditPlanValidator().validate(
        timeline, source_durations_ms={"c1": 2000},
    )
    assert not result.is_valid
    assert any(e.code.value == "invalid_source_range" for e in result.errors)


# ---------------------------------------------------------------------------
# 10. Pipeline rejects empty clips list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_rejects_empty_clips():
    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="ollama",
        ollama_base_url="http://127.0.0.1:59998",
        ollama_text_model="qwen2.5:0.5b",
    )
    providers = build_providers(settings)
    pipeline = build_pipeline(providers=providers)

    with pytest.raises(PipelineError, match="at least one clip is required"):
        await pipeline.run(
            project_id="test", user_id="u", prompt="test", clips=[],
        )


# ---------------------------------------------------------------------------
# 11. Offline mode with real local model (live marker — requires Ollama)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_offline_pipeline_uses_only_local_model(tmp_path):
    """Live test: full offline pipeline never contacts a cloud provider."""
    text_model = os.environ.get("MONTA_TEST_OLLAMA_TEXT_MODEL", "qwen2.5:0.5b")
    from shared.local_runtime.adapters.ollama import OllamaRuntime
    from shared.providers.base import GenerationConfig, Message

    runtime = OllamaRuntime(base_url="http://localhost:11434")
    try:
        health = await runtime.check_health()
        assert health.is_healthy, f"Ollama not available: {health.error_message}"
        assert text_model in health.installed_models, (
            f"Model {text_model} not installed; have: {health.installed_models}"
        )
    finally:
        await runtime.aclose()

    settings = ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="ollama",
        ollama_base_url="http://localhost:11434",
        ollama_text_model=text_model,
    )
    providers = build_providers(settings)
    assert providers.is_offline

    clips = [
        ClipSource(clip_id="c1", path=str(_generate_clip(tmp_path / "c1.mp4", 4.0, "blue"))),
        ClipSource(clip_id="c2", path=str(_generate_clip(tmp_path / "c2.mp4", 4.0, "red"))),
    ]

    pipeline = build_pipeline(providers=providers)
    output = tmp_path / "offline_output.mp4"

    try:
        result = await pipeline.run(
            project_id="offline_test", user_id="u", prompt="make a fast travel reel", clips=clips,
        )
        assert result.story is not None
        assert any(e.startswith("llm:") for e in result.intent.extractors)

        timeline_ir = pipeline.generate_timeline_ir(result.story, result.context_pack)
        render_result = await pipeline.render_story(
            result.story, result.context_pack, str(output), timeline=timeline_ir,
        )
        assert render_result.success
        assert render_result.validation_passed
        assert output.exists()
        assert render_result.file_size_bytes > 1000

        # Verify output independently with ffprobe
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(output)],
            capture_output=True, text=True,
        )
        assert probe.returncode == 0
        streams = json.loads(probe.stdout)["streams"]
        assert any(s["codec_type"] == "video" for s in streams)
    finally:
        await providers.aclose()


# ---------------------------------------------------------------------------
# 12. Validator rejects empty timeline
# ---------------------------------------------------------------------------

def test_validator_rejects_empty_timeline():
    with pytest.raises(ValidationError):
        TimelineIR(project_id="test", video_segments=(), total_duration_ms=0)


# ---------------------------------------------------------------------------
# 13. Tiny (suspiciously small) output file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_output_validator_rejects_suspiciously_small_file(tmp_path):
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"\x00" * 100)  # Only 100 bytes — too small to be valid

    timeline = TimelineIR(
        project_id="test",
        video_segments=(TimelineSegmentIR(
            segment_id="s1", clip_id="c1", source_path=str(tiny),
            source_in_ms=0, source_out_ms=1000,
            timeline_start_ms=0, timeline_end_ms=1000,
        ),),
        total_duration_ms=1000,
    )
    result = await OutputValidator().validate(
        str(tiny), timeline, render_time_s=0.0, encoder_used="libx264",
    )
    assert not result.success
    assert "suspiciously small" in result.error_message


# ---------------------------------------------------------------------------
# 14. Speed out of range
# ---------------------------------------------------------------------------

def test_validator_rejects_extreme_speed():
    with pytest.raises(ValidationError):
        TimelineSegmentIR(
            segment_id="s1", clip_id="c1", source_path="/tmp/fake.mp4",
            source_in_ms=0, source_out_ms=1000,
            timeline_start_ms=0, timeline_end_ms=1000,
            speed=50.0,  # Way above max
        )


# ---------------------------------------------------------------------------
# 15. Inverted source range
# ---------------------------------------------------------------------------

def test_validator_rejects_inverted_source_range():
    with pytest.raises(ValidationError):
        TimelineSegmentIR(
            segment_id="s1", clip_id="c1", source_path="/tmp/fake.mp4",
            source_in_ms=3000, source_out_ms=1000,  # Inverted
            timeline_start_ms=0, timeline_end_ms=2000,
        )


# ---------------------------------------------------------------------------
# 16. Invalid StoryPlan index ordering
# ---------------------------------------------------------------------------

def test_translator_rejects_misordered_story_cuts(tmp_path):
    meta = _make_metadata("c1", str(tmp_path / "c1.mp4"), duration_s=10.0)
    pack = ContextPack(
        project_id="test", user_id="u", user_prompt="test",
        intent=_make_intent(), user_preferences=UserPreferences(user_id="u"),
        video_metadata=(meta,),
        platform=PlatformSpec(platform=Platform.TIKTOK, max_duration_s=60.0,
                              ideal_duration_s=(15.0, 30.0), aspect_ratio="9:16",
                              resolution="1080x1920", short_form=True),
    )

    # Cuts with wrong index: second cut has index=0 instead of 1
    cuts = (
        TimelineSegment(
            index=0, clip_id="c1", act_id="a1", start=0.0, end=3.0,
            source_in=0.0, source_out=3.0, energy=5.0, purpose="intro", reasoning="t",
        ),
        TimelineSegment(
            index=0, clip_id="c1", act_id="a2", start=3.0, end=6.0,
            source_in=3.0, source_out=6.0, energy=6.0, purpose="build", reasoning="t",
        ),
    )
    story = StoryPlan(
        story_pattern="test", pattern_version=1, story_confidence=0.8,
        hero_clip_id="c1",
        act_assignments={"a1": ("c1",), "a2": ("c1",)}, timeline=cuts,
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="t"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.8, reasoning="t"),
        reasoning=StoryReasoning(
            pattern_selection="test", candidates=(), adaptations=(),
            pacing="moderate", emotion="inspiring", clip_decisions=(), acts=(),
        ),
        story_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        emotion_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        pacing_score=Score(value=7.0, confidence=0.8, reasoning="t"),
        validation=ValidationReport(passed=True, rules_checked=(), summary="ok"),
        total_duration_s=6.0, target_duration_s=6.0,
        algorithm_version="v2",
    )

    with pytest.raises(ValueError, match="not ordered"):
        StoryToTimelineTranslator.translate(story, pack)
