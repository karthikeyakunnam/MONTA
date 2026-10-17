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


@pytest.mark.parametrize(
    "payload",
    [
        {"style_preset": "film-noir"},
        {"vignette": 1.1},
        {"transition_duration_ms": 2501},
        {"scale_mode": "smart-face-track"},
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
