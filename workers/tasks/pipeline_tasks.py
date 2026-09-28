"""
MONTA — Pipeline Worker Tasks
===============================
``tasks.run_pipeline`` is the job the API submits after an upload. It owns the
Layer 3–7 run for one project:

    load project + clips (DB)
      → Layer 3 intent, Layer 4 context, Layer 5 Director (which runs Layer 6 then Layer 7)
      → persist intent, story plan, judge verdict, per-clip intelligence
      → publish a real progress event at every stage transition

Design notes:
* The worker is the only process that imports the pipeline, so the API image stays light.
* It opens its own database engine (a Celery worker is not inside the API's request scope)
  and closes it in a ``finally``.
* Progress is published from the stages themselves, so a percentage is only ever
  "clips analyzed / clips total" — never a timer.
* Failure marks the project ``failed`` with the real error text and publishes it, so the UI
  stops waiting. Celery retries only infrastructure errors (broker/DB), never validation.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from celery import Task

from workers.celery_app import app

logger = logging.getLogger("monta.worker.pipeline")

MAX_RETRIES = 2
RETRY_BACKOFF_S = 10


def _settings():
    from app.config import settings

    return settings


async def _run(job_id: str, project_id: str, trace_id: str | None) -> dict:
    """Execute Layers 3–7 for one project. Returns a summary for the Celery result backend."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Clip, ClipStatus, Job, JobState, Project, ProjectStatus
    from app.services.progress import ProgressEvent, Stage, publish_sync
    from orchestration.pipeline import build_pipeline
    from shared.contracts.clip import ClipSource
    from shared.observability.context import bind, new_trace
    from shared.observability.logging import configure_logging

    settings = _settings()
    configure_logging("INFO")
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def publish(stage: Stage, message: str, **kw) -> None:
        publish_sync(settings.REDIS_URL, settings.PROGRESS_CHANNEL_PREFIX, settings.PROGRESS_HISTORY_KEY_PREFIX,
                     settings.PROGRESS_HISTORY_LENGTH,
                     ProgressEvent(project_id=project_id, stage=stage, message=message, job_id=job_id,
                                   trace_id=trace_id, **kw))

    try:
        with new_trace(project_id=project_id), bind(task_id=job_id, agent="pipeline"):
            async with session_maker() as db:
                project = await db.get(Project, project_id)
                if project is None:
                    raise LookupError(f"project {project_id} no longer exists")
                job = await db.get(Job, job_id)
                clips = list((await db.execute(
                    select(Clip).where(Clip.project_id == project_id, Clip.status != ClipStatus.REJECTED.value)
                    .order_by(Clip.created_at)
                )).scalars())
                if not clips:
                    raise ValueError("this project has no usable clips")
                if job is not None:
                    job.state = JobState.RUNNING.value
                    job.stage = Stage.ANALYZING.value
                    job.attempts = (job.attempts or 0) + 1
                    job.started_at = datetime.now(timezone.utc)
                project.status = ProjectStatus.ANALYZING.value
                project.status_detail = f"analyzing {len(clips)} clips"
                prompt = project.raw_prompt or ""
                user_id = project.user_id
                sources = [ClipSource(clip_id=c.id, path=str(_storage_path(settings, c.storage_key))) for c in clips]
                await db.commit()

            publish(Stage.ANALYZING, f"Analyzing {len(sources)} clips", detail={"clips": len(sources)})

            pipeline = build_pipeline()
            analyzed = {"count": 0}
            total = len(sources)

            def on_event(event) -> None:
                """Director task events → real progress. Percent is clips finished / clips total."""
                from shared.contracts.director import TaskStatus

                if event.status != TaskStatus.SUCCEEDED:
                    return
                if event.task_id.startswith("analyze_clip:"):
                    analyzed["count"] += 1
                    publish(Stage.ANALYZING, f"Analyzed {analyzed['count']} of {total} clips",
                            percent=round(100 * analyzed["count"] / total, 1),
                            clip_id=event.task_id.split(":", 1)[1], detail={"analyzed": analyzed["count"], "total": total})
                elif event.task_id == "design_story":
                    publish(Stage.STORY_BUILDING, "Building the story")

            pipeline.director.executor._on_event = on_event
            result = await pipeline.run(project_id=project_id, user_id=user_id, prompt=prompt, clips=sources)

            async with session_maker() as db:
                project = await db.get(Project, project_id)
                job = await db.get(Job, job_id)
                intent = result.intent.model_dump(mode="json")
                project.parsed_intent = intent
                project.context = {"platform": result.context_pack.platform.platform.value,
                                   "clips": len(result.context_pack.clip_intelligence),
                                   "degraded_sources": list(result.context_pack.degraded_sources)}
                for clip_id, intelligence in result.context_pack.clip_intelligence.items():
                    clip = await db.get(Clip, clip_id)
                    if clip is None:
                        continue
                    payload = intelligence.model_dump(mode="json")
                    clip.intelligence = payload
                    clip.status = ClipStatus.ANALYZED.value
                    clip.scene_tags = list(intelligence.visual_tags) or None
                    clip.actions = list(intelligence.activities) or None
                    clip.quality_score = intelligence.quality_score
                    clip.quality_details = {"energy_score": intelligence.energy_score,
                                            "lighting": intelligence.lighting.value,
                                            "camera_motion": intelligence.camera_motion.value}
                    clip.emotion_tags = [intelligence.emotion.value] if intelligence.emotion else None

                failures = [r.task_id for r in result.report.results if r.status.value in ("failed", "skipped")]
                if result.story is None:
                    project.status = ProjectStatus.FAILED.value
                    project.status_detail = f"story design did not complete: {result.report.reasoning}"
                    if job is not None:
                        job.state = JobState.FAILED.value
                        job.error = result.report.reasoning
                        job.finished_at = datetime.now(timezone.utc)
                    await db.commit()
                    publish(Stage.FAILED, project.status_detail, detail={"failed_tasks": failures})
                    return {"project_id": project_id, "status": "failed", "reason": result.report.reasoning}

                story = result.story.model_dump(mode="json")
                judgement = await pipeline.judge_story(result.story, result.context_pack)
                project.story_plan = story
                project.story_judgement = judgement.model_dump(mode="json")
                project.status = ProjectStatus.COMPLETE.value
                project.status_detail = (f"story ready: {result.story.story_pattern}, "
                                         f"{len(result.story.timeline)} cuts, {result.story.total_duration_s:.1f}s")
                if job is not None:
                    job.state = JobState.SUCCEEDED.value
                    job.stage = Stage.COMPLETE.value
                    job.finished_at = datetime.now(timezone.utc)
                    job.result = {"story_pattern": result.story.story_pattern,
                                  "cuts": len(result.story.timeline),
                                  "duration_s": result.story.total_duration_s,
                                  "judge_score": judgement.overall_score,
                                  "degraded_tasks": failures}
                await db.commit()

            publish(Stage.COMPLETE, project.status_detail,
                    percent=100.0,
                    detail={"story_pattern": result.story.story_pattern, "cuts": len(result.story.timeline),
                            "duration_s": result.story.total_duration_s, "judge_score": judgement.overall_score,
                            "validation_passed": result.story.validation.passed})
            logger.info("pipeline complete", extra={"project_id": project_id, "job_id": job_id,
                                                    "pattern": result.story.story_pattern})
            return {"project_id": project_id, "status": "complete", "story_pattern": result.story.story_pattern,
                    "cuts": len(result.story.timeline), "judge_score": judgement.overall_score}
    finally:
        await engine.dispose()


def _storage_path(settings, storage_key: str):
    """Absolute path for a clip's storage key.

    Uses ``settings.storage_root_path``, which anchors a relative ``STORAGE_ROOT`` to the
    repository root. Resolving it against the worker's cwd instead would make the worker look
    in a different directory from the one the API wrote to.
    """
    return (settings.storage_root_path / storage_key).resolve()


async def _mark_failed(project_id: str, job_id: str, message: str, trace_id: str | None) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Job, JobState, Project, ProjectStatus
    from app.services.progress import ProgressEvent, Stage, publish_sync

    settings = _settings()
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            project = await db.get(Project, project_id)
            if project is not None:
                project.status = ProjectStatus.FAILED.value
                project.status_detail = message[:500]
            job = await db.get(Job, job_id)
            if job is not None:
                job.state = JobState.FAILED.value
                job.error = message[:2000]
                job.finished_at = datetime.now(timezone.utc)
            await db.commit()
    finally:
        await engine.dispose()
    publish_sync(settings.REDIS_URL, settings.PROGRESS_CHANNEL_PREFIX, settings.PROGRESS_HISTORY_KEY_PREFIX,
                 settings.PROGRESS_HISTORY_LENGTH,
                 ProgressEvent(project_id=project_id, stage=Stage.FAILED, message=message[:500], job_id=job_id,
                               trace_id=trace_id))


@app.task(bind=True, name="tasks.run_pipeline", max_retries=MAX_RETRIES, acks_late=True)
def run_pipeline(self: Task, job_id: str, project_id: str, trace_id: str | None = None) -> dict:
    """Run Layers 3–7 for a project. Infrastructure errors retry; content errors fail fast."""
    _ensure_paths()
    try:
        return asyncio.run(_run(job_id, project_id, trace_id))
    except (ConnectionError, OSError) as exc:      # broker, database socket, storage volume
        logger.warning("pipeline infrastructure error", extra={"job_id": job_id, "error": str(exc)})
        if self.request.retries >= MAX_RETRIES:
            asyncio.run(_mark_failed(project_id, job_id, f"infrastructure error: {exc}", trace_id))
            raise
        raise self.retry(exc=exc, countdown=RETRY_BACKOFF_S * (self.request.retries + 1))
    except Exception as exc:
        logger.exception("pipeline failed", extra={"job_id": job_id, "project_id": project_id})
        asyncio.run(_mark_failed(project_id, job_id, f"{type(exc).__name__}: {exc}", trace_id))
        raise


def _ensure_paths() -> None:
    """The worker image mounts backend/ and the repo root; make both importable."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for path in (str(root), str(root / "backend")):
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ.setdefault("PYTHONPATH", str(root))


@app.task(name="tasks.sweep_incoming")
def sweep_incoming(older_than_s: int = 3600) -> dict:
    """Periodic cleanup of upload temp files left by interrupted requests."""
    _ensure_paths()
    from app.config import settings
    from services.media_gateway import LocalMediaStorage

    removed = LocalMediaStorage(settings.storage_root_path).sweep_incoming(older_than_s)
    return {"removed": removed}
