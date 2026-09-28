"""Layer 3 — Prompt Intelligence: messy language, confidence, ambiguity, conflicts, LLM ensemble, footage reconciliation."""

import json

import pytest

from services.prompt_engine import IntentEngine
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from services.prompt_engine.normalizer import normalize, osa_distance
from shared.contracts.vocab import CaptionStyle, ColorGrade, Emotion, Genre, MusicStyle, Pace, Platform, StoryRole
from shared.providers.errors import ProviderUnavailableError
from tests.conftest import ScriptedProvider, make_clip

lex = LexicalIntentExtractor()

NIKE = "make this feel like nike ad slow start then huge motivation ending use dark colors and aggressive cuts"


def test_flagship_prompt():
    a = lex.extract(NIKE)
    assert a.pace.value == Pace.AGGRESSIVE and a.pace.confidence > 0.6
    assert a.emotion.value == Emotion.MOTIVATIONAL
    assert a.color_grade.value == ColorGrade.DARK
    assert a.caption_style.value == CaptionStyle.NIKE
    assert a.reference_style.value == "nike"
    start, end = a.arc_for("start"), a.arc_for("end")
    assert start.pace == Pace.SLOW and "slow start" in start.span
    assert end.emotion == Emotion.MOTIVATIONAL
    assert not a.conflicts, "slow start + aggressive cuts is an arc, not a conflict"
    assert [m.field for m in a.missing] == ["target_platform"]
    assert a.target_platform.is_default


def test_every_field_explains_itself():
    a = lex.extract(NIKE)
    for f in (a.genre, a.emotion, a.pace, a.target_platform, a.color_grade, a.caption_style):
        assert f.reasoning and 0 <= f.confidence <= 1
    assert any(e.span == "aggressive cuts" for e in a.pace.evidence)


def test_typos_and_slang():
    a = lex.extract("yo make my gym vid fsat and agressive for insta, 30 sec, motivaton captions pls")
    fixes = {(c.original, c.corrected) for c in a.corrections}
    assert {("fsat", "fast"), ("agressive", "aggressive"), ("motivaton", "motivation"), ("insta", "instagram")} <= fixes
    assert a.genre.value == Genre.FITNESS
    assert a.pace.value in (Pace.FAST, Pace.AGGRESSIVE)
    assert a.target_platform.value == Platform.INSTAGRAM and not a.target_platform.is_default
    assert a.target_duration_s.value == 30
    assert a.caption_style.value == CaptionStyle.MOTIVATIONAL
    assert any("spelling-corrected" in e.detail for e in a.pace.evidence)


def test_common_words_are_never_corrected():
    assert all(c.original not in ("make", "this", "feel", "like") for c in normalize("make this feel like home").corrections)


def test_osa_distance_handles_transpositions():
    assert osa_distance("nkie", "nike") == 1
    assert osa_distance("slwo", "slow") == 1
    assert osa_distance("abc", "xyz", limit=2) == 3


def test_negation_implies_medium_pace():
    a = lex.extract("not too fast, emotional wedding video with piano")
    assert a.pace.value == Pace.MEDIUM
    assert a.genre.value == Genre.WEDDING
    assert a.music_style.value == MusicStyle.EMOTIONAL_PIANO


def test_hard_conflict_on_contradictory_global_pace():
    a = lex.extract("slow and fast cuts for my travel video")
    hard = [c for c in a.conflicts if c.severity == "hard"]
    assert hard and hard[0].fields == ("pace",)
    assert a.needs_clarification or a.overall_confidence < 0.6


def test_soft_cross_dimension_conflict():
    a = lex.extract("calm travel vlog but also super fast cuts")
    assert any(c.severity == "soft" and set(c.values) == {"calm", "fast"} for c in a.conflicts)


def test_ambiguity_between_close_candidates():
    a = lex.extract("make it emotional and energetic")
    assert any(x.field == "emotion" and set(x.candidates) == {"emotional", "energetic"} for x in a.ambiguities)


def test_vague_prompt_marks_missing_and_low_confidence():
    a = lex.extract("make it cool")
    assert {m.field for m in a.missing} == {"genre", "emotion", "pace", "target_platform"}
    assert any(x.field == "overall" for x in a.ambiguities)
    assert a.needs_clarification


def test_empty_prompt_is_accepted():
    a = lex.extract("")
    assert a.genre.is_default and a.overall_confidence < 0.3


def test_decade_reference_is_not_a_duration():
    a = lex.extract("90s vibe skate montage")
    assert a.target_duration_s is None
    assert a.color_grade.value == ColorGrade.VINTAGE


def test_platform_longest_match_wins():
    assert lex.extract("youtube shorts edit").target_platform.value == Platform.YOUTUBE_SHORT
    assert lex.extract("tiktok edit").target_platform.value == Platform.TIKTOK


# ---------------------------------------------------------------- ensemble

def _llm_json(**fields) -> str:
    return json.dumps(fields)


async def test_no_llm_is_marked_degraded():
    a = await IntentEngine().analyze(NIKE)
    assert a.extractors == ("lexical:lexicon.v1",)
    assert any("no text LLM" in d for d in a.degraded)


async def test_llm_agreement_raises_confidence_and_fills_gaps():
    llm = ScriptedProvider([_llm_json(
        pace={"value": "aggressive", "confidence": 0.9, "reasoning": "'aggressive cuts'"},
        target_platform={"value": "instagram", "confidence": 0.6, "reasoning": "'reel'-style ad"},
    )], vision=False)
    lexical = lex.extract(NIKE)
    a = await IntentEngine(llm).analyze(NIKE)
    assert a.pace.confidence > lexical.pace.confidence
    assert not a.target_platform.is_default
    assert a.target_platform.confidence == pytest.approx(0.6 * 0.85)
    assert "target_platform" not in {m.field for m in a.missing}
    assert a.extractors[-1].startswith("llm:")


async def test_llm_disagreement_records_ambiguity():
    llm = ScriptedProvider([_llm_json(color_grade={"value": "neon", "confidence": 0.95, "reasoning": "guess"})], vision=False)
    a = await IntentEngine(llm).analyze(NIKE)
    assert a.color_grade.value == ColorGrade.NEON
    assert a.color_grade.confidence < 0.95
    assert any(x.field == "color_grade" for x in a.ambiguities)


async def test_llm_failure_falls_back_to_lexical():
    llm = ScriptedProvider([ProviderUnavailableError("down")], vision=False)
    a = await IntentEngine(llm).analyze(NIKE)
    assert a.pace.value == Pace.AGGRESSIVE
    assert any("LLM extraction failed" in d for d in a.degraded)


async def test_llm_invalid_enum_is_repaired():
    llm = ScriptedProvider([
        _llm_json(pace={"value": "turbo", "confidence": 0.9, "reasoning": "x"}),
        _llm_json(pace={"value": "fast", "confidence": 0.9, "reasoning": "x"}),
    ], vision=False)
    a = await IntentEngine(llm).analyze("make it snappy")
    assert a.pace.value == Pace.FAST
    assert len(llm.requests) == 2


# ---------------------------------------------------------------- footage

def test_footage_revises_weak_genre():
    intent = lex.extract("make it cool")
    clips = [make_clip(f"c{i}", 6, activities=("deadlift", "gym")) for i in range(4)]
    revised = IntentEngine().reconcile_with_footage(intent, clips)
    assert revised.genre.value == Genre.FITNESS
    assert any(e.source == "footage" for e in revised.genre.evidence)
    assert "genre" not in {m.field for m in revised.missing}
    assert revised.revisions


def test_footage_never_overrides_confident_genre():
    intent = lex.extract("wedding wedding bride groom vows ceremony")
    clips = [make_clip(f"c{i}", 6, activities=("deadlift",)) for i in range(4)]
    assert IntentEngine().reconcile_with_footage(intent, clips).genre.value == Genre.WEDDING


def test_footage_without_vision_does_not_guess_genre():
    intent = lex.extract("make it cool")
    clips = [make_clip(f"c{i}", 6, activities=("deadlift",), vision=False, roles=((StoryRole.PROGRESS, .3),)) for i in range(4)]
    assert IntentEngine().reconcile_with_footage(intent, clips).genre.is_default
