"""Tests for Layer 10: Audio Studio, background music mixing, ducking, fades, and SFX."""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
import pytest

from services.audio_studio import AudioStudio, MusicGenerator, SoundFX, TempoMatcher
from services.edit_executor.compiler import FFmpegCompiler
from shared.contracts.timeline import (
    AspectRatio,
    AudioSegmentIR,
    RenderProfileIR,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
)


@pytest.fixture
def tmp_audio_dir(tmp_path):
    d = tmp_path / "audio_out"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_music_generator_creates_real_audio(tmp_audio_dir):
    gen = MusicGenerator(output_dir=tmp_audio_dir)
    out_file = await gen.generate(mood="cinematic", duration=2.0, style="cinematic")

    assert os.path.exists(out_file)
    assert os.path.getsize(out_file) > 1000

    # Verify audio file with ffprobe
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", out_file],
        capture_output=True,
        text=True,
    )
    dur = float(res.stdout.strip())
    assert 1.8 <= dur <= 2.5


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_sound_fx_generator(tmp_audio_dir):
    sfx = SoundFX(output_dir=tmp_audio_dir)
    for fx_name in ("whoosh", "impact", "bass_drop"):
        fx_file = await sfx.generate_fx(fx_name)
        assert os.path.exists(fx_file)
        assert os.path.getsize(fx_file) > 500


def test_tempo_matcher_bpm():
    matcher = TempoMatcher()
    # Fast cuts: average 1.0s cut -> 120 BPM
    bpm_fast = matcher.calculate_target_bpm([1.0, 1.0, 1.0])
    assert 100.0 <= bpm_fast <= 140.0

    # Slower cuts: average 3.0s cut -> ~80 BPM
    bpm_slow = matcher.calculate_target_bpm([3.0, 3.0, 3.0])
    assert 60.0 <= bpm_slow <= 100.0


def test_compiler_generates_ducking_filtergraph(tmp_path):
    seg = TimelineSegmentIR(
        segment_id="v_01",
        clip_id="c_01",
        source_path="/media/video1.mp4",
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
        has_audio=True,
    )
    bg = AudioSegmentIR(
        track_id="bg_01",
        source_path="/media/music.m4a",
        source_in_ms=0,
        timeline_start_ms=0,
        timeline_end_ms=3000,
        volume=0.4,
        fade_in_ms=500,
        fade_out_ms=800,
        ducking=True,
        duck_threshold=0.08,
        duck_ratio=4.0,
    )
    timeline = TimelineIR(
        project_id="p1",
        width=1920,
        height=1080,
        fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        audio_segments=(bg,),
        total_duration_ms=3000,
    )

    compiler = FFmpegCompiler()
    cmd = compiler.compile(timeline, output_path=tmp_path / "out.mp4", force_cpu=True)

    filter_graph = cmd.filter_graph
    assert "sidechaincompress=" in filter_graph
    assert "amix=" in filter_graph
    assert "afade=t=in" in filter_graph
    assert "afade=t=out" in filter_graph
    assert "-map" in cmd.argv
    assert "[a_mixed]" in cmd.argv


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_audio_studio_end_to_end_render(tmp_path):
    # 1. Create a synthetic test video clip with audio
    clip_path = tmp_path / "test_clip.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-c:a", "aac",
        str(clip_path),
    ], check=True)

    # 2. Build TimelineIR with hero cut
    seg = TimelineSegmentIR(
        segment_id="seg_01",
        clip_id="clip_01",
        source_path=str(clip_path),
        source_in_ms=0,
        source_out_ms=3000,
        timeline_start_ms=0,
        timeline_end_ms=3000,
        has_audio=True,
        is_hero=True,
    )
    timeline = TimelineIR(
        project_id="test_audio_render",
        width=640,
        height=360,
        fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    # 3. Create soundtrack with AudioStudio
    studio = AudioStudio(output_dir=str(tmp_path))
    tracks = await studio.create_soundtrack(
        timeline=timeline,
        style="cinematic",
        enable_ducking=True,
        music_volume=0.3,
        add_sfx_on_hero=False,
    )

    assert len(tracks) >= 1
    assert tracks[0].ducking is True
    assert os.path.exists(tracks[0].source_path)

    # 4. Attach tracks to timeline and compile
    final_timeline = timeline.model_copy(update={"audio_segments": tracks})
    out_mp4 = tmp_path / "final_ducked_render.mp4"
    compiler = FFmpegCompiler()
    cmd = compiler.compile(final_timeline, output_path=out_mp4, force_cpu=True)

    # 5. Execute render
    proc = await asyncio.create_subprocess_exec(
        *cmd.to_list(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, f"Render failed: {stderr.decode()}"
    assert out_mp4.exists()
    assert out_mp4.stat().st_size > 5000
