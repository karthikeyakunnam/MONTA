"""Manual decisions must survive validation and reach the real FFmpeg compiler."""

import pytest
import subprocess

from PIL import Image
from pydantic import ValidationError

from services.edit_executor.caption_engine import CaptionEngine
from services.edit_executor.compiler import FFmpegCompiler
from services.timeline_generator.manual import ManualTimeline, apply_manual_timeline, apply_manual_title_overrides
from shared.contracts.timeline import AudioSegmentIR, TimelineIR, VideoSegmentIR


@pytest.mark.asyncio
async def test_manual_title_text_timing_and_position_reach_encoded_mp4(tmp_path):
    from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
    from services.render_farm.renderer import MediaRenderer
    from services.render_farm.render_critic import inspect_render
    from workers.tasks.pipeline_tasks import _materialize_prompt_visuals

    source = tmp_path / "source.mp4"
    output = tmp_path / "edited_title.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=360x640:d=2:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(source)], check=True)
    original = LexicalIntentExtractor().extract("Show the exact title 'Original'").creative_brief
    manual = ManualTimeline.model_validate({"shots": [{"clip_id": "a", "source_in_ms": 0,
        "source_out_ms": 2000}], "titles": [{"overlay_index": 0, "text": "Changed",
        "start_s": 0.2, "end_s": 1.5, "position": "center"}]})
    edited_brief = apply_manual_title_overrides(original, manual.titles, 2.0)
    assert edited_brief.text_overlays[0].text == "Changed"
    assert edited_brief.directives[0].origin == "explicit_setting"
    overlays, _, _ = _materialize_prompt_visuals(
        edited_brief, 2000, tmp_path, "manual", 360, 640, manual_positions=True)
    assert overlays[0].text == "Changed" and overlays[0].position == "center"
    assert (overlays[0].start_ms, overlays[0].end_ms) == (200, 1500)
    timeline = TimelineIR(project_id="manual-title", width=360, height=640, fps=24,
        video_segments=(VideoSegmentIR(segment_id="one", clip_id="a", source_path=str(source),
            source_in_ms=0, source_out_ms=2000, timeline_start_ms=0,
            timeline_end_ms=2000, has_audio=False),),
        text_overlays=overlays, total_duration_ms=2000)
    result = await MediaRenderer().render(timeline, output_path=output)
    assert result.success and result.validation_passed
    report = await inspect_render(str(output), timeline, storage_root=str(tmp_path),
                                  actual_duration_s=result.duration_s)
    assert next(item for item in report["checks"] if item["name"].startswith("text:"))["status"] == "passed"


def test_manual_title_cannot_target_missing_overlay_or_exceed_render_duration():
    from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
    original = LexicalIntentExtractor().extract("Show title 'Original'").creative_brief
    title = ManualTimeline.model_validate({"shots": [{"clip_id": "a", "source_in_ms": 0,
        "source_out_ms": 1000}], "titles": [{"overlay_index": 0, "text": "Changed",
        "start_s": 0, "end_s": 2, "position": "lower_third"}]}).titles
    with pytest.raises(ValueError, match="exceeds"):
        apply_manual_title_overrides(original, title, 1.0)
    with pytest.raises(ValueError, match="unavailable"):
        apply_manual_title_overrides(original, (title[0].model_copy(update={"overlay_index": 5}),), 3.0)


def test_manual_title_override_survives_api_edit_options_serialization():
    from app.schemas.project import EditOptions

    options = EditOptions.model_validate({"manual_timeline": {
        "shots": [{"clip_id": "a", "source_in_ms": 0, "source_out_ms": 2000}],
        "titles": [{"overlay_index": 0, "text": "Human edited title",
                    "start_s": 0.25, "end_s": 1.75, "position": "lower_third"}],
    }})
    restored = EditOptions.model_validate_json(options.model_dump_json())
    title = restored.manual_timeline.titles[0]
    assert (title.text, title.start_s, title.end_s, title.position) == (
        "Human edited title", 0.25, 1.75, "lower_third")


def test_reorder_trim_split_remove_and_compile(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    music = tmp_path / "music.wav"
    a.write_bytes(b"source a")
    b.write_bytes(b"source b")
    music.write_bytes(b"music")
    base = TimelineIR(
        project_id="project", video_segments=(
            VideoSegmentIR(segment_id="a", clip_id="a", source_path=str(a), source_in_ms=0,
                           source_out_ms=4000, timeline_start_ms=0, timeline_end_ms=4000, has_audio=False),
            VideoSegmentIR(segment_id="b", clip_id="b", source_path=str(b), source_in_ms=0,
                           source_out_ms=4000, timeline_start_ms=4000, timeline_end_ms=8000, has_audio=False),
        ), audio_segments=(AudioSegmentIR(track_id="music", source_path=str(music),
                                          timeline_end_ms=8000, ducking=True),), total_duration_ms=8000,
    )
    # B moves first; A is trimmed and split into adjacent, non-overlapping shots.
    manual = ManualTimeline.model_validate({"shots": [
        {"clip_id": "b", "source_in_ms": 500, "source_out_ms": 2500},
        {"clip_id": "a", "source_in_ms": 1000, "source_out_ms": 2000,
         "transition_in": "crossfade", "transition_in_ms": 200, "volume": 0.5,
         "fade_in_ms": 100, "color": {"brightness": 0.1, "saturation": 0.8}},
        {"clip_id": "a", "source_in_ms": 2000, "source_out_ms": 3000},
    ], "music_volume": 0.3, "music_ducking": False})
    edited = apply_manual_timeline(base, manual, {"a": 4000, "b": 4000},
                                   {"a": (str(a), False, None), "b": (str(b), False, None)})
    assert [shot.clip_id for shot in edited.video_segments] == ["b", "a", "a"]
    assert [(shot.timeline_start_ms, shot.timeline_end_ms) for shot in edited.video_segments] == [
        (0, 2000), (2000, 3000), (3000, 4000),
    ]
    assert edited.render_duration_s == 3.8
    graph = FFmpegCompiler().compile(edited, tmp_path / "output.mp4", force_cpu=True).filter_graph
    assert graph.index("[0:v]trim=start=0.5000:end=2.5000") < graph.index("[1:v]trim=start=1.0000:end=2.0000")
    assert "xfade=transition=fade:duration=0.200" in graph
    assert "volume=0.500" in graph
    assert "volume=0.300" in graph
    assert "eq=brightness=0.100:saturation=0.800" in graph
    assert "afade=t=in:ss=0:d=0.100" in graph
    assert "sidechaincompress" not in graph


def test_manual_timeline_rejects_missing_and_out_of_range_sources(tmp_path):
    source = tmp_path / "a.mp4"
    source.write_bytes(b"source")
    base = TimelineIR(project_id="project", video_segments=(
        VideoSegmentIR(segment_id="a", clip_id="a", source_path=str(source), source_in_ms=0,
                       source_out_ms=1000, timeline_start_ms=0, timeline_end_ms=1000, has_audio=False),
    ), total_duration_ms=1000)
    with pytest.raises(ValueError, match="unavailable"):
        apply_manual_timeline(base, ManualTimeline.model_validate({"shots": [
            {"clip_id": "other", "source_in_ms": 0, "source_out_ms": 500}]}),
            {"a": 1000}, {"a": (str(source), False, None)})
    with pytest.raises(ValueError, match="exceeds source duration"):
        apply_manual_timeline(base, ManualTimeline.model_validate({"shots": [
            {"clip_id": "a", "source_in_ms": 0, "source_out_ms": 1500}]}),
            {"a": 1000}, {"a": (str(source), False, None)})


def test_manual_caption_text_and_rendered_timing_are_validated(tmp_path):
    source = tmp_path / "a.mp4"
    source.write_bytes(b"source")
    base = TimelineIR(project_id="project", video_segments=(
        VideoSegmentIR(segment_id="a", clip_id="a", source_path=str(source), source_in_ms=0,
                       source_out_ms=2000, timeline_start_ms=0, timeline_end_ms=2000, has_audio=False),
    ), total_duration_ms=2000)
    with pytest.raises(ValidationError, match="caption text must not be blank"):
        ManualTimeline.model_validate({"shots": [{"clip_id": "a", "source_in_ms": 0,
            "source_out_ms": 2000}], "captions": [{"start": 0, "end": 1, "text": "   "}]})
    manual = ManualTimeline.model_validate({"shots": [{"clip_id": "a", "source_in_ms": 0,
        "source_out_ms": 2000}], "captions": [{"start": 0.5, "end": 2.1, "text": "Edited words"}]})
    with pytest.raises(ValueError, match="caption timing exceeds"):
        apply_manual_timeline(base, manual, {"a": 2000}, {"a": (str(source), False, None)})


@pytest.mark.asyncio
async def test_edited_caption_is_burned_into_real_fixture_mp4(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "captioned.mp4"
    subprocess.check_call(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                           "-i", "color=c=black:size=360x640:rate=24:duration=2", "-c:v", "libx264",
                           "-pix_fmt", "yuv420p", str(source)])
    caption = ManualTimeline.model_validate({"shots": [{"clip_id": "a", "source_in_ms": 0,
        "source_out_ms": 2000}], "captions": [{"start": 0.5, "end": 1.5, "text": "Edited words"}]}).captions[0]
    await CaptionEngine(temp_dir=str(tmp_path)).burn_captions(
        str(source), [caption.model_dump()], str(output), style="Minimal", width=360, height=640)
    frames = []
    for at in (0.1, 1.0):
        frame = tmp_path / f"frame_{at}.png"
        subprocess.check_call(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at),
                               "-i", str(output), "-frames:v", "1", str(frame)])
        with Image.open(frame) as image:
            frames.append(max(max(pixel) for pixel in image.convert("RGB").crop((20, 450, 340, 630)).getdata()))
    assert frames[0] < 10
    assert frames[1] > 100
