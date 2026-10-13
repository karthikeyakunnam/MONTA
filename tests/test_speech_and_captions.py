"""Tests for Phase 3: Speech transcription, CaptionGenerator, and CaptionEngine."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
import pytest

from services.edit_executor.caption_engine import CaptionEngine
from speech.caption_generator import CaptionGenerator
from speech.transcriber import WhisperTranscriber
from shared.contracts.vocab import CaptionStyle


SAMPLE_SEGMENTS = [
    {"id": 0, "start": 0.5, "end": 2.0, "text": "Welcome to MONTA"},
    {"id": 1, "start": 2.2, "end": 4.5, "text": "Automated AI Video Editing"},
]


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
