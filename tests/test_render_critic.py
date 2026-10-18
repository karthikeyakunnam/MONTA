"""Encoded-media checks, using a small deterministic video fixture."""

import subprocess

import pytest
from PIL import Image, ImageDraw

from shared.contracts.base import revalidate
from services.render_farm.render_critic import inspect_render
from services.render_farm.renderer import MediaRenderer
from services.render_farm.targeted_repair import attempt_black_ending_repair
from shared.contracts.timeline import TextOverlayIR, TimelineIR, VideoSegmentIR


@pytest.mark.asyncio
async def test_encoded_title_check_catches_missing_asset_and_accepts_rendered_asset(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=320x180:d=2:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(source),
    ], check=True)
    title = tmp_path / "title.png"
    image = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((24, 20, 296, 72), fill=(255, 255, 255, 255))
    image.save(title)
    segment = VideoSegmentIR(
        segment_id="one", clip_id="source", source_path=str(source), source_in_ms=0,
        source_out_ms=2000, timeline_start_ms=0, timeline_end_ms=2000, has_audio=False,
    )
    overlay = TextOverlayIR(
        overlay_id="title", role="title", text="EXACT TITLE", source_span="EXACT TITLE",
        asset_path=str(title), start_ms=0, end_ms=1800, position="top", color="white",
        font_family="sans_serif", timing_source="prompt",
    )
    plan = TimelineIR(project_id="encoded-critic", width=320, height=180, fps=30,
                      video_segments=(segment,), text_overlays=(overlay,), total_duration_ms=2000)
    missing = await inspect_render(str(source), plan, storage_root=str(tmp_path), actual_duration_s=2)
    assert next(c for c in missing["checks"] if c["name"] == "text:title")["status"] == "failed"
    assert missing["status"] == "failed"

    output = tmp_path / "rendered.mp4"
    result = await MediaRenderer().render(plan, output_path=output)
    assert result.success and result.validation_passed
    present = await inspect_render(str(output), plan, storage_root=str(tmp_path), actual_duration_s=2)
    assert next(c for c in present["checks"] if c["name"] == "text:title")["status"] == "passed"
    assert next(c for c in present["checks"] if c["name"] == "duration_and_frame")["status"] == "passed"


@pytest.mark.asyncio
async def test_clipped_encoded_audio_is_repaired_by_a_timeline_limiter(tmp_path):
    source = tmp_path / "loud_source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=320x180:d=2:r=30", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=2:sample_rate=48000", "-af", "volume=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True)
    segment = VideoSegmentIR(
        segment_id="one", clip_id="source", source_path=str(source), source_in_ms=0,
        source_out_ms=2000, timeline_start_ms=0, timeline_end_ms=2000,
    )
    plan = TimelineIR(project_id="audio-repair", width=320, height=180, fps=30,
                      video_segments=(segment,), total_duration_ms=2000)
    reports = []
    for name, current in (("clipped", plan), ("repaired", revalidate(plan, audio_peak_limit=0.6))):
        rendered = tmp_path / f"{name}.mp4"
        result = await MediaRenderer().render(current, output_path=rendered)
        assert result.success and result.validation_passed
        report = await inspect_render(str(rendered), current, storage_root=str(tmp_path), actual_duration_s=2)
        reports.append(next(c for c in report["checks"] if c["name"] == "audio_clipping"))
    assert reports[0]["status"] == "failed"
    assert reports[1]["status"] == "passed"


@pytest.mark.asyncio
async def test_exposure_jump_is_compared_with_source_scene_change(tmp_path):
    sources = []
    for color in ("white", "blue"):
        source = tmp_path / f"{color}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            f"color=c={color}:s=320x180:d=1:r=30", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(source),
        ], check=True)
        sources.append(source)
    segments = tuple(VideoSegmentIR(
        segment_id=f"shot-{i}", clip_id=f"clip-{i}", source_path=str(source),
        source_in_ms=0, source_out_ms=1000, timeline_start_ms=i * 1000,
        timeline_end_ms=(i + 1) * 1000, has_audio=False,
    ) for i, source in enumerate(sources))
    plan = TimelineIR(project_id="source-cut", width=320, height=180, fps=30,
                      video_segments=segments, total_duration_ms=2000)
    output = tmp_path / "joined.mp4"
    result = await MediaRenderer().render(plan, output_path=output)
    assert result.success and result.validation_passed
    report = await inspect_render(str(output), plan, storage_root=str(tmp_path), actual_duration_s=2)
    cut = next(c for c in report["checks"] if c["name"] == "exposure_cuts")
    assert cut["status"] == "warning"
    assert cut["evidence"]["jumps"][0]["classification"] == "source_driven_scene_change"


@pytest.mark.asyncio
async def test_unrequested_black_ending_is_found_and_shifted_source_window_clears_it(tmp_path):
    source = tmp_path / "fading_source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=320x180:d=3:r=30", "-vf", "fade=t=out:st=2.7:d=0.3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True)
    ending = VideoSegmentIR(
        segment_id="ending", clip_id="source", source_path=str(source), source_in_ms=1000,
        source_out_ms=3000, timeline_start_ms=0, timeline_end_ms=2000, has_audio=False,
    )
    plan = TimelineIR(project_id="fade-check", width=320, height=180, fps=30,
                      video_segments=(ending,), total_duration_ms=2000)
    fixed_plan = revalidate(plan, video_segments=(revalidate(
        ending, source_in_ms=250, source_out_ms=2250),))
    reports = []
    for name, current in (("faded", plan), ("shifted", fixed_plan)):
        path = tmp_path / f"{name}.mp4"
        result = await MediaRenderer().render(current, output_path=path)
        assert result.success and result.validation_passed
        report = await inspect_render(str(path), current, storage_root=str(tmp_path), actual_duration_s=2)
        reports.append(next(c for c in report["checks"] if c["name"] == "black_frames"))
    assert reports[0]["status"] == "failed"
    assert reports[0]["evidence"]["ending_blackout"]
    assert reports[1]["status"] == "passed"


@pytest.mark.asyncio
async def test_orchestration_reencodes_confirmed_ending_and_preserves_previous_file(tmp_path):
    from orchestration.pipeline import MontaPipeline

    source = tmp_path / "fading_source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=320x180:d=3:r=30", "-vf", "fade=t=out:st=2.7:d=0.3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ], check=True)
    shot = VideoSegmentIR(
        segment_id="ending", clip_id="source", source_path=str(source),
        source_in_ms=1000, source_out_ms=3000,
        timeline_start_ms=0, timeline_end_ms=2000, has_audio=False,
    )
    timeline = TimelineIR(project_id="bounded-repair", width=320, height=180, fps=30,
                          video_segments=(shot,), total_duration_ms=2000)

    async def render(candidate, path):
        # The same MontaPipeline.render_story method used by the Celery worker.
        return await MontaPipeline.render_story(object(), None, None, str(path), timeline=candidate)

    async def inspect(candidate, path, result):
        return await inspect_render(str(path), candidate, storage_root=str(tmp_path),
                                    actual_duration_s=result.duration_s)

    previous = tmp_path / "previous_successful.mp4"
    initial_result = await render(timeline, previous)
    initial_report = await inspect(timeline, previous, initial_result)
    assert initial_report["status"] == "failed"
    prior_bytes = previous.read_bytes()
    outcome = await attempt_black_ending_repair(
        timeline, initial_report, previous, initial_result, tmp_path / "repaired.mp4",
        {"source": 3000}, render, inspect,
    )
    assert outcome.accepted
    assert outcome.output_path != previous and outcome.output_path.is_file()
    assert previous.read_bytes() == prior_bytes
    assert outcome.timeline.video_segments[0].source_in_ms == 250
    assert outcome.timeline.video_segments[0].source_out_ms == 2250
    assert next(c for c in outcome.report["checks"] if c["name"] == "black_frames")["status"] == "passed"
    assert outcome.report["repair_attempts"][0]["cause"] == "ending_blackout"


@pytest.mark.asyncio
async def test_unrepairable_or_user_trimmed_ending_never_promotes_candidate(tmp_path):
    source = tmp_path / "previous_successful.mp4"
    source.write_bytes(b"previous output retained")
    shot = VideoSegmentIR(segment_id="ending", clip_id="source", source_path=str(source),
                          source_in_ms=0, source_out_ms=2000,
                          timeline_start_ms=0, timeline_end_ms=2000, has_audio=False)
    timeline = TimelineIR(project_id="unrepairable", width=320, height=180, fps=30,
                          video_segments=(shot,), total_duration_ms=2000)
    report = {"status": "failed", "checks": [{"name": "black_frames", "status": "failed",
              "evidence": {"ending_blackout": [{"at_s": 1.9, "duration_s": 0.1}]}}]}
    from services.render_farm.output_validator import RenderResult
    result = RenderResult(True, str(source), 2, 320, 180, 30, "h264", "", len(source.read_bytes()),
                          0, "libx264", True)

    async def forbidden(*_args):
        pytest.fail("an unrepairable or manually trimmed plan must not re-render")

    for manual in (False, True):
        checked = await attempt_black_ending_repair(
            timeline, {"status": report["status"], "checks": report["checks"]}, source,
            result, tmp_path / "candidate.mp4", {"source": 2000}, forbidden, forbidden,
            manual_override=manual,
        )
        assert not checked.accepted and checked.output_path == source
        assert source.read_bytes() == b"previous output retained"
        assert checked.report["repair_attempts"][0]["status"] == "unavailable"
