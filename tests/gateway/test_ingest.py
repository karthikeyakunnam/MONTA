"""
The ordered ingest pipeline: sanitize → extension → store → size → dedupe → probe →
media rules → quota.

Order is not cosmetic. Each step is more expensive than the last, and two of the
orderings are load-bearing:

* extension **before** storing, so a `.exe` never touches the disk;
* dedupe **before** probing, so re-uploading a known file skips ffprobe entirely.

The other property tested here is cleanup: a rejection at *any* step must leave no bytes
behind. A gateway that stores first and validates later turns every rejected upload into
permanent disk usage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.media_gateway import (
    ClipQuota,
    DuplicateMatch,
    FileRejected,
    GatewayLimits,
    LocalMediaStorage,
    MediaGateway,
    MetadataExtractor,
    ValidationCode,
)
from services.media_gateway.config import ResolutionClass

from .conftest import MediaFixtures, byte_chunks, file_chunks, requires_ffmpeg

pytestmark = requires_ffmpeg


async def ingest(gateway: MediaGateway, path: Path, *, project_id: str = "p1", clip_id: str = "c1",
                 filename: str | None = None, existing: list[ClipQuota] | None = None, dedupe=None):
    return await gateway.ingest(project_id=project_id, clip_id=clip_id, filename=filename or path.name,
                                chunks=file_chunks(path), existing=existing or [], dedupe=dedupe)


async def test_a_valid_clip_is_stored_probed_and_classified(gateway: MediaGateway, media: MediaFixtures) -> None:
    result = await ingest(gateway, media.hd)

    assert result.clip_id == "c1"
    assert result.filename == "hd_clip.mp4"
    assert result.stored.key == "uploads/p1/c1.mp4"
    assert result.stored.path.exists()
    assert result.stored.size_bytes == media.hd.stat().st_size
    assert len(result.stored.sha256) == 64
    assert result.resolution_class.name == "1080p"
    assert result.metadata.duration_s == pytest.approx(2.0, abs=0.2)
    assert result.metadata.has_audio is True
    assert result.is_duplicate is False


async def test_4k_is_classified_into_its_own_budget(gateway: MediaGateway, media: MediaFixtures) -> None:
    result = await ingest(gateway, media.uhd)
    assert result.resolution_class.name == "4k"


@pytest.mark.parametrize("attr,extension", [("mov", "mov"), ("mkv", "mkv")])
async def test_every_accepted_container_round_trips(gateway: MediaGateway, media: MediaFixtures,
                                                    attr: str, extension: str) -> None:
    result = await ingest(gateway, getattr(media, attr))
    assert result.stored.key.endswith(f".{extension}")


# ------------------------------------------------------------------ rejections


async def test_an_executable_renamed_to_mp4_is_rejected_and_its_bytes_deleted(
    gateway: MediaGateway, media: MediaFixtures, storage: LocalMediaStorage
) -> None:
    """
    The headline security case. The extension lies, so the file *is* written — and must then
    be removed when the probe proves it is not a video.
    """
    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.not_media)

    assert excinfo.value.issues[0].code == ValidationCode.UNREADABLE_MEDIA
    assert not storage.exists("uploads/p1/c1.mp4")
    assert list((storage.root / ".incoming").glob("*")) == []


async def test_a_real_executable_extension_never_reaches_the_disk(
    gateway: MediaGateway, tmp_path: Path, storage: LocalMediaStorage
) -> None:
    """
    An `.exe` is rejected on its name, before a single byte is stored.

    Proven by the chunk iterator never being advanced: if the gateway stored first, the
    generator would have been consumed.
    """
    consumed = False

    async def chunks():
        nonlocal consumed
        consumed = True
        yield b"MZ\x90\x00"

    with pytest.raises(FileRejected) as excinfo:
        await gateway.ingest(project_id="p1", clip_id="c1", filename="payload.exe",
                             chunks=chunks(), existing=[])

    assert excinfo.value.issues[0].code == ValidationCode.UNSUPPORTED_EXTENSION
    assert consumed is False, "the file was read before its extension was checked"
    assert not any(storage.root.rglob("*.exe"))


async def test_a_zip_renamed_to_mp4_is_rejected(gateway: MediaGateway, tmp_path: Path,
                                                storage: LocalMediaStorage) -> None:
    """Zip bomb defence starts here: a PK archive with a video extension is not a video."""
    archive = tmp_path / "archive.mp4"
    archive.write_bytes(b"PK\x03\x04" + b"\x00" * 2048)

    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, archive)

    assert excinfo.value.issues[0].code == ValidationCode.UNREADABLE_MEDIA
    assert not storage.exists("uploads/p1/c1.mp4")


async def test_a_truncated_upload_is_rejected(gateway: MediaGateway, media: MediaFixtures) -> None:
    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.truncated)
    assert excinfo.value.issues[0].code == ValidationCode.UNREADABLE_MEDIA


async def test_an_empty_upload_is_rejected_as_empty_not_as_unreadable(
    gateway: MediaGateway, storage: LocalMediaStorage
) -> None:
    """Size is checked before the probe, so an empty file gets the clearer message."""
    with pytest.raises(FileRejected) as excinfo:
        await gateway.ingest(project_id="p1", clip_id="c1", filename="clip.mp4",
                             chunks=byte_chunks(), existing=[])
    assert excinfo.value.issues[0].code == ValidationCode.EMPTY_FILE
    assert not storage.exists("uploads/p1/c1.mp4")


async def test_a_clip_shorter_than_the_minimum_is_rejected(gateway: MediaGateway, media: MediaFixtures) -> None:
    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.tiny)
    assert excinfo.value.issues[0].code == ValidationCode.CLIP_TOO_SHORT


async def test_an_oversized_file_is_rejected_and_cleaned_up(
    media: MediaFixtures, extractor: MetadataExtractor, tmp_path: Path
) -> None:
    tight = GatewayLimits(max_file_bytes=1024)
    storage = LocalMediaStorage(tmp_path / "s", tight)
    gateway = MediaGateway(storage, extractor, tight)

    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.hd)

    assert excinfo.value.issues[0].code == ValidationCode.FILE_TOO_LARGE
    assert not storage.exists("uploads/p1/c1.mp4")
    assert list((storage.root / ".incoming").glob("*")) == []


async def test_a_clip_over_the_project_quota_is_rejected_and_cleaned_up(
    gateway: MediaGateway, media: MediaFixtures, storage: LocalMediaStorage
) -> None:
    """Quota is the last check, so its rejection is the one most likely to leak bytes."""
    existing = [ClipQuota("1080p", 30.0) for _ in range(20)]

    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.hd, existing=existing)

    assert excinfo.value.issues[0].code == ValidationCode.PROJECT_CLIP_LIMIT
    assert not storage.exists("uploads/p1/c1.mp4")


async def test_a_dropped_connection_mid_upload_leaves_nothing(
    gateway: MediaGateway, storage: LocalMediaStorage
) -> None:
    async def dies():
        yield b"\x00" * 8192
        raise ConnectionResetError("client vanished")

    with pytest.raises(ConnectionResetError):
        await gateway.ingest(project_id="p1", clip_id="c1", filename="clip.mp4", chunks=dies(), existing=[])

    assert not storage.exists("uploads/p1/c1.mp4")
    assert list((storage.root / ".incoming").glob("*")) == []


async def test_a_resolution_above_every_class_is_rejected(
    media: MediaFixtures, extractor: MetadataExtractor, tmp_path: Path
) -> None:
    """A deployment that only allows 720p must reject the 1080p fixture, with the right code."""
    hd_only = GatewayLimits(classes=(ResolutionClass(name="720p", max_pixels=1280 * 720,
                                                     max_clips=10, max_total_seconds=300),))
    storage = LocalMediaStorage(tmp_path / "s", hd_only)
    gateway = MediaGateway(storage, extractor, hd_only)

    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.hd)

    assert excinfo.value.issues[0].code == ValidationCode.RESOLUTION_TOO_HIGH
    assert not storage.exists("uploads/p1/c1.mp4")


# ------------------------------------------------------------------ deduplication


async def test_an_identical_file_in_the_same_project_reuses_the_original_bytes(
    gateway: MediaGateway, media: MediaFixtures, storage: LocalMediaStorage
) -> None:
    """
    The second copy must not double the disk usage, and must not be probed again.

    `probe_calls` is the assertion that matters: dedupe sits *before* the probe precisely so
    a re-upload costs a hash, not an ffprobe run.
    """
    first = await ingest(gateway, media.hd, clip_id="c1")
    probe_calls = 0
    original_probe = gateway.extractor.extract

    async def counting_probe(path):
        nonlocal probe_calls
        probe_calls += 1
        return await original_probe(path)

    gateway.extractor.extract = counting_probe        # type: ignore[method-assign]

    async def dedupe(sha256: str) -> DuplicateMatch | None:
        if sha256 == first.stored.sha256:
            return DuplicateMatch(clip_id="c1", storage_key=first.stored.key,
                                  metadata=first.metadata, same_project=True)
        return None

    second = await ingest(gateway, media.hd, clip_id="c2", dedupe=dedupe)

    assert probe_calls == 0, "a duplicate must not be probed again"
    assert second.duplicate_of == "c1"
    assert second.is_duplicate is True
    assert second.stored.key == first.stored.key           # points at the original
    assert not storage.exists("uploads/p1/c2.mp4")          # the copy was removed
    assert storage.exists("uploads/p1/c1.mp4")


async def test_an_identical_file_in_another_project_keeps_its_own_copy(
    gateway: MediaGateway, media: MediaFixtures, storage: LocalMediaStorage
) -> None:
    """
    Cross-project dedupe reuses the *metadata* but not the bytes.

    Sharing bytes across projects would mean one creator deleting a clip could break
    another's project. The saving we take is the probe; the isolation we keep is the file.
    """
    first = await ingest(gateway, media.hd, project_id="pA", clip_id="c1")

    async def dedupe(sha256: str) -> DuplicateMatch | None:
        return DuplicateMatch(clip_id="c1", storage_key=first.stored.key,
                              metadata=first.metadata, same_project=False)

    second = await ingest(gateway, media.hd, project_id="pB", clip_id="c2", dedupe=dedupe)

    assert second.duplicate_of is None                     # not a duplicate *within* pB
    assert second.stored.key == "uploads/pB/c2.mp4"
    assert storage.exists("uploads/pB/c2.mp4")
    assert storage.exists("uploads/pA/c1.mp4")


async def test_a_duplicate_still_has_to_fit_the_quota(
    gateway: MediaGateway, media: MediaFixtures
) -> None:
    """Dedupe saves work, not budget: a re-upload still counts against the project's limits."""
    first = await ingest(gateway, media.hd, clip_id="c1")

    async def dedupe(sha256: str) -> DuplicateMatch | None:
        return DuplicateMatch(clip_id="c1", storage_key=first.stored.key,
                              metadata=first.metadata, same_project=True)

    with pytest.raises(FileRejected) as excinfo:
        await ingest(gateway, media.hd, clip_id="c2",
                     existing=[ClipQuota("1080p", 30.0) for _ in range(20)], dedupe=dedupe)
    assert excinfo.value.issues[0].code == ValidationCode.PROJECT_CLIP_LIMIT


async def test_different_files_are_not_treated_as_duplicates(
    gateway: MediaGateway, media: MediaFixtures
) -> None:
    a = await ingest(gateway, media.hd, clip_id="c1")
    b = await ingest(gateway, media.hd_silent, clip_id="c2")
    assert a.stored.sha256 != b.stored.sha256


async def test_a_filename_is_sanitised_on_the_way_in(gateway: MediaGateway, media: MediaFixtures) -> None:
    """The stored name is safe; the storage key never depends on it at all."""
    result = await ingest(gateway, media.hd, filename="../../../etc/My Holiday!!.mp4")
    assert result.filename == "My_Holiday.mp4"
    assert result.stored.key == "uploads/p1/c1.mp4"
