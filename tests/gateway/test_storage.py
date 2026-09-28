"""
Storage: atomic publication, path confinement, streaming limits, cleanup.

The properties under test are the ones a crash or a hostile client can violate:
a partially written file must never be visible under its final key, a key must never
resolve outside the storage root, and a client that lies about its file size must be cut
off mid-stream rather than filling the disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest

from services.media_gateway import DEFAULT_LIMITS, FileRejected, GatewayLimits, LocalMediaStorage, ValidationCode
from services.media_gateway.storage import CHUNK_BYTES

from .conftest import byte_chunks


async def failing_chunks(good: bytes, error: Exception):
    """Emit some bytes, then blow up the way a dropped connection does."""
    yield good
    raise error


def test_keys_are_predictable_and_confined(storage: LocalMediaStorage) -> None:
    key = storage.key_for("p1", "c1", "MP4")
    assert key == "uploads/p1/c1.mp4"
    assert storage.path_for(key) == (storage.root / "uploads" / "p1" / "c1.mp4")


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "uploads/../../../etc/passwd",
        "/etc/passwd",
        "uploads/p1/../../../../tmp/x.mp4",
    ],
)
def test_path_for_refuses_to_escape_the_storage_root(storage: LocalMediaStorage, key: str) -> None:
    """The last line of defence: even a key from a corrupted database cannot reach outside."""
    with pytest.raises(ValueError, match="escapes the upload root"):
        storage.path_for(key)


async def test_save_stream_hashes_and_publishes_atomically(storage: LocalMediaStorage) -> None:
    payload = b"a" * 5000 + b"b" * 5000
    stored = await storage.save_stream("p1", "c1", "mp4", byte_chunks(payload[:5000], payload[5000:]))

    assert stored.key == "uploads/p1/c1.mp4"
    assert stored.size_bytes == len(payload)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.path.read_bytes() == payload
    # Nothing is left behind in the staging directory.
    assert list((storage.root / ".incoming").glob("*")) == []


async def test_an_interrupted_upload_never_becomes_visible(storage: LocalMediaStorage) -> None:
    """
    A dropped connection must leave no file under the final key.

    This is why the writer stages into `.incoming` and finishes with `os.replace`: a reader
    (or the worker) can only ever see a complete file. Writing directly to the final path
    would expose a truncated video that ffprobe would happily read as a 0.3s clip.
    """
    with pytest.raises(ConnectionError):
        await storage.save_stream("p1", "c1", "mp4", failing_chunks(b"x" * 4096, ConnectionError("client gone")))

    assert not storage.exists("uploads/p1/c1.mp4")
    assert not (storage.root / "uploads" / "p1" / "c1.mp4").exists()
    # And the staged temp file is cleaned up, not orphaned.
    assert list((storage.root / ".incoming").glob("*")) == []


async def test_oversized_stream_is_cut_off_instead_of_being_written_whole() -> None:
    """
    A client can lie in Content-Length, so the ceiling must be enforced while writing.

    The assertion that matters is not just the rejection: it is that we stopped *early*.
    With a 1 MiB limit and a 16 MiB body, at most one chunk past the limit may be written.
    """
    limits = GatewayLimits(max_file_bytes=1024 * 1024)
    root = Path(os.environ.get("TMPDIR", "/tmp")) / f"monta-oversize-{time.time_ns()}"
    storage = LocalMediaStorage(root, limits)
    written_chunks = 0

    async def big():
        nonlocal written_chunks
        for _ in range(16):
            written_chunks += 1
            yield b"z" * (1024 * 1024)

    with pytest.raises(FileRejected) as excinfo:
        await storage.save_stream("p1", "c1", "mp4", big())

    assert excinfo.value.issues[0].code == ValidationCode.FILE_TOO_LARGE
    assert written_chunks <= 2, "the limit must abort the stream, not be checked after the fact"
    assert not storage.exists("uploads/p1/c1.mp4")
    assert list((root / ".incoming").glob("*")) == []


async def test_streaming_memory_is_bounded_by_the_chunk_size(storage: LocalMediaStorage) -> None:
    """
    The writer must never accumulate the file in memory.

    Proven structurally: the largest object the storage layer is handed is one chunk, and
    tracemalloc's peak over a 12 MiB upload stays within a few chunk-sized buffers. A
    `content = await file.read()` implementation would peak at the whole file.
    """
    import tracemalloc

    total_mib = 12
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[1]

    async def chunks():
        for _ in range(total_mib):
            yield b"q" * CHUNK_BYTES

    stored = await storage.save_stream("p1", "c1", "mp4", chunks())
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    assert stored.size_bytes == total_mib * CHUNK_BYTES
    assert peak - baseline < 6 * CHUNK_BYTES, f"peak {peak - baseline} bytes suggests buffering the whole upload"


async def test_delete_and_delete_project(storage: LocalMediaStorage) -> None:
    await storage.save_stream("p1", "c1", "mp4", byte_chunks(b"one"))
    await storage.save_stream("p1", "c2", "mov", byte_chunks(b"two"))
    await storage.save_stream("p2", "c3", "mp4", byte_chunks(b"three"))

    assert storage.delete("uploads/p1/c1.mp4") is True
    assert storage.delete("uploads/p1/c1.mp4") is False      # idempotent: already gone
    assert storage.delete_project("p1") == 1                 # c2 remains
    assert storage.exists("uploads/p2/c3.mp4")
    assert not (storage.root / "uploads" / "p1").exists()


async def test_two_clips_can_share_a_project_directory_concurrently(storage: LocalMediaStorage) -> None:
    """Concurrent uploads into one project must not collide over the directory or temp names."""
    results = await asyncio.gather(*[
        storage.save_stream("p1", f"c{i}", "mp4", byte_chunks(bytes([i]) * 2048))
        for i in range(12)
    ])
    assert len({r.key for r in results}) == 12
    assert len({r.sha256 for r in results}) == 12
    assert all(r.path.exists() and r.size_bytes == 2048 for r in results)


def test_sweep_incoming_removes_only_stale_staging_files(storage: LocalMediaStorage) -> None:
    """
    Startup cleanup must not delete an upload that is still being written by another worker.

    A shared storage root can be served by several API processes; sweeping on age is what
    keeps a restart from truncating a live upload in a sibling process.
    """
    incoming = storage.root / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fresh = incoming / "fresh.tmp"
    stale = incoming / "stale.tmp"
    fresh.write_bytes(b"in progress")
    stale.write_bytes(b"abandoned")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    assert storage.sweep_incoming(older_than_s=3600) == 1
    assert fresh.exists()
    assert not stale.exists()


async def test_size_of_reports_bytes_on_disk(storage: LocalMediaStorage) -> None:
    stored = await storage.save_stream("p1", "c1", "mp4", byte_chunks(b"x" * 321))
    assert storage.size_of(stored.key) == 321
    assert storage.size_of("uploads/p1/missing.mp4") == 0


async def test_limits_are_honoured_from_the_injected_policy(tmp_path: Path) -> None:
    """Storage does not read global config: the limit it enforces is the one it was given."""
    tight = LocalMediaStorage(tmp_path / "s", GatewayLimits(max_file_bytes=100))
    with pytest.raises(FileRejected):
        await tight.save_stream("p1", "c1", "mp4", byte_chunks(b"x" * 101))
    loose = LocalMediaStorage(tmp_path / "s2", DEFAULT_LIMITS)
    assert (await loose.save_stream("p1", "c1", "mp4", byte_chunks(b"x" * 101))).size_bytes == 101
