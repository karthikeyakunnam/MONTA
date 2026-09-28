"""
Fixtures for the Layer 1/2 suite.

Two decisions shape everything here:

**Real media, not fake bytes.** Validation exists to tell the truth about a file, so the
fixtures are genuine H.264 files produced by ffmpeg (``lavfi`` sources — no assets in the
repo). A test that feeds ``b"fake mp4"`` to the extractor proves only that the extractor
rejects garbage; it cannot catch a wrong fps parse, a rotation bug or a container
mismatch. Generation is session-scoped so the cost is paid once, and the whole module
skips when ffmpeg is absent rather than silently testing nothing.

**Real database, real HTTP.** The API tests drive the actual ASGI app over
``httpx.ASGITransport`` against a per-test SQLite file created by the real Alembic-equivalent
metadata, with the real ``ProjectService``/``UploadService``/``MediaGateway`` wired in. The
only doubles are the two things that must not be started for a unit test: the Celery broker
and Redis. Both are replaced at the dependency-override seam the app already exposes, so
the code under test is unmodified production code.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from services.media_gateway import DEFAULT_LIMITS, GatewayLimits, LocalMediaStorage, MediaGateway, MetadataExtractor

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

requires_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[:400]}")


@dataclass
class MediaFixtures:
    """Paths to generated media, plus the properties each one is meant to exercise."""

    root: Path
    hd: Path                 # 1920x1080, 2s, h264, with audio
    hd_silent: Path          # 1920x1080, 1.5s, h264, no audio stream
    uhd: Path                # 3840x2160, 1s, h264 — the 4K budget class
    tiny: Path               # 1920x1080 but 0.2s — below min_clip_seconds
    mov: Path                # .mov container
    mkv: Path                # .mkv (matroska) container
    audio_only: Path         # .mp4 with no video stream at all
    not_media: Path          # executable bytes renamed to .mp4
    truncated: Path          # first 2 KB of a valid mp4: header without moov
    empty: Path              # 0 bytes


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> MediaFixtures:
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    root = tmp_path_factory.mktemp("media")

    hd = root / "hd_clip.mp4"
    _run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", str(hd)])

    hd_silent = root / "hd_silent.mp4"
    _run_ffmpeg(["-f", "lavfi", "-i", "smptebars=size=1920x1080:rate=25:duration=1.5",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(hd_silent)])

    uhd = root / "uhd_clip.mp4"
    _run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=3840x2160:rate=24:duration=1",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(uhd)])

    tiny = root / "tiny.mp4"
    _run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=0.2",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(tiny)])

    mov = root / "clip.mov"
    _run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(mov)])

    mkv = root / "clip.mkv"
    _run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=1",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(mkv)])

    audio_only = root / "audio_only.mp4"
    _run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(audio_only)])

    # A Mach-O/ELF-looking payload renamed to .mp4: the extension lies, the content does not.
    not_media = root / "payload.mp4"
    not_media.write_bytes(b"\xcf\xfa\xed\xfe" + b"MZ\x90\x00" + b"\x00" * 4096)

    truncated = root / "truncated.mp4"
    truncated.write_bytes(hd.read_bytes()[:2048])

    empty = root / "empty.mp4"
    empty.write_bytes(b"")

    return MediaFixtures(root=root, hd=hd, hd_silent=hd_silent, uhd=uhd, tiny=tiny, mov=mov, mkv=mkv,
                         audio_only=audio_only, not_media=not_media, truncated=truncated, empty=empty)


@pytest.fixture(scope="session")
def make_clip(tmp_path_factory: pytest.TempPathFactory):
    """
    Generate a real clip with given properties and *distinct bytes*.

    Needed because deduplication is content-addressed: uploading the same file five times
    exercises dedupe, not the quota. Shifting the hue per seed changes every pixel, so each
    clip has its own SHA-256 while keeping the resolution, duration and codec fixed.
    """
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    root = tmp_path_factory.mktemp("generated")
    cache: dict[tuple, Path] = {}

    def build(*, seed: int, width: int = 1920, height: int = 1080, seconds: float = 1.0,
              fps: int = 30, extension: str = "mp4") -> Path:
        key = (seed, width, height, seconds, fps, extension)
        if key in cache:
            return cache[key]
        path = root / f"gen_{width}x{height}_{seconds}s_{seed}.{extension}"
        _run_ffmpeg([
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
            "-vf", f"hue=h={(seed * 47) % 360}:s={1 + (seed % 3) * 0.2}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
        ])
        cache[key] = path
        return path

    return build


@pytest.fixture
def limits() -> GatewayLimits:
    """Production limits, unmodified. Tests that need a tighter budget build their own."""
    return DEFAULT_LIMITS


@pytest.fixture
def storage(tmp_path: Path, limits: GatewayLimits) -> LocalMediaStorage:
    return LocalMediaStorage(tmp_path / "storage", limits)


@pytest.fixture
def extractor() -> MetadataExtractor:
    return MetadataExtractor(ffprobe=FFPROBE or "ffprobe", timeout_s=30.0)


@pytest.fixture
def gateway(storage: LocalMediaStorage, extractor: MetadataExtractor, limits: GatewayLimits) -> MediaGateway:
    return MediaGateway(storage=storage, extractor=extractor, limits=limits)


async def file_chunks(path: Path, chunk_bytes: int = 64 * 1024) -> AsyncIterator[bytes]:
    """Feed a file to the gateway the way the API feeds an ``UploadFile``."""
    with path.open("rb") as handle:
        while True:
            chunk = await asyncio.to_thread(handle.read, chunk_bytes)
            if not chunk:
                return
            yield chunk


async def byte_chunks(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


# --------------------------------------------------------------------------------------
# Doubles for the two out-of-process dependencies
# --------------------------------------------------------------------------------------


@dataclass
class FakeQueue:
    """
    Stands in for Celery. Records submissions; can be told to fail like a dead broker.

    It returns the production ``SubmittedJob`` type rather than an ad-hoc stand-in: a double
    that returns a looser shape than the real thing will happily pass tests that the real
    thing would break on.
    """

    enabled: bool = True
    available: bool = True
    submitted: list[dict] = field(default_factory=list)

    async def submit_pipeline(self, *, job_id: str, project_id: str, trace_id: str | None = None):
        from app.services.queue import QueueUnavailable, SubmittedJob

        if not self.available:
            raise QueueUnavailable("broker unreachable (test)")
        self.submitted.append({"job_id": job_id, "project_id": project_id, "trace_id": trace_id})
        # Celery uses the job id as the task id, so tracking needs only one identifier.
        return SubmittedJob(task_id=job_id, queued=True)

    async def ping(self) -> bool:
        return self.available


@dataclass
class FakeProgressBus:
    """Stands in for Redis pub/sub. Keeps published events so tests can assert on them."""

    published: list = field(default_factory=list)
    available: bool = True

    async def publish(self, event) -> None:
        if not self.available:
            return          # the real bus is best-effort and must never raise into a request
        self.published.append(event)

    async def history(self, project_id: str, limit: int | None = None) -> list:
        events = [e for e in self.published if e.project_id == project_id]
        return events[-limit:] if limit else events

    async def subscribe(self, project_id: str):
        # Never yields: tests drive the socket through snapshot + history, not live traffic.
        while True:
            await asyncio.sleep(3600)
            yield ""        # pragma: no cover - unreachable, keeps this an async generator

    async def close(self) -> None:
        return None

    def stages(self) -> list[str]:
        return [str(e.stage) for e in self.published]


@dataclass
class AppHarness:
    """A live app instance plus the doubles wired into it."""

    client: object                  # httpx.AsyncClient
    app: object
    queue: FakeQueue
    bus: FakeProgressBus
    storage: LocalMediaStorage
    session_maker: object


@pytest.fixture
async def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AppHarness]:
    """
    The real FastAPI app on a real SQLite database with real services.

    The database URL and storage root are set before ``app.config`` is read, and the
    engine is created per test, so tests never share state. Only the broker and the
    progress bus are doubles.
    """
    import httpx

    db_path = tmp_path / "monta.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    monkeypatch.setenv("DEBUG", "false")

    from app import dependencies as deps
    from app.config import settings
    from app.db import session as db_session

    # Pin every setting the assertions depend on. `Settings` reads the repo's `.env`, so
    # without this a developer's local overrides (a stale SUPPORTED_FORMATS, a tighter quota)
    # would change what these tests mean. The values here are the shipped defaults.
    for field, value in {
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "STORAGE_ROOT": str(tmp_path / "storage"),
        "CORS_ORIGINS": ["http://localhost:3000"],
        "SUPPORTED_FORMATS": "mp4,mov,mkv",
        "MAX_UPLOAD_SIZE_MB": 500,
        "MAX_REQUEST_SIZE_MB": 4096,
        "MAX_CLIP_DURATION_SECONDS": 240,
        "MAX_CLIPS_1080P": 20,
        "MAX_MINUTES_1080P": 10.0,
        "MAX_CLIPS_4K": 4,
        "MAX_MINUTES_4K": 4.0,
        "QUEUE_ENABLED": True,
        "DEFAULT_USER_ID": "local",
    }.items():
        monkeypatch.setattr(settings, field, value)
    # A fresh engine per test; the module caches one globally.
    monkeypatch.setattr(db_session, "_engine", None, raising=False)
    monkeypatch.setattr(db_session, "_session_maker", None, raising=False)
    deps.get_storage.cache_clear()
    deps.get_limits.cache_clear()
    deps.get_gateway.cache_clear()
    deps.get_queue.cache_clear()

    # Import the models package first: `import app.models` binds the name `app` to the
    # *package*, which would shadow the FastAPI instance imported below.
    import app.models  # noqa: F401  - registers every table on Base.metadata
    from app.api.websockets import progress as progress_ws
    from app.db.base import Base
    from app.main import app as fastapi_app

    engine = db_session.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    queue = FakeQueue()
    bus = FakeProgressBus()
    storage = deps.get_storage()
    fastapi_app.dependency_overrides[deps.get_queue] = lambda: queue
    fastapi_app.dependency_overrides[deps.get_progress_bus] = lambda: bus
    # The websocket route resolves the bus by calling the provider directly (it has no
    # request scope for Depends), so that seam is patched at the module level.
    monkeypatch.setattr(deps, "get_progress_bus", lambda: bus)
    monkeypatch.setattr(progress_ws, "get_progress_bus", lambda: bus)

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1",
                                 headers={"X-MONTA-User": "tester"}) as client:
        yield AppHarness(client=client, app=fastapi_app, queue=queue, bus=bus, storage=storage,
                         session_maker=db_session.get_session_maker())

    fastapi_app.dependency_overrides.clear()
    await db_session.dispose_engine()


@pytest.fixture
def upload_payload():
    """Build the multipart payload for one or more real files."""

    def build(*paths: Path, field_name: str = "files") -> list[tuple[str, tuple[str, bytes, str]]]:
        return [(field_name, (p.name, p.read_bytes(), "video/mp4")) for p in paths]

    return build
