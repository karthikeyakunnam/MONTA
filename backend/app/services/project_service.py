"""
MONTA — Project Service
=========================
Project lifecycle and the pipeline hand-off.

Two invariants live here:

* **One active run per project.** ``start_pipeline`` refuses while a job is in
  flight, so two clicks cannot start two pipelines over the same clips.
* **The job row exists before the broker call.** If submission fails, the row
  stays as ``submit_failed`` with the error, the project goes back to
  ``uploading``, and the client gets a 503 it can retry — nothing is lost.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import (ACTIVE_STATUSES, Clip, ClipStatus, Job, JobKind, JobState, Project, ProjectStatus,
                        Render, User)
from app.services.progress import ProgressBus, ProgressEvent, Stage
from app.services.queue import PipelineQueue, QueueUnavailable
from shared.exceptions import MontaError
from shared.observability import catalog as m
from shared.observability.context import correlation

logger = logging.getLogger("monta.projects")


class ProjectNotFound(MontaError):
    """No such project for this user."""


class ProjectConflict(MontaError):
    """The project is not in a state where this action is allowed."""


@dataclass(frozen=True)
class ProjectSummary:
    """Everything the dashboard needs, all of it eagerly loaded.

    Every collection here is fetched with an explicit query rather than left to a
    relationship. Under asyncio a lazy load raises ``MissingGreenlet`` the moment a
    serializer touches it, so "the ORM will fetch it when asked" is not an option: what is
    not loaded here is not available at all.
    """

    project: Project
    clips: list[Clip]
    jobs: list[Job]
    renders: list[Render]

    @property
    def usable_clips(self) -> list[Clip]:
        return [c for c in self.clips if c.status != ClipStatus.REJECTED.value]

    @property
    def latest_job(self) -> Job | None:
        return self.jobs[0] if self.jobs else None

    @property
    def latest_render(self) -> Render | None:
        return self.renders[0] if self.renders else None

    @property
    def total_duration_s(self) -> float:
        return round(sum(c.duration or 0.0 for c in self.usable_clips), 2)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ProjectService:
    def __init__(self, db: AsyncSession, queue: PipelineQueue | None = None, progress: ProgressBus | None = None):
        self.db = db
        self.queue = queue
        self.progress = progress

    # ------------------------------------------------------------------ users

    async def ensure_user(self, user_id: str, email: str | None = None) -> User:
        """Real auth is not built yet; every project still points at a real user row."""
        user = await self.db.get(User, user_id)
        if user is None:
            user = User(id=user_id, email=email or f"{user_id}@monta.local", hashed_password="unset",
                        display_name=user_id)
            self.db.add(user)
            await self.db.flush()
        return user

    # ------------------------------------------------------------------ CRUD

    async def create_project(self, *, user_id: str, title: str, prompt: str, target_platform: str,
                             description: str | None = None, edit_options: dict | None = None) -> Project:
        await self.ensure_user(user_id)
        project = Project(
            id=new_id("proj"), user_id=user_id, title=title.strip(), description=description,
            raw_prompt=prompt.strip(), target_platform=target_platform, status=ProjectStatus.DRAFT.value,
            edit_options=edit_options or {},
        )
        self.db.add(project)
        await self.db.flush()
        logger.info("project created", extra={"project_id": project.id, "title": project.title})
        return project

    async def get(self, project_id: str, user_id: str) -> Project:
        project = await self.db.get(Project, project_id)
        if project is None or project.user_id != user_id:
            raise ProjectNotFound(f"project {project_id} not found")
        return project

    async def get_locked(self, project_id: str, user_id: str) -> Project:
        """Row-locked read, so concurrent uploads serialize on the quota check.

        ``with_for_update`` is a no-op on SQLite (single writer), and a real row lock on
        PostgreSQL, which is what production uses.
        """
        stmt = select(Project).where(Project.id == project_id)
        if self.db.bind and self.db.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        project = (await self.db.execute(stmt)).scalar_one_or_none()
        if project is None or project.user_id != user_id:
            raise ProjectNotFound(f"project {project_id} not found")
        return project

    async def list_projects(self, user_id: str, limit: int = 50) -> list[tuple[Project, int]]:
        """Projects newest first, each with its usable clip count."""
        counts = (
            select(Clip.project_id, func.count(Clip.id).label("n"))
            .where(Clip.status != ClipStatus.REJECTED.value)
            .group_by(Clip.project_id)
            .subquery()
        )
        stmt = (
            select(Project, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.project_id == Project.id)
            .where(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
            .limit(limit)
        )
        return [(row[0], int(row[1])) for row in (await self.db.execute(stmt)).all()]

    async def summary(self, project_id: str, user_id: str) -> ProjectSummary:
        project = await self.get(project_id, user_id)
        clips = list((await self.db.execute(
            select(Clip).where(Clip.project_id == project_id).order_by(Clip.created_at)
        )).scalars())
        jobs = list((await self.db.execute(
            select(Job).where(Job.project_id == project_id)
            .order_by(Job.prompt_version.desc(), Job.created_at.desc(), Job.id.desc()).limit(100)
        )).scalars())
        renders = list((await self.db.execute(
            select(Render).where(Render.project_id == project_id)
            .order_by(Render.created_at.desc(), Render.platform.desc(), Render.id.desc()).limit(100)
        )).scalars())
        return ProjectSummary(project=project, clips=clips, jobs=jobs, renders=renders)

    async def update(self, project_id: str, user_id: str, *, title: str | None = None, prompt: str | None = None,
                     target_platform: str | None = None, edit_options: dict | None = None) -> Project:
        project = await self.get(project_id, user_id)
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is {project.status}; wait for it to finish before editing it")
        if prompt is not None or target_platform is not None or edit_options is not None:
            has_jobs = (await self.db.execute(select(Job.id).where(Job.project_id == project_id).limit(1))).first()
            if has_jobs:
                raise ProjectConflict("use Revise edit to change a processed project; this preserves prompt and render history")
        if title is not None:
            project.title = title.strip()
        if prompt is not None:
            project.raw_prompt = prompt.strip()
        if target_platform is not None:
            project.target_platform = target_platform
        if edit_options is not None:
            project.edit_options = edit_options
        await self.db.flush()
        # ``updated_at`` is generated by the database (``onupdate=func.now()``), so the flush
        # leaves it expired. Reading it later — as the response serializer does — would trigger
        # a lazy refresh outside the async context and raise ``MissingGreenlet``. Refreshing
        # here loads the server's value while we still have a greenlet to do IO in.
        await self.db.refresh(project)
        return project

    async def delete(self, project_id: str, user_id: str, storage=None) -> None:
        project = await self.get(project_id, user_id)
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is {project.status}; cancel or wait before deleting it")
        await self.db.delete(project)
        await self.db.flush()
        if storage is not None:
            storage.delete_project(project_id)
        logger.info("project deleted", extra={"project_id": project_id})

    # ------------------------------------------------------------------ status

    async def set_status(self, project: Project, status: ProjectStatus, detail: str | None = None) -> None:
        project.status = status.value
        project.status_detail = detail
        await self.db.flush()

    # ------------------------------------------------------------------ pipeline

    async def start_pipeline(self, project_id: str, user_id: str, *, idempotency_key: str | None = None) -> Job:
        """Queue the current project version without creating another project."""
        if self.queue is None:
            raise QueueUnavailable("no queue is configured on this deployment")
        project = await self.get_locked(project_id, user_id)
        if idempotency_key:
            existing = await self._idempotent_job(project_id, idempotency_key)
            if existing:
                self._assert_same_snapshot(existing, project.raw_prompt, project.target_platform, project.edit_options or {})
                return existing
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is already {project.status}")
        usable = await self._usable_clip_count(project_id)
        self._require_project_inputs(project, usable)
        job = self._new_job(project, idempotency_key=idempotency_key)
        self.db.add(job)
        project.status = ProjectStatus.QUEUED.value
        project.status_detail = "waiting for a worker"
        await self.db.flush()
        await self.db.commit()  # worker must never race an uncommitted job row/snapshot
        return await self._submit_committed(project, job, usable=usable)

    async def revise(self, project_id: str, user_id: str, *, prompt: str, target_platform: str,
                     edit_options: dict, idempotency_key: str) -> Job:
        """Persist a new immutable edit version, then submit it against existing uploads."""
        if self.queue is None:
            raise QueueUnavailable("no queue is configured on this deployment")
        project = await self.get_locked(project_id, user_id)
        existing = await self._idempotent_job(project_id, idempotency_key)
        if existing:
            self._assert_same_snapshot(existing, prompt, target_platform, edit_options)
            return existing
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is already {project.status}")
        usable = await self._usable_clip_count(project_id)
        self._require_project_inputs(project, usable, prompt)

        current_version = int(project.prompt_version or 1)
        result = await self.db.execute(
            update(Project)
            .where(Project.id == project_id, Project.prompt_version == current_version,
                   Project.status.not_in(tuple(ACTIVE_STATUSES)))
            .values(prompt_version=current_version + 1, raw_prompt=prompt.strip(),
                    target_platform=target_platform, edit_options=edit_options,
                    status=ProjectStatus.QUEUED.value, status_detail="waiting for revised edit")
        )
        if result.rowcount != 1:
            await self.db.rollback()
            raise ProjectConflict("another revision changed this project; refresh and try again")
        await self.db.refresh(project)
        job = self._new_job(project, idempotency_key=idempotency_key)
        self.db.add(job)
        await self.db.flush()
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self._idempotent_job(project_id, idempotency_key)
            if existing:
                self._assert_same_snapshot(existing, prompt, target_platform, edit_options)
                return existing
            raise
        return await self._submit_committed(project, job, usable=usable, revision=True)

    async def retry(self, project_id: str, user_id: str, failed_job_id: str, *, idempotency_key: str) -> Job:
        """Retry a failed latest revision using its stored options and existing uploads."""
        if self.queue is None:
            raise QueueUnavailable("no queue is configured on this deployment")
        project = await self.get_locked(project_id, user_id)
        existing = await self._idempotent_job(project_id, idempotency_key)
        if existing:
            return existing
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is already {project.status}")
        failed = await self.db.get(Job, failed_job_id)
        if failed is None or failed.project_id != project_id:
            raise ProjectNotFound(f"job {failed_job_id} not found")
        if failed.state not in (JobState.FAILED.value, JobState.SUBMIT_FAILED.value):
            raise ProjectConflict("only failed or unqueued revisions can be retried")
        if failed.prompt_version != project.prompt_version:
            raise ProjectConflict("this is an older edit version; revise the current prompt to run it again")
        usable = await self._usable_clip_count(project_id)
        self._require_project_inputs(project, usable, failed.prompt_snapshot)
        job = Job(
            id=new_id("job"), project_id=project_id, kind=JobKind.PIPELINE.value, state=JobState.QUEUED.value,
            trace_id=correlation().get("trace_id"), prompt_version=failed.prompt_version,
            prompt_snapshot=failed.prompt_snapshot, target_platform_snapshot=failed.target_platform_snapshot,
            edit_options_snapshot=dict(failed.edit_options_snapshot or {}), idempotency_key=idempotency_key,
            retry_of_job_id=failed.id, created_at=datetime.now(timezone.utc),
        )
        self.db.add(job)
        project.status = ProjectStatus.QUEUED.value
        project.status_detail = f"retrying failed edit version {failed.prompt_version}"
        await self.db.flush()
        await self.db.commit()
        return await self._submit_committed(project, job, usable=usable, revision=True)

    async def cancel_queued(self, project_id: str, user_id: str, job_id: str) -> Job:
        """Cancel only before a worker starts; running FFmpeg/model work is not killed unsafely."""
        project = await self.get_locked(project_id, user_id)
        stmt = select(Job).where(Job.id == job_id, Job.project_id == project_id)
        if self.db.bind and self.db.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        job = (await self.db.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise ProjectNotFound(f"job {job_id} not found")
        if job.state not in (JobState.PENDING.value, JobState.QUEUED.value):
            raise ProjectConflict("a worker has started this job; cancellation is available only while queued")
        job.state = JobState.CANCELLED.value
        job.error = "Cancelled before worker start."
        job.finished_at = datetime.now(timezone.utc)
        if job.prompt_version is not None and int(job.prompt_version) != int(project.prompt_version or 1):
            await self.db.commit()
            if self.queue is not None:
                await self.queue.cancel(task_id=job.task_id or job.id)
            return job
        has_render = (await self.db.execute(select(Render.id).where(
            Render.project_id == project_id, Render.status == "complete").limit(1))).first() is not None
        project.status = ProjectStatus.COMPLETE.value if has_render else ProjectStatus.UPLOADING.value
        project.status_detail = "queued revision cancelled; previous render is preserved" if has_render else "queued revision cancelled"
        await self.db.commit()
        if self.queue is not None:
            await self.queue.cancel(task_id=job.task_id or job.id)
        await self._publish(project_id, Stage.FAILED, "Queued revision cancelled", job_id=job.id)
        return job

    async def _usable_clip_count(self, project_id: str) -> int:
        return int((await self.db.execute(
            select(func.count(Clip.id)).where(Clip.project_id == project_id, Clip.status != ClipStatus.REJECTED.value)
        )).scalar_one())

    @staticmethod
    def _require_project_inputs(project: Project, usable: int, prompt: str | None = None) -> None:
        if not (prompt or project.raw_prompt or "").strip():
            raise ProjectConflict("add an editing prompt before starting")
        if usable == 0:
            raise ProjectConflict("upload at least one clip before starting")

    @staticmethod
    def _new_job(project: Project, *, idempotency_key: str | None, retry_of_job_id: str | None = None) -> Job:
        return Job(
            id=new_id("job"), project_id=project.id, kind=JobKind.PIPELINE.value, state=JobState.QUEUED.value,
            trace_id=correlation().get("trace_id"), prompt_version=project.prompt_version,
            prompt_snapshot=project.raw_prompt, target_platform_snapshot=project.target_platform,
            edit_options_snapshot=dict(project.edit_options or {}), idempotency_key=idempotency_key,
            retry_of_job_id=retry_of_job_id, created_at=datetime.now(timezone.utc),
        )

    async def _idempotent_job(self, project_id: str, key: str) -> Job | None:
        return (await self.db.execute(select(Job).where(Job.project_id == project_id,
                                                        Job.idempotency_key == key))).scalar_one_or_none()

    @staticmethod
    def _assert_same_snapshot(job: Job, prompt: str | None, platform: str | None, options: dict) -> None:
        if (job.prompt_snapshot != (prompt or "").strip() or job.target_platform_snapshot != platform
                or (job.edit_options_snapshot or {}) != (options or {})):
            raise ProjectConflict("this request key was already used for a different edit")

    async def _submit_committed(self, project: Project, job: Job, *, usable: int, revision: bool = False) -> Job:
        if self.queue is None:
            raise QueueUnavailable("no queue is configured on this deployment")
        try:
            submitted = await self.queue.submit_pipeline(job_id=job.id, project_id=project.id, trace_id=job.trace_id)
        except QueueUnavailable as e:
            job.state = JobState.SUBMIT_FAILED.value
            job.error = str(e)
            previous_render = (await self.db.execute(select(Render.id).where(
                Render.project_id == project.id, Render.status == "complete").limit(1))).first() is not None
            project.status = ProjectStatus.COMPLETE.value if previous_render else ProjectStatus.UPLOADING.value
            project.status_detail = f"could not queue the {'revision' if revision else 'job'}: {e}"
            await self.db.commit()
            m.JOB_STATE.inc(kind="pipeline", state=JobState.SUBMIT_FAILED.value)
            await self._publish(project.id, Stage.FAILED, f"Could not start processing: {e}", job_id=job.id)
            raise
        job.task_id = submitted.task_id
        await self.db.commit()
        m.JOB_STATE.inc(kind="pipeline", state=JobState.QUEUED.value)
        await self._publish(project.id, Stage.VALIDATING,
                            f"{usable} clips queued for {'revision' if revision else 'analysis'}",
                            job_id=job.id, detail={"clips": usable, "prompt_version": job.prompt_version})
        return job

    async def _publish(self, project_id: str, stage: Stage, message: str, **kw) -> None:
        if self.progress is None:
            return
        await self.progress.publish(ProgressEvent(project_id=project_id, stage=stage, message=message,
                                                  trace_id=correlation().get("trace_id"), **kw))
