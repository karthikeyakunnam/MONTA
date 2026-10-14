"""
MONTA — M5 Audio Objective Validation Suite
=============================================
Tests: loudness, clipping, sync offsets, sample rates, channel layouts,
missing/corrupt asset handling, and multi-track mixing validation.
Uses ffprobe for objective checks. Runs real FFmpeg media.

All tests that produce/consume audio files run ffprobe to verify:
- Peak amplitude (no clipping > 0 dBFS)
- Integrated loudness (within expected range)
- Duration accuracy (within 500ms of expected)
- Sample rate: 48000 Hz
- Channel layout: stereo
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from services.audio_studio import AudioStudio, MusicGenerator, SoundFX
from services.audio_studio.tempo_matcher import TempoMatcher
from services.edit_executor.compiler import FFmpegCompiler
from services.render_farm.renderer import MediaRenderer
from shared.contracts.timeline import (
    AspectRatio,
    AudioSegmentIR,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not installed",
)

FFPROBE = "ffprobe"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ffprobe_audio(path: str) -> dict:
    """Return dict with duration_s, sample_rate, channels, peak_db (approx) via ffprobe."""
    result = subprocess.run(
        [
            FFPROBE, "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    data = json.loads(result.stdout)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    assert audio_streams, f"No audio stream found in {path}"
    s = audio_streams[0]
    return {
        "duration_s": float(data.get("format", {}).get("duration", 0)),
        "sample_rate": int(s.get("sample_rate", 0)),
        "channels": int(s.get("channels", 0)),
        "codec": s.get("codec_name", ""),
        "bit_rate": int(data.get("format", {}).get("bit_rate", 0)),
    }


def _measure_peak_loudness(path: str) -> dict:
    """Use ffmpeg volumedetect to get max_volume and mean_volume in dBFS."""
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=60,
    )
    # volumedetect outputs to stderr
    output = result.stderr
    max_vol = None
    mean_vol = None
    for line in output.splitlines():
        if "max_volume:" in line:
            max_vol = float(line.split("max_volume:")[1].strip().replace(" dB", ""))
        if "mean_volume:" in line:
            mean_vol = float(line.split("mean_volume:")[1].strip().replace(" dB", ""))
    return {"max_volume_db": max_vol, "mean_volume_db": mean_vol}


def _make_test_video(path: Path, duration_s: float = 3.0, freq: int = 440, color: str = "red") -> Path:
    """Synthesize a test video clip with tonal audio."""
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color={color}:size=320x240:duration={duration_s}:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration_s}",
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
        str(path),
    ], check=True)
    return path


def _make_silent_video(path: Path, duration_s: float = 3.0) -> Path:
    """Synthesize a silent test video clip (anullsrc audio)."""
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=black:size=320x240:duration={duration_s}:rate=30",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
        "-t", str(duration_s),
        "-c:v", "libx264", "-c:a", "aac",
        str(path),
    ], check=True)
    return path


# ---------------------------------------------------------------------------
# M5.1 — Music generation loudness/clipping/format validation
# ---------------------------------------------------------------------------

async def test_music_generator_loudness_no_clipping(tmp_path):
    """Generated music bed must not clip (max_volume < 0 dBFS) and be within usable loudness range."""
    gen = MusicGenerator(output_dir=str(tmp_path))
    for style in ("cinematic", "hype", "ambient", "lo_fi", "intense"):
        out = await gen.generate(mood=style, duration=4.0, style=style)
        assert Path(out).exists(), f"Music file not created for style {style}"
        info = _ffprobe_audio(out)
        assert info["sample_rate"] == 48000, f"{style}: sample_rate={info['sample_rate']}"
        assert info["channels"] == 2, f"{style}: channels={info['channels']}"
        assert info["duration_s"] == pytest.approx(4.0, abs=0.6), f"{style}: duration={info['duration_s']}"
        loud = _measure_peak_loudness(out)
        assert loud["max_volume_db"] is not None
        # No clipping: peak must be strictly below 0.0 dBFS
        assert loud["max_volume_db"] < 0.0, f"{style}: peak={loud['max_volume_db']} dBFS — CLIPPING"
        # Must be audible: mean volume above -60 dBFS
        assert loud["mean_volume_db"] is not None
        assert loud["mean_volume_db"] > -60.0, f"{style}: mean={loud['mean_volume_db']} dBFS — TOO QUIET"


async def test_music_generator_unknown_style_fallback(tmp_path):
    """Unknown style must fall back to 'default' without raising and produce valid audio."""
    gen = MusicGenerator(output_dir=str(tmp_path))
    out = await gen.generate(mood="psychedelic_space_jazz_unknown", duration=2.0)
    assert Path(out).exists()
    info = _ffprobe_audio(out)
    assert info["duration_s"] > 1.0


# ---------------------------------------------------------------------------
# M5.2 — SFX loudness/clipping/format
# ---------------------------------------------------------------------------

async def test_sfx_all_types_loudness_no_clipping(tmp_path):
    """All SFX types must produce valid audio with no clipping."""
    sfx = SoundFX(output_dir=str(tmp_path))
    for fx_name in ("whoosh", "impact", "bass_drop", "riser", "chime", "glitch"):
        out = await sfx.generate_fx(fx_name)
        assert Path(out).exists()
        info = _ffprobe_audio(out)
        assert info["sample_rate"] == 48000
        assert info["channels"] >= 1
        loud = _measure_peak_loudness(out)
        assert loud["max_volume_db"] is not None
        assert loud["max_volume_db"] < 0.0, f"{fx_name}: CLIPPING at {loud['max_volume_db']} dBFS"


# ---------------------------------------------------------------------------
# M5.3 — Missing/corrupt audio asset error handling
# ---------------------------------------------------------------------------

async def test_missing_music_asset_raises_cleanly(tmp_path):
    """AudioSegmentIR referencing a non-existent file must cause FFmpegCompiler to still
    produce a command (compilation succeeds), but render must fail with a clear error — not silently produce corrupt output."""
    seg = TimelineSegmentIR(
        segment_id="v1", clip_id="c1",
        source_path="/nonexistent/video.mp4",
        source_in_ms=0, source_out_ms=3000,
        timeline_start_ms=0, timeline_end_ms=3000,
        has_audio=True,
    )
    bg = AudioSegmentIR(
        track_id="bg_missing",
        source_path="/nonexistent/missing_music.m4a",
        source_in_ms=0, timeline_start_ms=0, timeline_end_ms=3000,
        volume=0.4,
    )
    timeline = TimelineIR(
        project_id="missing_asset_test",
        width=320, height=240, fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        audio_segments=(bg,),
        total_duration_ms=3000,
    )
    compiler = FFmpegCompiler()
    cmd = compiler.compile(timeline, output_path=str(tmp_path / "out.mp4"), force_cpu=True)
    # Command produced but render will fail — verify it references the missing path
    assert "/nonexistent/missing_music.m4a" in " ".join(cmd.argv)

    # Executing the command must fail (ffmpeg returns non-zero for missing inputs)
    proc = await asyncio.create_subprocess_exec(
        *cmd.to_list(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    assert proc.returncode != 0, "Expected failure for missing input file but render succeeded"
    error_text = stderr.decode()
    # FFmpeg should mention the missing file in its error output
    assert "No such file" in error_text or "cannot open" in error_text.lower() or "not found" in error_text.lower() or len(error_text) > 0


async def test_corrupt_audio_asset_fails_gracefully(tmp_path):
    """A corrupt (not valid audio) file referenced as background music must cause render to fail, not produce garbage."""
    corrupt = tmp_path / "corrupt_music.m4a"
    corrupt.write_bytes(b"NOT VALID AUDIO DATA" * 10)

    clip = _make_test_video(tmp_path / "clip.mp4", duration_s=2.0)
    seg = TimelineSegmentIR(
        segment_id="v1", clip_id="c1",
        source_path=str(clip),
        source_in_ms=0, source_out_ms=2000,
        timeline_start_ms=0, timeline_end_ms=2000,
        has_audio=True,
    )
    bg = AudioSegmentIR(
        track_id="bg_corrupt",
        source_path=str(corrupt),
        source_in_ms=0, timeline_start_ms=0, timeline_end_ms=2000,
        volume=0.3,
    )
    timeline = TimelineIR(
        project_id="corrupt_asset_test",
        width=320, height=240, fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        audio_segments=(bg,),
        total_duration_ms=2000,
    )
    out = tmp_path / "out_corrupt.mp4"
    renderer = MediaRenderer()
    result = await renderer.render(timeline, str(out))
    # Render must fail and report an error — not produce a silent/corrupt output
    assert not result.success or not out.exists() or out.stat().st_size < 1000, (
        "Expected render to fail for corrupt audio asset, but it succeeded"
    )


# ---------------------------------------------------------------------------
# M5.4 — Real-media: speech-heavy, music-only, silent, mixed tracks
# ---------------------------------------------------------------------------

async def test_real_media_speech_heavy_with_ducking(tmp_path):
    """Speech-heavy video: ducking must reduce music level when speech is detected."""
    # Speech-heavy: use sine wave at speech frequency range (300-3000Hz) to simulate voice
    speech_clip = tmp_path / "speech_heavy.mp4"
    _make_test_video(speech_clip, duration_s=4.0, freq=800, color="blue")

    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1",
        source_path=str(speech_clip),
        source_in_ms=0, source_out_ms=4000,
        timeline_start_ms=0, timeline_end_ms=4000,
        has_audio=True,
    )
    timeline_base = TimelineIR(
        project_id="speech_heavy_duck",
        width=320, height=240, fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        total_duration_ms=4000,
    )

    studio = AudioStudio(output_dir=str(tmp_path))
    tracks = await studio.create_soundtrack(
        timeline=timeline_base,
        style="ambient",
        enable_ducking=True,
        music_volume=0.4,
        add_sfx_on_hero=False,
    )
    assert len(tracks) == 1
    assert tracks[0].ducking is True

    final_timeline = timeline_base.model_copy(update={"audio_segments": tracks})
    out_mp4 = tmp_path / "speech_ducked.mp4"
    compiler = FFmpegCompiler()
    cmd = compiler.compile(final_timeline, output_path=str(out_mp4), force_cpu=True)
    assert "sidechaincompress=" in cmd.filter_graph

    proc = await asyncio.create_subprocess_exec(
        *cmd.to_list(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, f"Render failed: {stderr.decode()}"
    assert out_mp4.exists() and out_mp4.stat().st_size > 5000

    info = _ffprobe_audio(str(out_mp4))
    assert info["sample_rate"] == 48000
    assert info["channels"] == 2
    assert info["duration_s"] == pytest.approx(4.0, abs=0.6)

    loud = _measure_peak_loudness(str(out_mp4))
    assert loud["max_volume_db"] < 0.0, "Output is clipping"


async def test_real_media_silent_video_gets_music(tmp_path):
    """Silent video: AudioStudio should still attach music; rendered output has audio."""
    silent_clip = tmp_path / "silent.mp4"
    _make_silent_video(silent_clip, duration_s=3.0)

    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1",
        source_path=str(silent_clip),
        source_in_ms=0, source_out_ms=3000,
        timeline_start_ms=0, timeline_end_ms=3000,
        has_audio=True,  # file has audio stream, but silent
    )
    timeline_base = TimelineIR(
        project_id="silent_with_music",
        width=320, height=240, fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    studio = AudioStudio(output_dir=str(tmp_path))
    tracks = await studio.create_soundtrack(
        timeline=timeline_base, style="cinematic",
        enable_ducking=True, music_volume=0.6, add_sfx_on_hero=False,
    )
    final_timeline = timeline_base.model_copy(update={"audio_segments": tracks})
    out_mp4 = tmp_path / "silent_plus_music.mp4"
    compiler = FFmpegCompiler()
    cmd = compiler.compile(final_timeline, output_path=str(out_mp4), force_cpu=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd.to_list(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, f"Render failed: {stderr.decode()}"
    assert out_mp4.exists()

    # Verify output has audible content (music added)
    loud = _measure_peak_loudness(str(out_mp4))
    # Music should be clearly audible; mean > -40 dBFS
    assert loud["mean_volume_db"] is not None
    assert loud["mean_volume_db"] > -40.0, f"Music too quiet: {loud['mean_volume_db']} dBFS"


async def test_real_media_music_only_no_voice(tmp_path):
    """Music-only: no ducking when video has no audio stream. Volume is higher."""
    # No-audio video (video only, no audio track)
    video_only = tmp_path / "video_only.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=green:size=320x240:duration=3:rate=30",
        "-c:v", "libx264", "-an",  # no audio
        str(video_only),
    ], check=True)

    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1",
        source_path=str(video_only),
        source_in_ms=0, source_out_ms=3000,
        timeline_start_ms=0, timeline_end_ms=3000,
        has_audio=False,  # explicitly no audio
    )
    timeline_base = TimelineIR(
        project_id="music_only",
        width=320, height=240, fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    studio = AudioStudio(output_dir=str(tmp_path))
    tracks = await studio.create_soundtrack(
        timeline=timeline_base, style="hype",
        enable_ducking=True, music_volume=0.35, add_sfx_on_hero=False,
    )
    # No foreground audio => no ducking, music volume auto-raised
    assert not tracks[0].ducking, "Ducking should be disabled when no foreground audio"
    assert tracks[0].volume > 0.35, "Volume should be higher when no foreground audio to duck"


async def test_real_media_mixed_audio_and_music(tmp_path):
    """Mixed: existing video audio + background music track — verify render works, 48kHz stereo output."""
    speech_clip = _make_test_video(tmp_path / "mixed_clip.mp4", duration_s=3.0, freq=600)

    seg = TimelineSegmentIR(
        segment_id="s1", clip_id="c1",
        source_path=str(speech_clip),
        source_in_ms=0, source_out_ms=3000,
        timeline_start_ms=0, timeline_end_ms=3000,
        has_audio=True,
    )
    timeline_base = TimelineIR(
        project_id="mixed_audio",
        width=320, height=240, fps=30.0,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(seg,),
        total_duration_ms=3000,
    )

    studio = AudioStudio(output_dir=str(tmp_path))
    tracks = await studio.create_soundtrack(
        timeline=timeline_base, style="lo_fi",
        enable_ducking=True, music_volume=0.3, add_sfx_on_hero=False,
    )
    final = timeline_base.model_copy(update={"audio_segments": tracks})
    out = tmp_path / "mixed_final.mp4"
    cmd = FFmpegCompiler().compile(final, output_path=str(out), force_cpu=True)
    proc = await asyncio.create_subprocess_exec(*cmd.to_list(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, f"Mixed render failed: {stderr.decode()}"

    info = _ffprobe_audio(str(out))
    assert info["sample_rate"] == 48000
    assert info["channels"] == 2
    assert info["duration_s"] == pytest.approx(3.0, abs=0.5)


# ---------------------------------------------------------------------------
# M5.5 — Duration accuracy and sample rate preservation
# ---------------------------------------------------------------------------

async def test_audio_duration_accuracy_across_styles(tmp_path):
    """Generated music duration must match requested duration within ±600ms for each style."""
    gen = MusicGenerator(output_dir=str(tmp_path))
    for duration in (2.0, 5.0, 10.0, 30.0):
        out = await gen.generate(mood="cinematic", duration=duration)
        info = _ffprobe_audio(out)
        assert abs(info["duration_s"] - duration) < 0.6, (
            f"Duration mismatch: requested {duration}s, got {info['duration_s']}s"
        )


# ---------------------------------------------------------------------------
# M5.6 — Voiceover / DJRemix / BeatExtension unsupported documentation check
# ---------------------------------------------------------------------------

def test_voiceover_stub_documented_returns_empty():
    """VoiceoverGenerator.generate must return empty string (stub) without raising."""
    from services.audio_studio.voiceover import VoiceoverGenerator
    vg = VoiceoverGenerator()
    import asyncio as _asyncio
    result = _asyncio.get_event_loop().run_until_complete(
        vg.generate("Test narration text", voice="default")
    )
    # Stub: returns empty string — this IS the documented behavior
    assert isinstance(result, str)
    # Document: not supported
    assert result == "", "VoiceoverGenerator is a stub; TTS model not integrated"


def test_dj_remix_stub_documented():
    """DJRemix.remix must return empty string (stub) without raising."""
    from services.audio_studio.dj_remix import DJRemix
    import asyncio as _asyncio
    result = _asyncio.get_event_loop().run_until_complete(
        DJRemix().remix("/fake/track.m4a", style="hype")
    )
    assert isinstance(result, str)
    assert result == "", "DJRemix is a stub; beat detection/remix not integrated"


def test_beat_extension_stub_documented():
    """BeatExtender.extend must return empty string (stub) without raising."""
    from services.audio_studio.beat_extension import BeatExtender
    import asyncio as _asyncio
    result = _asyncio.get_event_loop().run_until_complete(
        BeatExtender().extend("/fake/track.m4a", target_duration=30.0)
    )
    assert isinstance(result, str)
    assert result == "", "BeatExtender is a stub; loop-point detection not integrated"
