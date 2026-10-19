"""Tests for Phase 3: Speech transcription, CaptionGenerator, and CaptionEngine."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
import pytest

from services.edit_executor.caption_engine import CaptionEngine
from speech.caption_generator import CaptionGenerator
from speech.transcriber import LocalWhisperModelUnavailable, WhisperTranscriber
from shared.contracts.vocab import CaptionStyle
from shared.contracts.timeline import TextOverlayIR


SAMPLE_SEGMENTS = [
    {"id": 0, "start": 0.5, "end": 2.0, "text": "Welcome to MONTA"},
    {"id": 1, "start": 2.2, "end": 4.5, "text": "Automated AI Video Editing"},
]


def test_missing_local_whisper_weights_report_unavailable_without_download(tmp_path, monkeypatch):
    import whisper

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(whisper, "load_model", lambda *_args: pytest.fail("unexpected model download"))
    with pytest.raises(LocalWhisperModelUnavailable, match="sentence-aware cuts are unavailable"):
        WhisperTranscriber(model_size="base")._get_model()


def test_explicit_local_whisper_weights_are_loaded_by_path(tmp_path, monkeypatch):
    import whisper

    model_file = tmp_path / "custom.pt"
    model_file.write_bytes(b"local test fixture")
    seen = []
    monkeypatch.setattr(whisper, "load_model", lambda path: seen.append(path) or object())
    transcriber = WhisperTranscriber(model_size=str(model_file))
    assert transcriber._get_model() is transcriber._get_model()
    assert seen == [str(model_file)]


def test_caption_generator_to_srt():
    srt = CaptionGenerator.to_srt(SAMPLE_SEGMENTS)
    assert "1" in srt
    assert "00:00:00,500 --> 00:00:02,000" in srt
    assert "Welcome to MONTA" in srt
    assert "2" in srt
    assert "00:00:02,200 --> 00:00:04,500" in srt
    assert "Automated AI Video Editing" in srt


def test_caption_generator_to_ass_styles():
    for style in (CaptionStyle.NIKE, CaptionStyle.GYMSHARK, CaptionStyle.DAVID_GOGGINS, CaptionStyle.MINIMAL):
        ass = CaptionGenerator.to_ass(SAMPLE_SEGMENTS, style=style, play_res_x=1920, play_res_y=1080)
        assert "[Script Info]" in ass
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass
        assert "Dialogue: 0,0:00:00.50,0:00:02.00,Main" in ass

        # Nike and Gymshark uppercase check
        if style in (CaptionStyle.NIKE, CaptionStyle.GYMSHARK):
            assert "WELCOME TO MONTA" in ass


def test_caption_engine_creates_overlay_image(tmp_path):
    engine = CaptionEngine(temp_dir=str(tmp_path))
    out_img = tmp_path / "test_overlay.png"
    engine.create_overlay_image(
        text="STAY HARD",
        width=1280,
        height=720,
        style=CaptionStyle.DAVID_GOGGINS,
        output_path=str(out_img),
    )
    assert out_img.exists()
    assert out_img.stat().st_size > 1000


def test_long_source_caption_stays_inside_vertical_safe_area(tmp_path):
    from PIL import Image

    engine = CaptionEngine(temp_dir=str(tmp_path))
    overlay = engine.create_overlay_image(
        "And to begin with, I have this habit of appreciating the food that someone makes for me, irrespective",
        width=1080, height=1920, style=CaptionStyle.MINIMAL,
        output_path=str(tmp_path / "long_caption.png"),
    )
    with Image.open(overlay) as image:
        bounds = image.getchannel("A").getbbox()
    assert bounds is not None
    assert bounds[0] >= 50 and bounds[2] <= 1030
    assert bounds[1] >= 1056 and bounds[3] <= 1920


@pytest.mark.parametrize("caption", ["W" * 250, "many " * 1000])
def test_caption_rejects_text_that_cannot_fit_safe_area(tmp_path, caption):
    engine = CaptionEngine(temp_dir=str(tmp_path))
    with pytest.raises(ValueError, match="safe video area"):
        engine.create_overlay_image(caption, width=1080, height=1920)


@pytest.mark.asyncio
@pytest.mark.parametrize("gain", [0, -1, 1.01, float("nan"), float("inf")])
async def test_caption_audio_gain_rejects_unsafe_values(tmp_path, gain):
    engine = CaptionEngine(temp_dir=str(tmp_path))
    with pytest.raises(ValueError, match="audio_gain"):
        await engine.burn_captions("unused.mp4", [], str(tmp_path / "out.mp4"), audio_gain=gain)


def test_caption_avoids_timed_subtitle_without_changing_its_words(tmp_path):
    from PIL import Image, ImageDraw

    reserved_path = tmp_path / "subtitle.png"
    reserved = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    ImageDraw.Draw(reserved).rectangle((220, 1650, 860, 1710), fill=(255, 255, 255, 255))
    reserved.save(reserved_path)
    subtitle = TextOverlayIR(
        overlay_id="subtitle", role="subtitle", text="Exact subtitle",
        source_span="test", asset_path=str(reserved_path), start_ms=350, end_ms=4500,
        position="bottom", color="white", font_family="sans_serif", timing_source="prompt",
    )
    engine = CaptionEngine(temp_dir=str(tmp_path))
    caption = "Hi, welcome to day 21 of speaking and I get rid of embarrassment series."
    opening = engine.create_avoiding_overlay_image(
        caption, 1080, 1920, 0, 3.6, (subtitle,), output_path=str(tmp_path / "opening.png"),
    )
    partial = engine.create_avoiding_overlay_image(
        caption, 1080, 1920, 4.226, 8.357, (subtitle,),
        output_path=str(tmp_path / "partial.png"),
    )
    later = engine.create_avoiding_overlay_image(
        caption, 1080, 1920, 4.6, 8.0, (subtitle,), output_path=str(tmp_path / "later.png"),
    )
    with Image.open(opening) as image:
        opening_box = image.getchannel("A").getbbox()
    with Image.open(partial) as image:
        partial_box = image.getchannel("A").getbbox()
    with Image.open(later) as image:
        later_box = image.getchannel("A").getbbox()
    assert opening_box is not None and opening_box[3] < 864
    assert partial_box is not None and partial_box[3] < 864
    assert later_box is not None and later_box[1] > 1056

    track = engine._prepare_caption_overlays([
        {"start": 0.0, "end": 3.6, "text": caption},
        {"start": 4.6, "end": 8.0, "text": "A later cue keeps the same vertical region."},
        {"start": 8.1, "end": 12.0, "text":
         "And to begin with, I have this habit of appreciating the food that someone makes for me, irrespective"},
    ], 1080, 1920, CaptionStyle.MINIMAL, (subtitle,))
    with Image.open(track[0][0]) as image:
        first_box = image.getchannel("A").getbbox()
    with Image.open(track[1][0]) as image:
        second_box = image.getchannel("A").getbbox()
    with Image.open(track[2][0]) as image:
        long_box = image.getchannel("A").getbbox()
    assert first_box is not None and first_box[3] < 384
    assert second_box is not None and second_box[3] < 384
    assert long_box is not None and long_box[3] < 384
    assert [(start, end) for _, start, end in track] == [
        (0.0, 3.6), (4.6, 8.0), (8.1, 12.0),
    ]


def test_caption_blocks_when_title_occupies_both_safe_areas(tmp_path):
    from PIL import Image, ImageDraw

    reserved_path = tmp_path / "blocked.png"
    reserved = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(reserved)
    draw.rectangle((0, 0, 1080, 600), fill=(255, 255, 255, 255))
    draw.rectangle((0, 1300, 1080, 1920), fill=(255, 255, 255, 255))
    reserved.save(reserved_path)
    overlay = TextOverlayIR(
        overlay_id="blocked", role="title", text="Title", source_span="test",
        asset_path=str(reserved_path), start_ms=0, end_ms=3000,
        position="center", color="white", font_family="serif", timing_source="prompt",
    )
    engine = CaptionEngine(temp_dir=str(tmp_path))
    with pytest.raises(ValueError, match="available safe areas"):
        engine.create_avoiding_overlay_image(
            "A short caption", 1080, 1920, 0, 2.0, (overlay,),
            output_path=str(tmp_path / "caption.png"),
        )


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_caption_engine_burns_captions_into_video(tmp_path):
    # 1. Create a 3-second test video
    input_video = tmp_path / "raw_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
        "-c:v", "libx264",
        str(input_video),
    ], check=True)

    # 2. Burn captions
    engine = CaptionEngine(temp_dir=str(tmp_path))
    output_video = tmp_path / "captioned_video.mp4"

    test_captions = [
        {"id": 0, "start": 0.5, "end": 2.5, "text": "JUST DO IT"},
    ]

    result_path = await engine.burn_captions(
        video_path=str(input_video),
        captions=test_captions,
        output_path=str(output_video),
        style=CaptionStyle.NIKE,
        width=640,
        height=360,
    )

    assert os.path.exists(result_path)
    assert Path(result_path).stat().st_size > 1000

    # Verify duration
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", result_path],
        capture_output=True,
        text=True,
    )
    dur = float(res.stdout.strip())
    assert 2.8 <= dur <= 3.2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_whisper_audio_extraction(tmp_path):
    # Create video with audio tone
    input_video = tmp_path / "tone_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-c:a", "aac",
        str(input_video),
    ], check=True)

    transcriber = WhisperTranscriber(model_size="tiny")
    wav_path = await transcriber.extract_audio_from_video(str(input_video))

    assert os.path.exists(wav_path)
    assert os.path.getsize(wav_path) > 1000

    # Probe wav
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate", "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
        capture_output=True,
        text=True,
    )
    lines = res.stdout.strip().splitlines()
    assert "16000" in lines  # 16kHz
    assert "1" in lines      # Mono
