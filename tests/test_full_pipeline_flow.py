"""
MONTA — Full Active Pipeline Flow Test Suite
==============================================
Validates the complete active pipeline:
prompt → local vision → story → style → timeline → audio/captions → render → critic/revision → export
with TaskPersistence crash recovery.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.timeline_generator.translator import StoryToTimelineTranslator
from shared.contracts.clip import TechnicalMetadata
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
    AudioSegmentIR,
    TimelineIR,
)
from shared.contracts.vocab import Emotion, Genre, Pace, Platform
from shared.task_persistence import TaskPersistence, TaskStage


def _make_test_intent(raw_prompt: str = "cinematic travel video") -> IntentAnalysis:
    return IntentAnalysis(
        raw_prompt=raw_prompt,
        normalized_prompt=raw_prompt,
        genre=Explained(value=Genre.TRAVEL, confidence=0.9, reasoning="test fixture"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.85, reasoning="test fixture"),
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="test fixture"),
        target_platform=Explained(value=Platform.TIKTOK, confidence=0.9, reasoning="test fixture"),
        overall_confidence=0.85,
        extractors=("test:fixture",),
    )


@pytest.fixture
def sample_context(tmp_path):
    clip1 = tmp_path / "clip1.mp4"
    clip2 = tmp_path / "clip2.mp4"
    clip1.touch()
    clip2.touch()

    meta1 = TechnicalMetadata(
        clip_id="c1",
        path=str(clip1),
        duration_s=10.0,
        fps=30.0,
        width=1920,
        height=1080,
        codec="h264",
        has_audio=True,
        file_size_bytes=10000,
        fingerprint="fp1",
    )
    meta2 = TechnicalMetadata(
        clip_id="c2",
        path=str(clip2),
        duration_s=8.0,
        fps=30.0,
        width=1920,
        height=1080,
        codec="h264",
        has_audio=False,
        file_size_bytes=8000,
        fingerprint="fp2",
    )

    platform_spec = PlatformSpec(
        platform=Platform.TIKTOK,
        max_duration_s=60.0,
        ideal_duration_s=(15.0, 30.0),
        aspect_ratio="9:16",
        resolution="1080x1920",
        short_form=True,
    )

    return ContextPack(
        project_id="proj_test",
        user_id="user_1",
        user_prompt="cinematic travel video",
        intent=_make_test_intent(),
        user_preferences=UserPreferences(user_id="user_1"),
        video_metadata=(meta1, meta2),
        platform=platform_spec,
    )


@pytest.fixture
def sample_story():
    cuts = (
        TimelineSegment(
            index=0,
            clip_id="c1",
            act_id="act_intro",
            start=0.0,
            end=3.0,
            source_in=1.0,
            source_out=4.0,
            energy=4.0,
            purpose="intro",
            reasoning="scenic calm opener",
        ),
        TimelineSegment(
            index=1,
            clip_id="c2",
            act_id="act_climax",
            start=3.0,
            end=6.0,
            source_in=2.0,
            source_out=5.0,
            energy=8.5,
            is_hero=True,
            purpose="climax",
            reasoning="high energy hero shot",
        ),
    )

    return StoryPlan(
        story_pattern="three_act_build",
        pattern_version=1,
        story_confidence=0.9,
        act_assignments={"act_intro": ("c1",), "act_climax": ("c2",)},
        timeline=cuts,
        pace=Explained(value=Pace.MEDIUM, confidence=0.9, reasoning="calm build"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.85, reasoning="uplifting"),
        reasoning=StoryReasoning(
            pattern_selection="three_act_build",
            candidates=(),
            adaptations=(),
            pacing="moderate",
            emotion="inspiring",
            clip_decisions=(),
            acts=(),
        ),
        story_score=Score(value=8.5, confidence=0.9, reasoning="strong match"),
        emotion_score=Score(value=8.0, confidence=0.9, reasoning="good emotion"),
        pacing_score=Score(value=8.5, confidence=0.9, reasoning="good pace"),
        validation=ValidationReport(passed=True, rules_checked=(), summary="valid"),
        total_duration_s=6.0,
        target_duration_s=6.0,
        hero_clip_id="c2",
        algorithm_version="v2",
    )


@pytest.mark.asyncio
async def test_montapipeline_soundtrack_generation(sample_context, sample_story):
    """Test MontaPipeline audio soundtrack generation and timeline attachment."""
    from orchestration.pipeline import MontaPipeline

    pipeline = MontaPipeline.__new__(MontaPipeline)
    timeline = pipeline.generate_timeline_ir(sample_story, sample_context)

    assert isinstance(timeline, TimelineIR)
    assert len(timeline.video_segments) == 2
    assert timeline.aspect_ratio == AspectRatio.PORTRAIT_9_16

    # Test build_soundtrack
    bg_file = Path(sample_context.video_metadata[0].path).parent / "bg_music.wav"
    sfx_file = Path(sample_context.video_metadata[0].path).parent / "impact.wav"
    bg_file.touch()
    sfx_file.touch()

    with patch("services.audio_studio.studio.MusicGenerator.generate", new_callable=AsyncMock) as mock_gen, \
         patch("services.audio_studio.studio.SoundFX.generate_fx", new_callable=AsyncMock) as mock_sfx:
        mock_gen.return_value = str(bg_file)
        mock_sfx.return_value = str(sfx_file)

        soundtrack = await pipeline.build_soundtrack(timeline, sample_context)
        assert len(soundtrack) >= 1
        bg = soundtrack[0]
        assert isinstance(bg, AudioSegmentIR)
        assert bg.track_id == "bg_music_main"
        assert bg.loop is True
        assert bg.ducking is True  # Clip c1 has audio so ducking is enabled

        # Re-generate timeline with audio attached
        full_timeline = pipeline.generate_timeline_ir(sample_story, sample_context, audio_segments=soundtrack)
        assert len(full_timeline.audio_segments) == len(soundtrack)


@pytest.mark.asyncio
async def test_montapipeline_critic_evaluation_and_revision(sample_context, sample_story):
    """Test Critic evaluation and bounded revision loop."""
    from orchestration.pipeline import MontaPipeline

    pipeline = MontaPipeline.__new__(MontaPipeline)
    timeline = pipeline.generate_timeline_ir(sample_story, sample_context)

    # Evaluate timeline
    evaluation = await pipeline.evaluate_timeline(timeline, sample_story, sample_context)
    assert "overall_score" in evaluation
    assert "scores" in evaluation
    assert "should_retry" in evaluation
    assert evaluation["scores"]["story_quality"] >= 7.0
    assert evaluation["scores"]["pacing"] >= 7.0


@pytest.mark.asyncio
async def test_task_persistence_stages_in_pipeline():
    """Verify TaskPersistence correctly tracks pipeline lifecycle transitions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        persistence = TaskPersistence(tmp_dir)
        cp = persistence.create("job_123", "proj_456")

        assert cp.stage == TaskStage.PENDING.value
        assert cp.is_complete is False

        # Advance through stages
        cp.advance_to(TaskStage.ANALYZING)
        persistence.save(cp)
        assert persistence.load("job_123").stage == TaskStage.ANALYZING.value

        cp.advance_to(TaskStage.STORY_DESIGN)
        persistence.save(cp)
        assert persistence.load("job_123").stage == TaskStage.STORY_DESIGN.value

        cp.advance_to(TaskStage.TIMELINE_GENERATION)
        persistence.save(cp)
        assert persistence.load("job_123").stage == TaskStage.TIMELINE_GENERATION.value

        cp.advance_to(TaskStage.RENDERING)
        persistence.save(cp)
        assert persistence.load("job_123").stage == TaskStage.RENDERING.value

        cp.advance_to(TaskStage.EXPORTING)
        persistence.save(cp)
        assert persistence.load("job_123").stage == TaskStage.EXPORTING.value

        cp.mark_complete(artifacts={"master": "/tmp/out.mp4", "score": 8.7})
        persistence.save(cp)
        loaded = persistence.load("job_123")
        assert loaded.is_complete is True
        assert loaded.stage == TaskStage.COMPLETE.value
        assert loaded.artifacts["score"] == 8.7


@pytest.mark.asyncio
async def test_platform_export_pipeline_step(tmp_path):
    """Test FormatExporter can produce vertical social exports."""
    from services.render_farm.format_exporter import FormatExporter

    src_file = tmp_path / "master.mp4"
    src_file.write_bytes(b"dummy mp4 content")

    exporter = FormatExporter()
    with patch("asyncio.create_subprocess_exec") as mock_exec, \
         patch("services.render_farm.format_exporter.FormatExporter._probe_source") as mock_probe:
        mock_probe.return_value = (1920, 1080, 25.0)

        async def fake_subprocess(*args, **kwargs):
            # Create the expected output file when ffmpeg is invoked
            out_file = tmp_path / "master_tiktok.mp4"
            out_file.write_bytes(b"exported mp4 content")
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            return mock_proc

        mock_exec.side_effect = fake_subprocess

        result = await exporter.export(
            source_path=str(src_file),
            platform="tiktok",
            output_dir=str(tmp_path),
        )
        assert result.success is True
        assert result.platform == "tiktok"
        assert result.width == 1080
        assert result.height == 1920
        assert mock_exec.called
