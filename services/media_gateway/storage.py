"""
MONTA — Storage Engine (Layer 2)
==================================
Layout: ``{root}/uploads/{project_id}/{clip_id}.{ext}``.

Writes stream to a temp file inside the storage root (same filesystem), so the
final publish is an atomic ``os.replace`` — a reader never sees a partial file.
The SHA-256 is computed during the same pass, so a stored file is hashed
without a second read. Over-sized uploads abort mid-stream and the temp file is
removed.

Every path is rebuilt from validated identifiers and then re-checked against
the root, so a crafted project or clip id cannot escape the storage directory.
"""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from services.media_gateway.config import DEFAULT_LIMITS, GatewayLimits
from services.media_gateway.validation import (
    ValidationCode,
    ValidationIssue,
    FileRejected,
    validate_identifier,
)

CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    key: str
    path: Path
    size_bytes: int
    sha256: str


class LocalMediaStorage:
    """Filesystem storage. The same interface fronts object storage later (put/open/delete/key)."""

    def __init__(self, root: str | Path, limits: GatewayLimits = DEFAULT_LIMITS):
        self.root = Path(root).resolve()
        self.limits = limits
        self.uploads = self.root / "uploads"
        self.tmp = self.root / ".incoming"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    def key_for(self, project_id: str, clip_id: str, extension: str) -> str:
        validate_identifier(project_id, "project_id")
        validate_identifier(clip_id, "clip_id")
        ext = extension.lower().lstrip(".")
        if ext not in self.limits.allowed_extensions:
            raise ValueError(f"refusing to store extension {ext!r}")
        return f"uploads/{project_id}/{clip_id}.{ext}"

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.uploads):
            raise ValueError(f"storage key escapes the upload root: {key!r}")
        return path

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def size_of(self, key: str) -> int:
        """Bytes on disk, or 0 when the key holds nothing.

        Total like :meth:`exists`, rather than raising: a clip row can outlive its bytes
        (a sweep, a restored database, a half-finished delete), and a size query is not the
        place to turn that into an exception.
        """
        path = self.path_for(key)
        return path.stat().st_size if path.is_file() else 0

    async def save_stream(self, project_id: str, clip_id: str, extension: str,
                          chunks: AsyncIterator[bytes]) -> StoredFile:
        """Stream ``chunks`` to storage, hashing as it goes. Raises ``FileRejected`` when too large."""
        key = self.key_for(project_id, clip_id, extension)
        final = self.path_for(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        fd, temp_name = tempfile.mkstemp(dir=self.tmp, prefix=f"{clip_id}-", suffix=f".{extension.lower()}")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.limits.max_file_bytes:
                        raise FileRejected([ValidationIssue(
                            ValidationCode.FILE_TOO_LARGE,
                            f"This file is larger than the {self.limits.max_file_bytes / (1024 * 1024):.0f} MB "
                            f"limit per clip; the upload was stopped.",
                            {"max_bytes": self.limits.max_file_bytes, "received_bytes": written},
                        )])
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)
            if written == 0:
                raise FileRejected([ValidationIssue(ValidationCode.EMPTY_FILE, "This file is empty.", {"bytes": 0})])
            os.replace(temp_path, final)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return StoredFile(key=key, path=final, size_bytes=written, sha256=digest.hexdigest())

    def delete(self, key: str) -> bool:
        path = self.path_for(key)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return existed

    def delete_project(self, project_id: str) -> int:
        validate_identifier(project_id, "project_id")
        folder = self.path_for(f"uploads/{project_id}")
        if not folder.is_dir():
            return 0
        removed = 0
        for child in folder.iterdir():
            if child.is_file():
                child.unlink()
                removed += 1
        folder.rmdir()
        return removed

    def sweep_incoming(self, older_than_s: float = 3600) -> int:
        """Delete abandoned temp files (a crash between stream and publish). Safe to run on a schedule."""
        import time

        cutoff = time.time() - older_than_s
        removed = 0
        for child in self.tmp.iterdir():
            if child.is_file() and child.stat().st_mtime < cutoff:
                child.unlink(missing_ok=True)
                removed += 1
        return removed
