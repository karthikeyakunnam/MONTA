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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
                             description: str | None = None) -> Project:
        await self.ensure_user(user_id)
        project = Project(
            id=new_id("proj"), user_id=user_id, title=title.strip(), description=description,
            raw_prompt=prompt.strip(), target_platform=target_platform, status=ProjectStatus.DRAFT.value,
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
            select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(10)
        )).scalars())
        renders = list((await self.db.execute(
            select(Render).where(Render.project_id == project_id).order_by(Render.created_at.desc()).limit(5)
        )).scalars())
        return ProjectSummary(project=project, clips=clips, jobs=jobs, renders=renders)

    async def update(self, project_id: str, user_id: str, *, title: str | None = None, prompt: str | None = None,
                     target_platform: str | None = None) -> Project:
        project = await self.get(project_id, user_id)
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is {project.status}; wait for it to finish before editing it")
        if title is not None:
            project.title = title.strip()
        if prompt is not None:
            project.raw_prompt = prompt.strip()
        if target_platform is not None:
            project.target_platform = target_platform
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

    async def start_pipeline(self, project_id: str, user_id: str) -> Job:
        """Create a job row, hand it to the queue, and return it with its tracking id."""
        if self.queue is None:
            raise QueueUnavailable("no queue is configured on this deployment")
        project = await self.get_locked(project_id, user_id)
        if project.status in ACTIVE_STATUSES:
            raise ProjectConflict(f"this project is already {project.status}")
        if not (project.raw_prompt or "").strip():
            raise ProjectConflict("add an editing prompt before starting")
        usable = (await self.db.execute(
            select(func.count(Clip.id)).where(Clip.project_id == project_id, Clip.status != ClipStatus.REJECTED.value)
        )).scalar_one()
        if usable == 0:
            raise ProjectConflict("upload at least one clip before starting")

        trace_id = correlation().get("trace_id")
        job = Job(id=new_id("job"), project_id=project_id, kind=JobKind.PIPELINE.value,
                  state=JobState.PENDING.value, trace_id=trace_id)
        self.db.add(job)
        await self.set_status(project, ProjectStatus.QUEUED, "waiting for a worker")
        await self.db.flush()

        try:
            submitted = await self.queue.submit_pipeline(job_id=job.id, project_id=project_id, trace_id=trace_id)
        except QueueUnavailable as e:
            job.state = JobState.SUBMIT_FAILED.value
            job.error = str(e)
            await self.set_status(project, ProjectStatus.UPLOADING, f"could not queue the job: {e}")
            # Commit the failure before re-raising. The request-scoped session rolls back on
            # any exception, which would erase both the `submit_failed` row and the status
            # reset — leaving the project in `queued` with nothing queued, and no record of
            # why. This is the one place the service must commit on its own: the failure has
            # to outlive the request that failed.
            await self.db.commit()
            m.JOB_STATE.inc(kind="pipeline", state=JobState.SUBMIT_FAILED.value)
            await self._publish(project_id, Stage.FAILED, f"Could not start processing: {e}", job_id=job.id)
            raise

        job.state = JobState.QUEUED.value
        job.task_id = submitted.task_id
        await self.db.flush()
        m.JOB_STATE.inc(kind="pipeline", state=JobState.QUEUED.value)
        await self._publish(project_id, Stage.VALIDATING, f"{usable} clips queued for analysis", job_id=job.id,
                            detail={"clips": usable})
        return job

    async def _publish(self, project_id: str, stage: Stage, message: str, **kw) -> None:
        if self.progress is None:
            return
        await self.progress.publish(ProgressEvent(project_id=project_id, stage=stage, message=message,
                                                  trace_id=correlation().get("trace_id"), **kw))
