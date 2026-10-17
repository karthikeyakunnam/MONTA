"""Persisted studio controls must survive schema validation and reach rendering."""

import pytest
from pydantic import ValidationError

from backend.app.schemas.project import EditOptions
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from workers.tasks.pipeline_tasks import _materialize_prompt_visuals, _style_override


def test_studio_revision_options_are_validated_and_json_persistable():
    options = EditOptions.model_validate({
        "target_duration_s": 24,
        "aspect_ratio": "4:5",
        "style_preset": "trailer",
        "pace": "fast",
        "transition": "crossfade",
        "transition_duration_ms": 360,
        "scale_mode": "fill",
        "brightness": -0.08,
        "contrast": 1.35,
        "saturation": 0.82,
        "gamma": 0.95,
        "shadow_coolness": 0.3,
        "highlight_warmth": 0.4,
        "vignette": 0.25,
        "add_music": False,
        "caption_style": "None",
        "title_position": "lower_third",
        "include_prior_exports": False,
    })
    persisted = EditOptions.model_validate(options.model_dump(mode="json"))
    assert persisted == options
    override = _style_override(persisted.model_dump(mode="json"))
    assert override["style_preset"] == "trailer"
    assert override["transitions"] == "crossfade"
    assert override["transition_ms"] == 360
    assert override["scale_mode"] == "fill"
    assert override["highlight_warmth"] == pytest.approx(0.4)
    assert override["vignette"] == pytest.approx(0.25)
    assert persisted.title_position == "lower_third"


@pytest.mark.parametrize(
    "payload",
    [
        {"style_preset": "film-noir"},
        {"vignette": 1.1},
        {"transition_duration_ms": 2501},
        {"scale_mode": "smart-face-track"},
        {"title_position": "over_face"},
    ],
)
def test_unsupported_or_out_of_range_controls_are_rejected(payload):
    with pytest.raises(ValidationError):
        EditOptions.model_validate(payload)


def test_prompt_visuals_materialize_exact_text_and_report_unsupported_effects(tmp_path):
    brief = LexicalIntentExtractor().extract(
        'Centered serif title "PERSPECTIVES". White caption overlay "PURE AS BIRTH". '
        'Subtle film grain, floating dust particles, and atmospheric haze.'
    ).creative_brief

    overlays, grain_strength, unavailable = _materialize_prompt_visuals(
        brief, 20_000, tmp_path, "job_prompt_visuals",
    )

    assert [(item.role, item.text) for item in overlays] == [
        ("title", "PERSPECTIVES"), ("caption", "PURE AS BIRTH"),
    ]
    assert all(item.timing_source == "presentation_default" for item in overlays)
    assert all(tmp_path in __import__("pathlib").Path(item.asset_path).parents for item in overlays)
    assert all(__import__("pathlib").Path(item.asset_path).is_file() for item in overlays)
    assert grain_strength == 2
    assert {item["effect"] for item in unavailable} == {"dust_particles", "atmospheric_haze"}


def test_portrait_title_uses_safe_zone_and_explicit_center_restores_center(tmp_path):
    from PIL import Image

    prompt = ("Centered bold magenta serif title 'PERSPECTIVES', with a clean white "
              "tracking sans-serif subtitle underneath 'A Cinematic Experiment'.")
    brief = LexicalIntentExtractor().extract(prompt).creative_brief
    assert brief.text_overlays[1].color == "white"
    assert brief.text_overlays[1].font_family == "sans_serif"
    overlays, _, _ = _materialize_prompt_visuals(brief, 5000, tmp_path, "safe", 1080, 1920)
    assert [item.position for item in overlays] == ["lower_third", "bottom"]
    with Image.open(overlays[0].asset_path) as asset:
        title_box = asset.getchannel("A").getbbox()
    # The baseline 1–3s frames show the speaker's face within this center area.
    face_region = (240, 180, 840, 1100)
    assert title_box[1] > face_region[3]
    centered, _, _ = _materialize_prompt_visuals(
        brief, 5000, tmp_path, "center", 1080, 1920, title_position="center")
    assert [item.position for item in centered] == ["center", "below_title"]


def test_cached_subtitle_style_is_refreshed_from_its_local_prompt_clause():
    from shared.contracts.base import revalidate
    from workers.tasks.pipeline_tasks import _refresh_prompt_text_styles

    prompt = ("Centered magenta serif title 'PERSPECTIVES', with a clean white "
              "tracking sans-serif subtitle underneath 'A Cinematic Experiment'.")
    brief = LexicalIntentExtractor().extract(prompt).creative_brief
    stale = revalidate(brief, text_overlays=(brief.text_overlays[0], revalidate(
        brief.text_overlays[1], color="magenta", font_family="serif", start_s=0.5)))
    refreshed = _refresh_prompt_text_styles(stale, prompt)
    assert refreshed.text_overlays[1].color == "white"
    assert refreshed.text_overlays[1].font_family == "sans_serif"
    assert refreshed.text_overlays[1].start_s == 0.5


def test_unquoted_title_and_explicit_opening_timing_reach_overlay(tmp_path):
    from workers.tasks.pipeline_tasks import _refresh_prompt_text_styles

    prompt = ("Show a clean white lower-third title reading Oath-Taking Ceremony "
              "for the opening three seconds. Do not invent speech captions.")
    brief = LexicalIntentExtractor().extract(prompt).creative_brief
    assert [(item.role, item.text, item.end_s) for item in brief.text_overlays] == [
        ("title", "Oath-Taking Ceremony", 3.0),
    ]
    # Existing cached projects with this previously missed title gain the real overlay.
    from shared.contracts.base import revalidate
    refreshed = _refresh_prompt_text_styles(revalidate(brief, text_overlays=()), prompt)
    overlays, _, _ = _materialize_prompt_visuals(
        refreshed, 25_000, tmp_path, "event", title_position="lower_third")
    assert [(item.text, item.start_ms, item.end_ms, item.color) for item in overlays] == [
        ("Oath-Taking Ceremony", 0, 3000, "white"),
    ]
    assert __import__("pathlib").Path(overlays[0].asset_path).is_file()


def test_prior_exports_are_excluded_from_auto_story_but_manual_selection_can_use_them():
    from types import SimpleNamespace
    from workers.tasks.pipeline_tasks import _select_auto_sources

    original = SimpleNamespace(id="raw", original_filename="Ashish4.mp4")
    export = SimpleNamespace(id="export", original_filename="monta_edit_proj_72f7f005f001.mp4")
    assert _select_auto_sources([original, export], {}) == [original]
    assert _select_auto_sources([export], {}) == [export]
    assert _select_auto_sources([original, export], {"include_prior_exports": True}) == [original, export]
    assert _select_auto_sources([original, export], {"manual_timeline": {"shots": []}}) == [original, export]
