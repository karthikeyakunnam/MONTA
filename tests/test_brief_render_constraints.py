"""Explicit brief requirements survive style inference and reach the real render inputs."""

import pytest

from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from tests.test_prompt_engine import EVENT_ACCEPTANCE_PROMPT
from workers.tasks.pipeline_tasks import _brief_render_constraints, _materialize_prompt_visuals, _style_override


def _brief():
    return LexicalIntentExtractor().extract(EVENT_ACCEPTANCE_PROMPT).creative_brief


def test_brief_disables_inferred_music_and_captions_and_neutralizes_grade():
    constraints = _brief_render_constraints(_brief(), {})
    assert constraints["add_music"] is False
    assert constraints["captions_off"] is True
    assert {**constraints["style"], **_style_override({})} == {
        "color_grade": "natural", "brightness": 0.0, "contrast": 1.0,
        "saturation": 1.0, "gamma": 1.0, "shadow_coolness": 0.0,
        "highlight_warmth": 0.0, "vignette": 0.0, "transitions": "cut",
    }


def test_unset_music_option_follows_prompt_instead_of_enabling_a_default_soundtrack():
    from app.schemas.project import EditOptions

    assert EditOptions().add_music is None
    assert _brief_render_constraints(_brief(), {"add_music": None})["add_music"] is False
    positive = LexicalIntentExtractor().extract("Make a recap and add background music.").creative_brief
    assert _brief_render_constraints(positive, {"add_music": None})["add_music"] is True
    neutral = LexicalIntentExtractor().extract("Make a short event recap.").creative_brief
    assert _brief_render_constraints(neutral, {"add_music": None})["add_music"] is False


@pytest.mark.parametrize("options", [
    {"add_music": True}, {"caption_style": "Minimal"},
    {"manual_timeline": {"captions": [{"text": "Unrequested"}]}},
    {"color_grade": "dark"}, {"transition": "fade"}, {"title_position": "center"},
])
def test_conflicting_explicit_settings_are_rejected(options):
    with pytest.raises(ValueError, match="Brief (forbids|requires)"):
        _brief_render_constraints(_brief(), options)


def test_prompt_title_uses_exact_position_and_timing(tmp_path):
    overlays, grain, unavailable = _materialize_prompt_visuals(
        _brief(), 25_000, tmp_path, "acceptance", 1080, 1920,
    )
    assert grain == 0 and unavailable == []
    assert [(item.role, item.text, item.position, item.start_ms, item.end_ms)
            for item in overlays] == [
        ("title", "Oath-Taking Ceremony", "lower_third", 0, 3000),
    ]
