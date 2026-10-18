"""Encoded-media checks, using a small deterministic video fixture."""

import subprocess

import pytest
from PIL import Image, ImageDraw

from services.render_farm.render_critic import inspect_render
from services.render_farm.renderer import MediaRenderer
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
