"""
MONTA — Ingest Orchestrator (Layer 2)
=======================================
One call per file, ordered cheapest-check-first:

    sanitize name → extension → stream to storage (hash + size cap)
      → dedupe by SHA-256 → ffprobe → media rules → project quota

Side-effect discipline: a file that fails *any* check leaves nothing behind —
the stored object is deleted before the error is raised, so a rejected upload
never occupies storage or a quota slot. Duplicates skip ffprobe entirely and
reuse the metadata already proven for that hash.

This module holds no database or HTTP code: callers pass the project's current
clips and a dedupe lookup, and receive a decided result.
"""

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from services.media_gateway.config import DEFAULT_LIMITS, GatewayLimits, ResolutionClass
from services.media_gateway.metadata import MediaMetadata, MediaProbeError, MetadataExtractor
from services.media_gateway.storage import LocalMediaStorage, StoredFile
from services.media_gateway.validation import (
    ClipQuota,
    FileRejected,
    ValidationCode,
    ValidationIssue,
    check_extension,
    check_size,
    resolution_class_for,
    sanitize_filename,
    validate_media,
    validate_project_quota,
)
from shared.observability import catalog as m
from shared.observability.tracing import span

logger = logging.getLogger("monta.media_gateway")

#: Any absolute POSIX path, used to keep server paths out of user-facing text.
_ABS_PATH = re.compile(r"(/[^\s:,)]+)+")

DedupeLookup = Callable[[str], Awaitable["DuplicateMatch | None"]]


@dataclass(frozen=True)
class DuplicateMatch:
    """An already-stored clip with the same SHA-256."""

    clip_id: str
    storage_key: str
    metadata: MediaMetadata
    same_project: bool


@dataclass(frozen=True)
class IngestResult:
    clip_id: str
    filename: str
    stored: StoredFile
    metadata: MediaMetadata
    resolution_class: ResolutionClass
    duplicate_of: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


def _redact_paths(message: str, path: Path) -> str:
    """Strip filesystem paths out of a tool's error text before showing it to a user.

    Removes the exact path we passed in, then any remaining absolute path, leaving the part
    that actually helps ("Invalid data found when processing input").
    """
    cleaned = message.replace(str(path), "").replace(f"{path.name}", "")
    cleaned = _ABS_PATH.sub("", cleaned)
    cleaned = re.sub(r"\s*:\s*", ": ", cleaned).strip(" :;,")
    return cleaned or "the file is not decodable"


class MediaGateway:
    """Validates and stores one upload. Stateless; safe to share across requests."""

    def __init__(self, storage: LocalMediaStorage, extractor: MetadataExtractor,
                 limits: GatewayLimits = DEFAULT_LIMITS):
        self.storage = storage
        self.extractor = extractor
        self.limits = limits

    async def ingest(
        self,
        *,
        project_id: str,
        clip_id: str,
        filename: str,
        chunks: AsyncIterator[bytes],
        existing: Sequence[ClipQuota],
        dedupe: DedupeLookup | None = None,
    ) -> IngestResult:
        """Ingest one file. Raises ``FileRejected`` with every user-facing issue."""
        with span("gateway.ingest", clip_id=clip_id) as sp:
            safe_name = sanitize_filename(filename, self.limits)
            issue = check_extension(safe_name, self.limits)
            if issue:
                self._reject([issue])
            extension = safe_name.rsplit(".", 1)[-1].lower()

            stored = await self.storage.save_stream(project_id, clip_id, extension, chunks)
            m.UPLOAD_BYTES.inc(stored.size_bytes)
            sp.set(bytes=stored.size_bytes, sha256=stored.sha256[:12])
            try:
                issue = check_size(stored.size_bytes, self.limits)
                if issue:
                    self._reject([issue])

                duplicate = await dedupe(stored.sha256) if dedupe else None
                if duplicate is not None:
                    metadata = duplicate.metadata
                    m.UPLOAD_DEDUPE.inc(scope="project" if duplicate.same_project else "global")
                    sp.set(duplicate_of=duplicate.clip_id)
                else:
                    metadata = await self._probe(stored)

                issues = validate_media(metadata, self.limits)
                if issues:
                    self._reject(issues)
                resolution_class = resolution_class_for(metadata, self.limits)
                issues = validate_project_quota(existing, resolution_class, metadata.duration_s, self.limits)
                if issues:
                    self._reject(issues)
            except BaseException:
                self.storage.delete(stored.key)
                raise

            if duplicate is not None and duplicate.same_project:
                # The identical file is already in this project: drop the copy, point at the original.
                self.storage.delete(stored.key)
                stored = StoredFile(key=duplicate.storage_key, path=self.storage.path_for(duplicate.storage_key),
                                    size_bytes=metadata.file_size_bytes, sha256=stored.sha256)

            m.UPLOAD_ACCEPTED.inc(resolution_class=resolution_class.name)
            # Not "filename": that is a LogRecord attribute, and colliding with it used to
            # abort the very request this line reports on.
            logger.info("clip accepted", extra={"clip_id": clip_id, "clip_filename": safe_name,
                                                "bytes": stored.size_bytes, "duration_s": round(metadata.duration_s, 2),
                                                "resolution": metadata.resolution,
                                                "duplicate_of": duplicate.clip_id if duplicate else None})
            return IngestResult(
                clip_id=clip_id, filename=safe_name, stored=stored, metadata=metadata,
                resolution_class=resolution_class,
                duplicate_of=duplicate.clip_id if duplicate and duplicate.same_project else None,
            )

    async def _probe(self, stored: StoredFile) -> MediaMetadata:
        try:
            return await self.extractor.extract(stored.path)
        except MediaProbeError as e:
            # ffprobe's diagnostics quote the absolute path it was given, which is a server
            # filesystem path containing the storage layout, the project id and the clip id.
            # That goes in the log, where it is useful, and never in the response — a
            # rejection message is shown to the uploader and could be screenshotted anywhere.
            detail = _redact_paths(str(e), stored.path)
            logger.info("probe rejected a file", extra={"key": stored.key, "probe_error": str(e)})
            self._reject([ValidationIssue(
                ValidationCode.UNREADABLE_MEDIA,
                "This file could not be read as video. It may be corrupt, or not a real video file "
                f"({detail}).",
                {"reason": detail},
            )])

    @staticmethod
    def _reject(issues: Sequence[ValidationIssue]) -> None:
        for issue in issues:
            m.UPLOAD_REJECTED.inc(reason=issue.code.value)
        logger.info("clip rejected", extra={"reasons": [i.code.value for i in issues]})
        raise FileRejected(issues)
