"""Tests for Layer 8: Style Engine integration with Timeline Generator and Compiler."""

import pytest

from services.edit_executor.compiler import FFmpegCompiler
from services.style_engine.mapper import StyleMapper
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
    ColorAdjustment,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)
from shared.contracts.vocab import (
    CaptionStyle,
    ColorGrade,
    Emotion,
    Genre,
    MusicStyle,
    Pace,
    Platform,
    StoryRole,
)


def _sample_pack(color_grade=None) -> ContextPack:
    meta = TechnicalMetadata(
        clip_id="c1",
        path="/media/clip1.mp4",
        duration_s=6.0,
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264",
        bitrate_kbps=5000,
        has_audio=True,
        file_size_bytes=1024,
        fingerprint="fp1",
    )
    meta2 = TechnicalMetadata(
        clip_id="c2",
        path="/media/clip2.mp4",
        duration_s=6.0,
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264",
        bitrate_kbps=5000,
        has_audio=True,
        file_size_bytes=1024,
        fingerprint="fp2",
    )
    intent = IntentAnalysis(
        raw_prompt="Make a cinematic reel",
        normalized_prompt="make a cinematic reel",
        genre=Explained(value=Genre.TRAVEL, confidence=0.9, reasoning="test"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.85, reasoning="test"),
        color_grade=Explained(value=ColorGrade(color_grade), confidence=0.9, reasoning="test") if color_grade else None,
        pace=Explained(value=Pace.MEDIUM, confidence=0.9, reasoning="test"),
        target_platform=Explained(value=Platform.TIKTOK, confidence=0.9, reasoning="test"),
        target_duration_s=Explained(value=6.0, confidence=0.9, reasoning="test"),
        overall_confidence=0.85,
        extractors=("test:fixture",),
    )
    return ContextPack(
        project_id="style_test_proj",
        user_id="user_1",
        user_prompt="Make a cinematic reel",
        user_preferences=UserPreferences(user_id="user_1"),
        intent=intent,
        platform=PlatformSpec(
            platform=Platform.TIKTOK,
            max_duration_s=60.0,
            ideal_duration_s=(15.0, 30.0),
            aspect_ratio="9:16",
            resolution="1080x1920",
            short_form=True,
        ),
        video_metadata=(meta, meta2),
    )


def _make_story(hero_clip: str = "c2") -> StoryPlan:
    cuts = (
        TimelineSegment(
            index=0,
            clip_id="c1",
            act_id="act_intro",
            start=0.0,
            end=3.0,
            source_in=0.0,
            source_out=3.0,
            energy=4.0,
            purpose="intro",
            reasoning="scenic opener",
        ),
        TimelineSegment(
            index=1,
            clip_id="c2",
            act_id="act_climax",
            start=3.0,
            end=6.0,
            source_in=0.0,
            source_out=3.0,
            energy=8.5,
            is_hero=True,
            purpose="climax",
            reasoning="hero climax",
        ),
    )
    return StoryPlan(
        story_pattern="three_act_build",
        pattern_version=1,
        story_confidence=0.9,
        act_assignments={"act_intro": ("c1",), "act_climax": ("c2",)},
        timeline=cuts,
        pace=Explained(value=Pace.MEDIUM, confidence=0.9, reasoning="medium pace"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.85, reasoning="inspiring"),
        reasoning=StoryReasoning(
            pattern_selection="three_act_build",
            candidates=(),
            adaptations=(),
            pacing="moderate",
            emotion="inspiring",
            clip_decisions=(),
            acts=(),
        ),
        story_score=Score(value=8.5, confidence=0.9, reasoning="good"),
        emotion_score=Score(value=8.0, confidence=0.9, reasoning="good"),
        pacing_score=Score(value=8.5, confidence=0.9, reasoning="good"),
        validation=ValidationReport(passed=True, rules_checked=(), summary="valid"),
        total_duration_s=6.0,
        target_duration_s=6.0,
        hero_clip_id=hero_clip,
        algorithm_version="v2",
    )


def test_style_mapper_resolves_presets():
    mapper = StyleMapper()
    cinematic = mapper.get_preset("cinematic")
    assert cinematic["color_grade"] == "orange_teal"
    assert "orchestral" in cinematic["music"]

    hype = mapper.get_preset("hype")
    assert hype["cut_speed"] == "aggressive"
    assert hype["color_grade"] == "high_contrast"


def test_style_mapper_color_adjustments():
    ot_adj = StyleMapper.resolve_color_adjustment("orange_teal")
    assert ot_adj is not None
    assert ot_adj.contrast > 1.0
    assert ot_adj.saturation > 1.0

    dark_adj = StyleMapper.resolve_color_adjustment("dark")
    assert dark_adj is not None
    assert dark_adj.brightness < 0.0

    none_adj = StyleMapper.resolve_color_adjustment(None)
    assert none_adj is None


def test_story_to_timeline_applies_style_engine_color_and_scale():
    pack = _sample_pack(color_grade="orange_teal")
    story = _make_story()

    style_override = {
        "color_grade": "orange_teal",
        "transitions": "crossfade",
        "transition_ms": 300,
        "scale_mode": "fill",
    }
    timeline = StoryToTimelineTranslator.translate(story, pack, style_override=style_override)

    assert timeline.scale_mode == ScaleMode.FILL
    assert timeline.video_segments[0].color is not None
    assert timeline.video_segments[0].color.contrast == 1.18
    # Second segment should have crossfade transition
    assert timeline.video_segments[1].transition_in == TransitionType.CROSSFADE
    assert timeline.video_segments[1].transition_in_ms == 300


def test_ffmpeg_compiler_compiles_styled_filters(tmp_path):
    pack = _sample_pack()
    story = _make_story()
    style_override = {
        "color_grade": "vibrant",
        "scale_mode": "fill",
    }
    timeline = StoryToTimelineTranslator.translate(story, pack, style_override=style_override)

    compiler = FFmpegCompiler()
    cmd = compiler.compile(timeline, output_path=tmp_path / "styled.mp4", force_cpu=True)

    filter_graph = cmd.filter_graph
    assert "contrast=1.120" in filter_graph
    assert "saturation=1.250" in filter_graph
    assert "crop=1080:1920" in filter_graph  # ScaleMode.FILL applied
