"""Layer 3 — Prompt Intelligence: messy language, confidence, ambiguity, conflicts, LLM ensemble, footage reconciliation."""

import json

import pytest

from services.prompt_engine import IntentEngine
from services.prompt_engine.engine import is_incremental_revision
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from services.prompt_engine.normalizer import normalize, osa_distance
from shared.contracts.vocab import CaptionStyle, ColorGrade, Emotion, Genre, MusicStyle, Pace, Platform, StoryRole
from shared.providers.errors import ProviderUnavailableError
from tests.conftest import ScriptedProvider, make_clip

lex = LexicalIntentExtractor()

EVENT_ACCEPTANCE_PROMPT = (
    "Open on venue signage, develop through greetings, show stage interaction, and end with podium speaker. "
    "Make a 25-second cinematic event recap with calm cuts and natural color. "
    "Show the exact title 'Oath-Taking Ceremony' in the lower third for the first 3 seconds. "
    "Keep natural source audio; do not add music or captions."
)


def test_event_brief_retains_explicit_render_constraints():
    intent = lex.extract(EVENT_ACCEPTANCE_PROMPT)
    brief = intent.creative_brief
    assert intent.target_duration_s.value == 25
    assert [beat.description for beat in brief.scene_beats] == [
        "venue signage", "greetings", "stage interaction", "podium speaker",
    ]
    assert [(item.role, item.text, item.position, item.end_s) for item in brief.text_overlays] == [
        ("title", "Oath-Taking Ceremony", "lower_third", 3.0),
    ]
    assert {(item.category, item.value) for item in brief.directives} >= {
        ("color", "natural"), ("transition", "cut"), ("audio", "source_audio"),
        ("audio", "music_off"), ("text", "captions_off"),
    }

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


def test_quoted_title_subtitle_credits_and_caption_are_preserved_as_requirements():
    prompt = (
        'Main title in bold magenta capital serif text "PERSPECTIVES". '
        "Centered neon glow title 'PERSPECTIVES'. "
        'Clean white tracking sans-serif subtitle underneath "A Cinematic Experiment". '
        'Credits: "CINEMATOGRAPHY BY ASHISH" and "SHOT ON SAMSUNG". '
        'White caption overlay "PURE AS BIRTH".'
    )

    brief = lex.extract(prompt).creative_brief
    requirements = brief.text_overlays

    assert [(item.role, item.text) for item in requirements] == [
        ("title", "PERSPECTIVES"),
        ("subtitle", "A Cinematic Experiment"),
        ("credit", "CINEMATOGRAPHY BY ASHISH"),
        ("credit", "SHOT ON SAMSUNG"),
        ("caption", "PURE AS BIRTH"),
    ]
    assert requirements[0].position == "center"
    assert requirements[0].color == "magenta"
    assert requirements[0].font_family == "serif"
    assert requirements[0].glow is True
    assert requirements[1].position == "below_title"
    assert requirements[1].color == "white"
    assert requirements[1].font_family == "sans_serif"
    assert requirements[4].color == "white"
    assert [(item.priority, item.value) for item in brief.directives if item.category == "text"] == [
        ("required", f"{item.role}:{item.text}") for item in requirements
    ]
    assert all(all(span in prompt for span in item.source_span.split(" | ")) for item in brief.directives)


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


def test_observed_misspelled_duration_and_emotional_arc_are_extracted():
    prompt = ("make a sad looking over and make it loook Like Happy ending but it is been with going sad part "
              "make a vedeo 30 secounds")
    a = lex.extract(prompt)
    assert a.target_duration_s and a.target_duration_s.value == 30
    assert ("secounds", "seconds") in {(c.original, c.corrected) for c in a.corrections}
    assert a.arc_for("start") and a.arc_for("start").emotion == Emotion.EMOTIONAL
    assert a.arc_for("end") and a.arc_for("end").emotion == Emotion.UPLIFTING


def test_explicit_sad_hopeful_happy_arc_is_ordered():
    a = lex.extract("sad start, hopeful middle, happy ending")
    assert [beat.section for beat in a.pacing_arc] == ["start", "middle", "end"]
    assert [beat.emotion for beat in a.pacing_arc] == [Emotion.EMOTIONAL, Emotion.INSPIRING, Emotion.UPLIFTING]


def test_platform_longest_match_wins():
    assert lex.extract("youtube shorts edit").target_platform.value == Platform.YOUTUBE_SHORT
    assert lex.extract("tiktok edit").target_platform.value == Platform.TIKTOK


def test_incremental_revision_keeps_unchanged_constraints_and_merges_only_the_new_arc_beat():
    previous_prompt = "A 15 second travel montage with a calm mood, natural colors, no captions, and a slow start."
    previous = lex.extract(previous_prompt)
    delta = lex.extract("Make the ending happier with vibrant colors.")

    assert is_incremental_revision(previous_prompt, "Make the ending happier with vibrant colors.")
    merged = IntentEngine.merge_revision(previous, delta)
    assert merged.genre.value == Genre.TRAVEL
    assert merged.emotion.value == Emotion.CALM
    assert merged.pace.value == Pace.SLOW
    assert merged.target_duration_s.value == 15
    assert merged.color_grade.value == ColorGrade.VIBRANT
    assert merged.caption_style.value == CaptionStyle.NONE
    assert merged.arc_for("start").pace == Pace.SLOW
    assert merged.arc_for("end").emotion == Emotion.UPLIFTING
    assert merged.creative_brief.visual_treatments == previous.creative_brief.visual_treatments
    assert any("inherited unchanged requirements" in note for note in merged.revisions)


def test_incremental_revision_keeps_or_replaces_explicit_scene_order():
    previous = lex.extract("Open on venue signage, develop through arrivals, and end with podium speaker.")
    unchanged = lex.extract("Keep everything but make the ending more hopeful.")
    retained = IntentEngine.merge_revision(previous, unchanged)
    assert [beat.description for beat in retained.creative_brief.scene_beats] == [
        "venue signage", "arrivals", "podium speaker"]

    changed = lex.extract("Open on the stage, develop through greetings, and end with the speech.")
    replaced = IntentEngine.merge_revision(previous, changed)
    assert [beat.description for beat in replaced.creative_brief.scene_beats] == [
        "the stage", "greetings", "the speech"]
    assert len([d for d in replaced.creative_brief.directives
                if d.category == "narrative" and d.value.startswith("scene_beat_")]) == 3


def test_shadowy_happy_motivational_brief_keeps_visual_and_emotional_axes_separate():
    intent = lex.extract("A shadowy, happy, motivational reel")
    brief = intent.creative_brief
    assert "shadow_led_contrast" in brief.visual_treatments
    assert Emotion.MOTIVATIONAL in brief.emotional_tones
    assert Emotion.UPLIFTING in brief.emotional_tones
    assert brief.energy_trajectory == "rising"
    assert intent.arc_for("start").pace == Pace.SLOW
    assert intent.arc_for("middle").pace == Pace.MEDIUM
    assert intent.arc_for("end").pace == Pace.FAST


def test_physical_effect_requests_are_explicitly_classified_from_prompt_evidence():
    intent = lex.extract(
        "Add subtle film grain, floating dust particles, atmospheric haze, rim lighting, and slow camera movements."
    )

    assert set(intent.creative_brief.visual_treatments) == {
        "film_grain", "dust_particles", "atmospheric_haze", "rim_lighting", "camera_motion",
    }
    assert {"film grain", "dust particles", "atmospheric haze", "rim lighting", "slow camera movements"} <= set(
        intent.creative_brief.evidence
    )
    directives = {item.value: item for item in intent.creative_brief.directives}
    assert directives["film_grain"].priority == "optional"
    assert all(item.source_span in intent.raw_prompt for item in directives.values())


def test_targeted_happier_revision_preserves_shadowy_visual_treatment_and_motivation():
    previous = lex.extract(
        'A shadowy, happy, motivational reel with an uplifting resolution. '
        'Centered magenta serif title "PERSPECTIVES".'
    )
    revised = lex.extract("Make the ending happier")
    merged = IntentEngine.merge_revision(previous, revised)
    assert "shadow_led_contrast" in merged.creative_brief.visual_treatments
    assert Emotion.MOTIVATIONAL in merged.creative_brief.emotional_tones
    assert Emotion.UPLIFTING in merged.creative_brief.emotional_tones
    assert merged.creative_brief.energy_trajectory == "rising"
    assert merged.creative_brief.text_overlays == previous.creative_brief.text_overlays


def test_full_prompt_replacement_does_not_inherit_an_unrelated_prompt():
    previous_prompt = "A calm travel montage with soft music and natural colors."
    revised_prompt = "A fast wedding highlight with energetic music and bold reds."
    assert not is_incremental_revision(previous_prompt, revised_prompt)


# ---------------------------------------------------------------- ensemble

def _llm_json(**fields) -> str:
    return json.dumps(fields)


async def test_no_llm_is_marked_degraded():
    a = await IntentEngine().analyze(NIKE)
    assert a.extractors == ("lexical:lexicon.v2",)
    assert any("no text LLM" in d for d in a.degraded)


async def test_llm_grounded_agreement_raises_confidence():
    prompt = f"{NIKE} for Instagram reels"
    llm = ScriptedProvider([_llm_json(
        pace={"value": "aggressive", "confidence": 0.9, "reasoning": "'aggressive cuts'", "evidence_span": "aggressive cuts"},
        target_platform={"value": "instagram", "confidence": 0.6, "reasoning": "'Instagram reels'", "evidence_span": "Instagram"},
    )], vision=False)
    lexical = lex.extract(prompt)
    a = await IntentEngine(llm).analyze(prompt)
    assert a.pace.confidence > lexical.pace.confidence
    assert not a.target_platform.is_default
    assert a.target_platform.confidence > lexical.target_platform.confidence
    assert "target_platform" not in {m.field for m in a.missing}
    assert a.extractors[-1].startswith("llm:")


async def test_llm_color_guess_without_prompt_evidence_is_discarded():
    llm = ScriptedProvider([_llm_json(color_grade={"value": "neon", "confidence": 0.95, "reasoning": "guess"})], vision=False)
    a = await IntentEngine(llm).analyze(NIKE)
    assert a.color_grade.value == ColorGrade.DARK
    assert not any(x.field == "color_grade" for x in a.ambiguities)
    assert any("color_grade" in revision for revision in a.revisions)


async def test_llm_pace_without_a_prompt_span_cannot_override_slow_camera_direction():
    prompt = "A cinematic portrait with slow camera movements."
    llm = ScriptedProvider([_llm_json(pace={
        "value": "fast", "confidence": 0.99,
        "reasoning": "fast edit", "evidence_span": "fast edit",
    })], vision=False)

    intent = await IntentEngine(llm).analyze(prompt)

    assert intent.pace.value == Pace.SLOW
    assert all(e.span != "fast edit" for e in intent.pace.evidence)


async def test_explicit_calm_cuts_survive_conflicting_model_pace():
    prompt = "Make a 25-second event recap with calm cuts and natural color."
    llm = ScriptedProvider([_llm_json(pace={
        "value": "medium", "confidence": 0.95,
        "reasoning": "typical event recap", "evidence_span": "event recap",
    })], vision=False)

    intent = await IntentEngine(llm).analyze(prompt)

    assert intent.pace.value == Pace.SLOW
    assert "Explicit prompt pacing kept" in intent.pace.reasoning


def test_quoted_title_explicit_source_window_survives_lexical_fallback():
    prompt = ("Show the exact title 'Oath-Taking Ceremony' in the lower third "
              "from 0 to 3 seconds. Keep natural source audio.")
    intent = IntentEngine().lexical.extract(prompt)
    assert len(intent.creative_brief.text_overlays) == 1
    title = intent.creative_brief.text_overlays[0]
    assert (title.text, title.position, title.start_s, title.end_s) == (
        "Oath-Taking Ceremony", "lower_third", 0.0, 3.0)


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
