"""
Metadata extraction — against real files, produced by ffmpeg.

Every assertion here is about a number we later make product decisions on: duration feeds
the story budget, fps feeds cut timing, the resolution class feeds the quota. A test that
mocked ffprobe would let a parsing regression through, so these run the real binary and
skip cleanly when it is not installed.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from services.media_gateway.metadata import (
    MediaProbeError,
    MetadataExtractor,
    parse_probe_output,
)

from .conftest import FFPROBE, MediaFixtures, requires_ffmpeg

pytestmark = requires_ffmpeg


async def test_probes_a_real_1080p_clip_with_audio(extractor: MetadataExtractor, media: MediaFixtures) -> None:
    meta = await extractor.extract(media.hd)

    assert meta.width == 1920 and meta.height == 1080
    assert meta.resolution == "1920x1080"
    assert meta.video_codec == "h264"
    assert meta.fps == pytest.approx(30.0, abs=0.01)
    assert meta.duration_s == pytest.approx(2.0, abs=0.2)
    assert "mp4" in meta.containers
    assert meta.has_audio is True
    assert len(meta.audio_streams) == 1
    assert meta.audio_streams[0].codec == "aac"
    assert meta.file_size_bytes == media.hd.stat().st_size
    assert meta.bitrate and meta.bitrate > 0


async def test_a_clip_with_no_audio_track_reports_no_audio(extractor: MetadataExtractor, media: MediaFixtures) -> None:
    """The story layer needs to know this: a silent clip cannot carry a beat-matched cut."""
    meta = await extractor.extract(media.hd_silent)

    assert meta.has_audio is False
    assert list(meta.audio_streams) == []
    assert meta.fps == pytest.approx(25.0, abs=0.01)


async def test_probes_4k(extractor: MetadataExtractor, media: MediaFixtures) -> None:
    meta = await extractor.extract(media.uhd)
    assert (meta.width, meta.height) == (3840, 2160)
    assert meta.fps == pytest.approx(24.0, abs=0.01)


@pytest.mark.parametrize("attr,expected_container", [("mov", "mov"), ("mkv", "matroska")])
async def test_probes_every_accepted_container(extractor: MetadataExtractor, media: MediaFixtures,
                                               attr: str, expected_container: str) -> None:
    meta = await extractor.extract(getattr(media, attr))
    assert expected_container in meta.containers
    assert meta.video_codec == "h264"


async def test_a_file_with_no_video_stream_is_a_probe_error(extractor: MetadataExtractor,
                                                            media: MediaFixtures) -> None:
    """An audio-only .mp4 probes fine as a container but has nothing to edit."""
    with pytest.raises(MediaProbeError):
        await extractor.extract(media.audio_only)


async def test_an_executable_renamed_to_mp4_is_rejected(extractor: MetadataExtractor,
                                                        media: MediaFixtures) -> None:
    """
    The security case: content, not extension, decides.

    A payload with a Mach-O/PE header and a `.mp4` name passes the extension check. ffprobe
    is what proves it is not a video, which is why probing is mandatory rather than an
    optimisation we could skip for speed.
    """
    with pytest.raises(MediaProbeError):
        await extractor.extract(media.not_media)


async def test_a_truncated_file_is_rejected(extractor: MetadataExtractor, media: MediaFixtures) -> None:
    """First 2 KB of a valid mp4: an ftyp box with no moov. Must not probe as a tiny clip."""
    with pytest.raises(MediaProbeError):
        await extractor.extract(media.truncated)


async def test_an_empty_file_is_rejected(extractor: MetadataExtractor, media: MediaFixtures) -> None:
    with pytest.raises(MediaProbeError):
        await extractor.extract(media.empty)


async def test_a_missing_path_is_a_probe_error(extractor: MetadataExtractor, tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError):
        await extractor.extract(tmp_path / "does-not-exist.mp4")


async def test_available_reports_whether_ffprobe_exists() -> None:
    assert MetadataExtractor(ffprobe="ffprobe").available() is True
    assert MetadataExtractor(ffprobe="ffprobe-that-does-not-exist").available() is False


async def test_a_missing_ffprobe_binary_fails_as_a_probe_error(media: MediaFixtures) -> None:
    """A broken deployment must produce a clean rejection, not an OSError out of the endpoint."""
    with pytest.raises(MediaProbeError):
        await MetadataExtractor(ffprobe="/nonexistent/ffprobe").extract(media.hd)


@pytest.fixture
def hanging_ffprobe(tmp_path: Path) -> Path:
    """
    A stand-in binary that ignores its arguments and never exits.

    Needed because the real thing cannot be made to hang on demand, and `/bin/sleep` exits
    immediately when handed ffprobe's flags — a test built on it would pass without ever
    exercising the timeout.
    """
    script = tmp_path / "hanging-ffprobe"
    script.write_text("#!/bin/sh\nsleep 300\n")
    script.chmod(0o755)
    return script


async def test_a_hanging_probe_is_killed_at_the_timeout(media: MediaFixtures, hanging_ffprobe: Path) -> None:
    """
    ffprobe on a network path or a pathological file can hang forever.

    Two things must happen: the wait raises, and the process group is reaped. A leaked probe
    would hold a worker slot and a file handle for its full 300s.
    """
    extractor = MetadataExtractor(ffprobe=str(hanging_ffprobe), timeout_s=0.4)
    started = time.perf_counter()
    with pytest.raises(MediaProbeError, match="timed out"):
        await extractor.extract(media.hd)
    elapsed = time.perf_counter() - started

    assert 0.4 <= elapsed < 5.0, f"expected the timeout to fire at ~0.4s, took {elapsed:.2f}s"
    assert _sleep_processes_started_by_the_test() == 0, "the probe's process group leaked"


async def test_cancelling_a_probe_propagates_and_reaps_the_child(media: MediaFixtures,
                                                                 hanging_ffprobe: Path) -> None:
    """
    A client disconnect cancels the request task, and the subprocess must die with it.

    The cancellation itself must also propagate: swallowing it and reporting a probe failure
    would hide the fact that the caller is being torn down.
    """
    extractor = MetadataExtractor(ffprobe=str(hanging_ffprobe), timeout_s=300.0)
    task = asyncio.create_task(extractor.extract(media.hd))
    await asyncio.sleep(0.3)                    # let the subprocess actually start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    assert _sleep_processes_started_by_the_test() == 0, "cancellation left the probe running"


def _sleep_processes_started_by_the_test() -> int:
    """Count surviving `sleep 300` children — the signature of a leaked probe."""
    import subprocess

    result = subprocess.run(["ps", "-o", "command="], capture_output=True, text=True)
    return sum(1 for line in result.stdout.splitlines() if line.strip().endswith("sleep 300"))


# ------------------------------------------------------------------ pure parsing


def test_parse_handles_a_variable_frame_rate_stream() -> None:
    """VFR phone footage reports r_frame_rate as a ratio; 0/0 must not divide by zero."""
    payload = {
        "format": {"format_name": "mov,mp4,m4a", "duration": "12.5", "bit_rate": "5000000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
             "r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001", "duration": "12.5"},
        ],
    }
    meta = parse_probe_output(payload, file_size_bytes=8_000_000)
    assert meta.fps == pytest.approx(29.97, abs=0.01)
    assert meta.display_size == (1080, 1920)


def test_a_stream_with_no_frame_rate_is_rejected_rather_than_defaulted() -> None:
    """
    Both frame rates reporting 0/0 means the file cannot be cut on a timeline.

    Guessing 30fps here would push a wrong number into the story layer's cut timing, so
    the file is rejected instead — the creator can re-export it.
    """
    payload = {
        "format": {"format_name": "matroska,webm", "duration": "4.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                     "r_frame_rate": "0/0", "avg_frame_rate": "0/0"}],
    }
    with pytest.raises(MediaProbeError, match="frame rate"):
        parse_probe_output(payload, file_size_bytes=1000)


def test_parse_reads_rotation_from_side_data_and_from_tags() -> None:
    """ffmpeg 5+ moved rotation into side_data; older files still carry a tag. Support both."""
    tagged = {
        "format": {"format_name": "mov,mp4,m4a", "duration": "3.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                     "r_frame_rate": "30/1", "tags": {"rotate": "90"}}],
    }
    side_data = {
        "format": {"format_name": "mov,mp4,m4a", "duration": "3.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                     "r_frame_rate": "30/1",
                     "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}],
    }
    assert parse_probe_output(tagged, 1000).display_size == (1080, 1920)
    assert parse_probe_output(side_data, 1000).display_size == (1080, 1920)


def test_parse_falls_back_to_the_stream_duration_when_the_container_lacks_one() -> None:
    """Some MKV files carry no format duration; the video stream still does."""
    payload = {
        "format": {"format_name": "matroska,webm"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                     "r_frame_rate": "25/1", "duration": "7.25"}],
    }
    assert parse_probe_output(payload, 1000).duration_s == pytest.approx(7.25)


def test_parse_rejects_a_payload_with_no_video_stream() -> None:
    payload = {"format": {"format_name": "mov,mp4,m4a", "duration": "5.0"},
               "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
    with pytest.raises(MediaProbeError):
        parse_probe_output(payload, 1000)


def test_parse_collects_every_audio_stream() -> None:
    """A multi-language export has several audio tracks; the editor must see all of them."""
    payload = {
        "format": {"format_name": "mov,mp4,m4a", "duration": "5.0"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
             "r_frame_rate": "30/1"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
             "sample_rate": "48000", "tags": {"language": "eng"}},
            {"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 6,
             "sample_rate": "48000", "tags": {"language": "fra"}},
        ],
    }
    meta = parse_probe_output(payload, 1000)
    assert [s.index for s in meta.audio_streams] == [1, 2]
    assert [s.channels for s in meta.audio_streams] == [2, 6]
    assert meta.has_audio is True


def test_metadata_is_immutable() -> None:
    """Probe results are facts about a file; nothing downstream may edit them in place."""
    meta = parse_probe_output(
        {"format": {"format_name": "mov,mp4,m4a", "duration": "3.0"},
         "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                      "r_frame_rate": "30/1"}]},
        1000,
    )
    with pytest.raises(Exception):
        meta.duration_s = 99.0      # type: ignore[misc]


# ------------------------------------------------------------------ concurrency ceiling


async def test_concurrent_probes_are_capped(media: MediaFixtures) -> None:
    """
    ffprobe is a real process, so async concurrency does not make it free.

    Without a ceiling, a burst of uploads forks one process per file, every probe slows down,
    and the slowest start tripping the timeout — a load spike would turn into *rejected*
    uploads rather than merely slow ones. This asserts the gate actually holds: with a cap of
    2, never more than 2 probes run at once, and all of them still complete.
    """
    extractor = MetadataExtractor(ffprobe=FFPROBE or "ffprobe", timeout_s=30.0, max_concurrency=2)
    in_flight = 0
    peak = 0
    original = extractor._extract

    async def watched(path):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            return await original(path)
        finally:
            in_flight -= 1

    extractor._extract = watched        # type: ignore[method-assign]

    results = await asyncio.gather(*[extractor.extract(media.hd) for _ in range(10)])

    assert len(results) == 10
    assert all(r.width == 1920 for r in results)
    assert peak <= 2, f"{peak} probes ran at once despite a cap of 2"


async def test_the_cap_is_per_event_loop_and_does_not_deadlock(media: MediaFixtures) -> None:
    """
    The extractor is a process-wide singleton while tests and embedded uses run several loops.

    A semaphore holds futures belonging to the loop that awaited it, so one shared across
    loops is a latent hang. Reusing the same extractor from a second loop must just work.
    """
    extractor = MetadataExtractor(ffprobe=FFPROBE or "ffprobe", timeout_s=30.0, max_concurrency=1)
    first = await extractor.extract(media.hd)

    def in_a_fresh_loop():
        return asyncio.run(extractor.extract(media.hd))

    second = await asyncio.to_thread(in_a_fresh_loop)
    assert first.duration_s == pytest.approx(second.duration_s)
    assert len(extractor._gates) == 2
