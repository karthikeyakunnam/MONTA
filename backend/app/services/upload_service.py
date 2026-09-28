"""
MONTA — Upload Service
========================
Wires the Media Gateway (Layer 2 library) to the database.

Per file:

1. lock the project row, so two parallel uploads cannot both take the last quota slot;
2. read the project's current clips as quota input and as the dedupe index;
3. hand the request stream to the gateway (sanitize → store → hash → probe → validate);
4. on success write the clip row and move the project to ``uploading``;
5. on rejection write a ``rejected`` clip row — no bytes on disk, but the UI can show
   exactly why the file failed, and the row is excluded from every quota.

The stream is consumed once, in 1 MiB chunks, so memory does not scale with file size.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clip, ClipStatus, Project, ProjectStatus
from app.services.progress import ProgressBus, ProgressEvent, Stage
from app.services.project_service import ProjectService, new_id
from services.media_gateway import (
    ClipQuota,
    DuplicateMatch,
    FileRejected,
    MediaGateway,
    MediaMetadata,
)
from services.media_gateway.storage import CHUNK_BYTES
from shared.observability.context import bind, correlation
from shared.observability.tracing import span

logger = logging.getLogger("monta.upload")


@dataclass(frozen=True)
class UploadOutcome:
    """One file's result. ``clip`` is set when accepted, ``issues`` when rejected."""

    filename: str
    accepted: bool
    clip: Clip | None = None
    issues: list[dict] | None = None
    duplicate_of: str | None = None


class UploadService:
    def __init__(self, db: AsyncSession, gateway: MediaGateway, projects: ProjectService,
                 progress: ProgressBus | None = None):
        self.db = db
        self.gateway = gateway
        self.projects = projects
        self.progress = progress

    async def ingest_upload(self, *, project: Project, upload: UploadFile) -> UploadOutcome:
        """Validate and store one uploaded file, then persist it."""
        clip_id = new_id("clip")
        with bind(project_id=project.id), span("upload.ingest", clip_id=clip_id) as sp:
            existing = await self._existing_clips(project.id)
            quota = [ClipQuota(resolution_class=c.resolution_class or "1080p", duration_s=c.duration or 0.0)
                     for c in existing if c.status != ClipStatus.REJECTED.value]
            try:
                result = await self.gateway.ingest(
                    project_id=project.id, clip_id=clip_id, filename=upload.filename or "clip",
                    chunks=self._chunks(upload), existing=quota, dedupe=self._dedupe_lookup(project.id),
                )
            except FileRejected as rejected:
                sp.set(outcome="rejected", reasons=[i.code.value for i in rejected.issues])
                clip = await self._record_rejection(project, clip_id, upload.filename or "clip", rejected)
                await self._publish(project.id, Stage.VALIDATING,
                                    f"{clip.original_filename} was rejected: {rejected.issues[0].message}",
                                    clip_id=clip_id, detail={"issues": rejected.as_dicts()})
                return UploadOutcome(filename=clip.original_filename, accepted=False, issues=rejected.as_dicts())

            if result.duplicate_of:
                # These exact bytes are already in this project. The gateway has dropped the
                # second copy and pointed `stored` at the original, and the schema enforces
                # UNIQUE(project_id, sha256), so inserting another row here would raise an
                # IntegrityError on a very ordinary action — re-dragging the same folder.
                # One clip per distinct file per project is also what the story layer wants:
                # two rows over one file would let it pick the same shot twice.
                original = await self.db.get(Clip, result.duplicate_of)
                if original is not None:
                    sp.set(outcome="duplicate", duplicate_of=original.id)
                    await self._publish(
                        project.id, Stage.UPLOADED,
                        f"{upload.filename or result.filename} is already in this project as "
                        f"{original.original_filename}",
                        clip_id=original.id, detail={"duplicate_of": original.id},
                    )
                    logger.info("duplicate upload reused existing clip",
                                extra={"clip_id": original.id, "project_id": project.id})
                    return UploadOutcome(filename=upload.filename or result.filename, accepted=True,
                                         clip=original, duplicate_of=original.id)

            clip = Clip(
                id=clip_id, project_id=project.id, status=ClipStatus.UPLOADED.value,
                filename=result.filename, original_filename=upload.filename or result.filename,
                storage_key=result.stored.key, sha256=result.stored.sha256, duplicate_of=result.duplicate_of,
                container=result.metadata.container, format=result.filename.rsplit(".", 1)[-1].lower(),
                video_codec=result.metadata.video_codec, fps=round(result.metadata.fps, 3),
                width=result.metadata.display_size[0], height=result.metadata.display_size[1],
                resolution=result.metadata.resolution, resolution_class=result.resolution_class.name,
                duration=round(result.metadata.duration_s, 3), bitrate=result.metadata.bitrate,
                file_size_bytes=result.stored.size_bytes, has_audio=result.metadata.has_audio,
                audio_streams=[a.model_dump() for a in result.metadata.audio_streams],
            )
            self.db.add(clip)
            if project.status in (ProjectStatus.DRAFT.value, ProjectStatus.FAILED.value):
                await self.projects.set_status(project, ProjectStatus.UPLOADING, None)
            await self.db.flush()
            sp.set(outcome="accepted", duplicate_of=result.duplicate_of)
            await self._publish(project.id, Stage.UPLOADED, f"{clip.original_filename} uploaded", clip_id=clip_id,
                                detail={"duration_s": clip.duration, "resolution": clip.resolution,
                                        "duplicate_of": result.duplicate_of})
            return UploadOutcome(filename=clip.original_filename, accepted=True, clip=clip,
                                 duplicate_of=result.duplicate_of)

    @staticmethod
    async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
        while True:
            chunk = await upload.read(CHUNK_BYTES)
            if not chunk:
                break
            yield chunk

    async def _existing_clips(self, project_id: str) -> list[Clip]:
        return list((await self.db.execute(select(Clip).where(Clip.project_id == project_id))).scalars())

    def _dedupe_lookup(self, project_id: str):
        """Find any stored clip with the same content hash: same project first, then any project."""

        async def lookup(sha256: str) -> DuplicateMatch | None:
            stmt = (
                select(Clip)
                .where(Clip.sha256 == sha256, Clip.status != ClipStatus.REJECTED.value, Clip.duration.is_not(None))
                .order_by((Clip.project_id != project_id), Clip.created_at)
                .limit(1)
            )
            clip = (await self.db.execute(stmt)).scalars().first()
            if clip is None:
                return None
            metadata = MediaMetadata(
                container=clip.container or "unknown", duration_s=clip.duration or 0.0, fps=clip.fps or 0.0,
                video_codec=clip.video_codec or "unknown", width=clip.width or 0, height=clip.height or 0,
                bitrate=clip.bitrate, rotation=0, file_size_bytes=clip.file_size_bytes or 0,
                audio_streams=tuple(clip.audio_streams or ()),
            )
            return DuplicateMatch(clip_id=clip.id, storage_key=clip.storage_key, metadata=metadata,
                                  same_project=clip.project_id == project_id)

        return lookup

    async def _record_rejection(self, project: Project, clip_id: str, filename: str, rejected: FileRejected) -> Clip:
        """Rejections are stored so the dashboard can explain them; they hold no bytes and no quota."""
        clip = Clip(
            id=clip_id, project_id=project.id, status=ClipStatus.REJECTED.value,
            filename=filename[:255], original_filename=filename[:255], storage_key="",
            sha256=f"rejected-{clip_id}", format=(filename.rsplit(".", 1)[-1].lower()[:16] if "." in filename else ""),
            file_size_bytes=0, has_audio=False, rejection_issues=rejected.as_dicts(),
        )
        self.db.add(clip)
        await self.db.flush()
        logger.info("upload rejected", extra={"clip_id": clip_id, "project_id": project.id,
                                              "reasons": [i.code.value for i in rejected.issues]})
        return clip

    async def delete_clip(self, project: Project, clip_id: str) -> bool:
        clip = await self.db.get(Clip, clip_id)
        if clip is None or clip.project_id != project.id:
            return False
        reused = False
        if clip.storage_key:
            others = (await self.db.execute(
                select(Clip.id).where(Clip.storage_key == clip.storage_key, Clip.id != clip.id).limit(1)
            )).scalars().first()
            reused = others is not None
            if not reused:
                self.gateway.storage.delete(clip.storage_key)
        await self.db.delete(clip)
        await self.db.flush()
        logger.info("clip deleted", extra={"clip_id": clip_id, "project_id": project.id, "file_kept": reused})
        return True

    async def _publish(self, project_id: str, stage: Stage, message: str, **kw) -> None:
        if self.progress is None:
            return
        await self.progress.publish(ProgressEvent(project_id=project_id, stage=stage, message=message,
                                                  trace_id=correlation().get("trace_id"), **kw))
