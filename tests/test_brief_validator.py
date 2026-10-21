"""Brief acceptance reports distinguish plan facts from encoded and semantic evidence."""

from shared.contracts.base import revalidate
from shared.contracts.timeline import ColorAdjustment, TextOverlayIR, TimelineIR, VideoSegmentIR
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from services.render_farm.brief_validator import validate_brief_output
from tests.test_prompt_engine import EVENT_ACCEPTANCE_PROMPT


def _timeline():
    beats = LexicalIntentExtractor().extract(EVENT_ACCEPTANCE_PROMPT).creative_brief.scene_beats
    rows = tuple(VideoSegmentIR(
        segment_id=f"s{index}", clip_id=f"c{index}", source_path=f"/source/{index}.mov",
        source_in_ms=0, source_out_ms=6250,
        timeline_start_ms=index * 6250, timeline_end_ms=(index + 1) * 6250,
        color=ColorAdjustment(), narrative_role=f"Requested scene beat {index + 1}: {beat.description}",
    ) for index, beat in enumerate(beats))
    title = TextOverlayIR(
        overlay_id="title", role="title", text="Oath-Taking Ceremony",
        source_span="'Oath-Taking Ceremony'", asset_path="/source/title.png",
        start_ms=0, end_ms=3000, position="lower_third", color="white",
        font_family="serif", timing_source="prompt",
    )
    return TimelineIR(project_id="project", video_segments=rows,
                      text_overlays=(title,), total_duration_ms=25_000)


def _encoded(duration=25.0):
    return {"checks": [
        {"name": "duration_and_frame", "status": "passed",
         "evidence": {"encoded_duration_s": duration}},
        {"name": "black_frames", "status": "passed", "evidence": {}},
        {"name": "audio_peak", "status": "passed", "evidence": {"max_volume_db": -3.0}},
        {"name": "audio_clipping", "status": "passed", "evidence": {}},
        {"name": "text:title", "status": "passed", "evidence": {"matching_pixel_fraction": 0.98}},
    ]}


def test_explicit_event_plan_and_encoded_asset_are_checked_without_fabricating_semantics():
    brief = LexicalIntentExtractor().extract(EVENT_ACCEPTANCE_PROMPT).creative_brief
    report = validate_brief_output(brief, _timeline(), _encoded(), requested_duration_s=25)
    checks = {item["name"]: item["status"] for item in report["checks"]}
    assert report["status"] == "unavailable"
    assert checks["beat_plan_order"] == checks["planned_overlays"] == "passed"
    assert checks["no_generated_captions"] == checks["no_added_music"] == "passed"
    assert checks["source_faithful_grade"] == checks["calm_cut_plan"] == "passed"
    assert checks["calm_pacing"] == "unavailable"
    assert checks["encoded_text_asset:title"] == "passed"
    assert checks["encoded_scene_meaning"] == checks["independent_text_ocr"] == "unavailable"


def test_requested_spoken_dialogue_and_phrase_cuts_are_never_inferred_from_audio_stream():
    brief = LexicalIntentExtractor().extract(
        "Keep natural source speech audible and avoid cutting spoken phrases.").creative_brief
    required = {(item.category, item.value) for item in brief.directives if item.priority == "required"}
    assert {("audio", "source_audio"), ("audio", "speech_present"),
            ("audio", "speech_boundaries")} <= required
    report = validate_brief_output(brief, _timeline(), _encoded(), requested_duration_s=25)
    checks = {item["name"]: item["status"] for item in report["checks"]}
    assert checks["source_audio_plan"] == checks["encoded_audio_stream"] == "passed"
    assert checks["spoken_dialogue_present"] == checks["spoken_phrase_cut_alignment"] == "unavailable"


def test_wrong_encoded_duration_or_story_order_fails_without_promotion():
    brief = LexicalIntentExtractor().extract(EVENT_ACCEPTANCE_PROMPT).creative_brief
    wrong = _timeline()
    swapped = tuple(revalidate(item, narrative_role=wrong.video_segments[3-index].narrative_role)
                    for index, item in enumerate(wrong.video_segments))
    wrong = revalidate(wrong, video_segments=swapped)
    report = validate_brief_output(brief, wrong, _encoded(23.5), requested_duration_s=25)
    checks = {item["name"]: item["status"] for item in report["checks"]}
    assert report["status"] == "failed"
    assert checks["requested_duration"] == "failed"
    assert checks["beat_plan_order"] == "failed"


def test_multiple_adjacent_shots_per_beat_keep_order_but_recurrence_fails():
    brief = LexicalIntentExtractor().extract(EVENT_ACCEPTANCE_PROMPT).creative_brief
    timeline = _timeline()
    first = timeline.video_segments[0]
    opening = revalidate(first, source_out_ms=3000, timeline_end_ms=3000)
    opening_context = revalidate(first, segment_id="s0-context",
                                 source_in_ms=3000, source_out_ms=6250,
                                 timeline_start_ms=3000, timeline_end_ms=6250)
    multi = revalidate(timeline, video_segments=(opening, opening_context,
                                                 *timeline.video_segments[1:]))
    report = validate_brief_output(brief, multi, _encoded(), requested_duration_s=25)
    assert next(item for item in report["checks"] if item["name"] == "beat_plan_order")["status"] == "passed"

    repeated = revalidate(multi, video_segments=(*multi.video_segments[:-1],
                          revalidate(multi.video_segments[-1], narrative_role=first.narrative_role)))
    report = validate_brief_output(brief, repeated, _encoded(), requested_duration_s=25)
    assert next(item for item in report["checks"] if item["name"] == "beat_plan_order")["status"] == "failed"
