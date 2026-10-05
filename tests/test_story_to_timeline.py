"""
Tests for StoryToTimelineTranslator.
"""

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
from shared.contracts.timeline import AspectRatio, ScaleMode, TransitionType
from shared.contracts.vocab import Emotion, Genre, Pace, Platform


def _make_test_intent(raw_prompt: str = "cinematic travel video") -> IntentAnalysis:
    """Build a fully-populated IntentAnalysis for test use."""
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


def test_story_to_timeline_translation(tmp_path):
    clip1_path = str(tmp_path / "clip1.mp4")
    clip2_path = str(tmp_path / "clip2.mp4")

    # Create metadata
    meta1 = TechnicalMetadata(
        clip_id="c1",
        path=clip1_path,
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
        path=clip2_path,
        duration_s=8.0,
        fps=30.0,
        width=1920,
        height=1080,
        codec="h264",
        has_audio=True,
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

    pack = ContextPack(
        project_id="proj_test",
        user_id="user_1",
        user_prompt="cinematic travel video",
        intent=_make_test_intent(),
        user_preferences=UserPreferences(user_id="user_1"),
        video_metadata=(meta1, meta2),
        platform=platform_spec,
    )

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

    story = StoryPlan(
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

    timeline_ir = StoryToTimelineTranslator.translate(story, pack)

    # Verifications
    assert timeline_ir.project_id == "proj_test"
    # Target platform is TikTok -> 9:16 portrait
    assert timeline_ir.aspect_ratio == AspectRatio.PORTRAIT_9_16
    assert timeline_ir.width == 1080
    assert timeline_ir.height == 1920
    assert len(timeline_ir.video_segments) == 2

    seg1 = timeline_ir.video_segments[0]
    assert seg1.clip_id == "c1"
    assert seg1.source_path == clip1_path
    assert seg1.source_in_ms == 1000
    assert seg1.source_out_ms == 4000
    assert seg1.timeline_start_ms == 0
    assert seg1.timeline_end_ms == 3000

    seg2 = timeline_ir.video_segments[1]
    assert seg2.clip_id == "c2"
    assert seg2.source_path == clip2_path
    assert seg2.source_in_ms == 2000
    assert seg2.source_out_ms == 5000
    assert seg2.is_hero
