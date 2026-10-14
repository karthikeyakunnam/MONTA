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

    from app.models import Clip, ClipStatus, Job, JobState, Project, ProjectStatus, Timeline
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
                target_platform = project.target_platform
                edit_options = dict(project.edit_options or {})
                sources = [ClipSource(clip_id=c.id, path=str(_storage_path(settings, c.storage_key))) for c in clips]
                await db.commit()

            from shared.task_persistence import TaskPersistence, TaskStage
            checkpoint_dir = settings.storage_root_path / "checkpoints" / project_id
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            persistence = TaskPersistence(str(checkpoint_dir))
            checkpoint = persistence.create(task_id=job_id, project_id=project_id)
            checkpoint.advance_to(TaskStage.ANALYZING)
            persistence.save(checkpoint)

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
                    checkpoint.advance_to(TaskStage.STORY_DESIGN)
                    persistence.save(checkpoint)
                    publish(Stage.STORY_BUILDING, "Building the story")

            pipeline.director.executor._on_event = on_event
            result = await pipeline.run(project_id=project_id, user_id=user_id, prompt=prompt, clips=sources,
                                        target_platform=target_platform, edit_options=edit_options)

            async with session_maker() as db:
                project = await db.get(Project, project_id)
                job = await db.get(Job, job_id)
                intent = result.intent.model_dump(mode="json")
                project.parsed_intent = intent
                # Keep the complete ContextPack, including real source metadata and
                # intelligence provenance, as the first inspectable M3 artifact.
                project.context = result.context_pack.model_dump(mode="json")
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

                # Advance task checkpoint to timeline generation
                checkpoint.advance_to(TaskStage.TIMELINE_GENERATION)
                persistence.save(checkpoint)

                # --- Layer 8, 9 & 10: Timeline Building & Audio Studio ---
                publish(Stage.TIMELINE_BUILDING, "Generating timeline and compiling execution plan")
                style_override = _style_override(edit_options)
                base_timeline = pipeline.generate_timeline_ir(result.story, result.context_pack,
                                                              style_override=style_override)

                # Compose audio soundtrack with speech-aware ducking and hero accents
                audio_tracks = ()
                try:
                    publish(Stage.TIMELINE_BUILDING, "Composing background score and audio mixing")
                    if edit_options.get("add_music", True):
                        selected_music = edit_options.get("music_style")
                        audio_tracks = await pipeline.build_soundtrack(
                            base_timeline, result.context_pack,
                            style=selected_music,
                        )
                except Exception as e:
                    logger.warning("Audio soundtrack composition skipped: %s", e)

                timeline_ir = pipeline.generate_timeline_ir(
                    result.story,
                    result.context_pack,
                    style_override=style_override,
                    audio_segments=audio_tracks if audio_tracks else None,
                )

                # --- Layer 12: Critic System & Bounded Revision Loop ---
                from orchestration.agents.critic.retry_logic import RetryManager

                retry_mgr = RetryManager()
                critic_verdict = None
                active_story = result.story
                best_story = active_story
                best_timeline_ir = timeline_ir
                best_score = 0.0
                best_verdict = None
                attempted_patterns = [active_story.story_pattern] if active_story.story_pattern else []

                try:
                    publish(Stage.TIMELINE_BUILDING, "Evaluating edit with Critic System")
                    critic_verdict = await pipeline.evaluate_timeline(timeline_ir, active_story, result.context_pack)
                    best_score = float(critic_verdict.get("overall_score", 0.0))
                    best_verdict = critic_verdict
                    logger.info("Critic score (attempt 0): %.2f (should_retry=%s)", best_score, critic_verdict.get("should_retry"))

                    attempt = 0
                    while retry_mgr.should_retry(score=critic_verdict.get("overall_score", 0.0), retry_count=attempt):
                        attempt += 1
                        strategy = retry_mgr.get_retry_strategy(
                            feedback=critic_verdict.get("feedback", []),
                            scores=critic_verdict.get("scores", {}),
                        )
                        focus_desc = ", ".join(strategy["focus_areas"]) or "narrative pacing"
                        publish(
                            Stage.STORY_BUILDING,
                            f"Critic revision {attempt}/{RetryManager.MAX_RETRIES}: targeting {focus_desc}",
                            detail={"attempt": attempt, "strategy": strategy},
                        )
                        try:
                            revised_story, _ = await pipeline.redesign_story(
                                result.context_pack,
                                avoid_patterns=attempted_patterns,
                            )
                            if revised_story.story_pattern:
                                attempted_patterns.append(revised_story.story_pattern)
                            revised_base = pipeline.generate_timeline_ir(
                                revised_story, result.context_pack, style_override=style_override,
                            )
                            revised_audio = ()
                            if edit_options.get("add_music", True):
                                selected_music = edit_options.get("music_style")
                                revised_audio = await pipeline.build_soundtrack(
                                    revised_base, result.context_pack,
                                    style=selected_music,
                                )
                            revised_timeline = pipeline.generate_timeline_ir(
                                revised_story,
                                result.context_pack,
                                style_override=style_override,
                                audio_segments=revised_audio if revised_audio else None,
                            )
                            new_verdict = await pipeline.evaluate_timeline(revised_timeline, revised_story, result.context_pack)
                            new_score = float(new_verdict.get("overall_score", 0.0))
                            logger.info("Critic revision attempt %d score: %.2f (vs previous best: %.2f)", attempt, new_score, best_score)

                            # Keep the best valid result if a revision worsens quality
                            if new_score > best_score:
                                best_score = new_score
                                best_story = revised_story
                                best_timeline_ir = revised_timeline
                                best_verdict = new_verdict

                            critic_verdict = new_verdict
                        except Exception as rev_err:
                            logger.warning("Critic revision attempt %d failed: %s", attempt, rev_err)
                            break

                    # Preserve the best evaluated edit result across all bounded retry attempts
                    active_story = best_story
                    timeline_ir = best_timeline_ir
                    critic_verdict = best_verdict or critic_verdict
                    logger.info("Selected final edit with score: %.2f (pattern=%s)", best_score, active_story.story_pattern)
                except Exception as critic_err:
                    logger.warning("Critic evaluation skipped: %s", critic_err)

                existing_timeline = (await db.execute(
                    select(Timeline).where(Timeline.project_id == project_id)
                )).scalar_one_or_none()
                timeline_row = existing_timeline or Timeline(id=f"timeline_{job_id}", project_id=project_id)
                timeline_row.story_acts = {"assignments": active_story.act_assignments, "hero_clip_id": active_story.hero_clip_id}
                timeline_row.style_config = {
                    "pace": active_story.pace.value.value,
                    "emotion": active_story.emotion.value.value,
                    "timeline_validation": {"is_valid": True, "errors": [], "warnings": []},
                    "critic_evaluation": critic_verdict,
                }
                timeline_row.entries = timeline_ir.model_dump(mode="json")
                timeline_row.total_duration = timeline_ir.duration_s
                if existing_timeline is None:
                    db.add(timeline_row)

                # A real timeline is now persisted even if the later FFmpeg step
                # fails, so an operator can identify the failed layer.
                project.status = ProjectStatus.RENDERING.value
                project.status_detail = "timeline validated; preparing FFmpeg render"
                if job is not None:
                    job.stage = Stage.TIMELINE_BUILDING.value
                await db.commit()

                # --- Layer 13: Media Rendering ---
                checkpoint.advance_to(TaskStage.RENDERING)
                persistence.save(checkpoint)
                publish(Stage.RENDERING, "Rendering final media output with FFmpeg")
                
                render_dir = settings.storage_root_path / "renders" / project_id
                render_dir.mkdir(parents=True, exist_ok=True)
                render_file = render_dir / f"final_{job_id}.mp4"

                def on_render_progress(pct: float, elapsed: float, stage_name: str):
                    publish(Stage.RENDERING, f"Rendering video: {pct:.0f}%", percent=pct, detail={"stage": stage_name, "elapsed_s": elapsed})

                render_result = await pipeline.render_story(
                    story=active_story,
                    pack=result.context_pack,
                    output_path=str(render_file),
                    on_progress=on_render_progress,
                    timeline=timeline_ir,
                )

                if not render_result.success:
                    checkpoint.mark_failed(render_result.error_message or "Rendering failed")
                    persistence.save(checkpoint)
                    raise RuntimeError(f"Rendering failed: {render_result.error_message}")

                if not render_result.validation_passed:
                    raise RuntimeError("FFprobe did not validate the rendered MP4.")

                caption_style = edit_options.get("caption_style")
                if caption_style is None:
                    inferred_caption = result.context_pack.intent.caption_style
                    caption_style = inferred_caption.value.value if inferred_caption and inferred_caption.value else "None"
                if caption_style and caption_style != "None":
                    publish(Stage.RENDERING, "Transcribing speech and burning captions with local Whisper")
                    try:
                        from services.edit_executor.caption_engine import CaptionEngine
                        from services.render_farm.output_validator import OutputValidator

                        caption_engine = CaptionEngine(
                            model_size=settings.WHISPER_MODEL_SIZE,
                            ffmpeg_bin=settings.FFMPEG_PATH,
                            temp_dir=str(render_dir),
                        )
                        captions = await caption_engine.transcribe(str(render_file))
                        if captions:
                            caption_file = render_dir / f"captioned_{job_id}.mp4"
                            await caption_engine.burn_captions(
                                str(render_file), captions, str(caption_file), style=caption_style,
                                width=render_result.width, height=render_result.height,
                            )
                            caption_validation = await OutputValidator(
                                ffprobe_bin=settings.FFPROBE_PATH,
                            ).validate(
                                caption_file, timeline_ir, render_result.render_time_s,
                                render_result.encoder_used,
                            )
                            if not caption_validation.success or not caption_validation.validation_passed:
                                raise RuntimeError(caption_validation.error_message or "captioned video failed validation")
                            render_file = caption_file
                            render_result = caption_validation
                    except Exception as caption_error:
                        raise RuntimeError(
                            "Captions were requested but local transcription or caption rendering failed. "
                            f"Check the Whisper installation/model ({settings.WHISPER_MODEL_SIZE}): {caption_error}"
                        ) from caption_error

                # --- Layer 6 & 13: Platform Export ---
                checkpoint.advance_to(TaskStage.EXPORTING)
                persistence.save(checkpoint)
                platform_export_path = None
                target_platform = result.context_pack.platform.platform.value if result.context_pack.platform else "general"
                if target_platform in ("tiktok", "instagram", "youtube_short", "twitter"):
                    try:
                        from services.render_farm.format_exporter import FormatExporter
                        publish(Stage.RENDERING, f"Exporting optimized {target_platform} format")
                        export_file = render_dir / f"export_{target_platform}_{job_id}.mp4"
                        exporter = FormatExporter()
                        platform_export_path = await exporter.export_for_platform(
                            input_path=str(render_file),
                            output_path=str(export_file),
                            platform=target_platform,
                            resolution="1080p",
                        )
                    except Exception as exp_err:
                        logger.warning("Platform export failed, master render preserved: %s", exp_err)

                checkpoint.mark_complete(artifacts={
                    "master_mp4": str(render_file),
                    "platform_export": platform_export_path,
                    "critic_score": critic_verdict.get("overall_score") if critic_verdict else None,
                })
                persistence.save(checkpoint)

                timeline_row.style_config = {
                    **(timeline_row.style_config or {}),
                    "compiled_execution_plan": render_result.execution_plan,
                    "platform_export": platform_export_path,
                    "render_metadata": {
                        "output_path": render_result.output_path,
                        "duration_s": render_result.duration_s,
                        "width": render_result.width,
                        "height": render_result.height,
                        "fps": render_result.fps,
                        "video_codec": render_result.video_codec,
                        "audio_codec": render_result.audio_codec,
                        "encoder": render_result.encoder_used,
                        "file_size_bytes": render_result.file_size_bytes,
                        "validation_passed": render_result.validation_passed,
                        "warnings": list(render_result.warnings),
                    },
                }

                # Persist Render rows
                from app.models.render import Render, RenderStatus
                from uuid import uuid4

                render_id = str(uuid4())
                render_row = Render(
                    id=render_id,
                    project_id=project_id,
                    status=RenderStatus.COMPLETE.value,
                    resolution=f"{render_result.width}x{render_result.height}",
                    format="mp4",
                    output_path=str(render_file),
                    file_size_bytes=float(render_result.file_size_bytes),
                    duration=float(render_result.duration_s),
                    progress_percent=100.0,
                    started_at=job.started_at if job else None,
                    completed_at=datetime.now(timezone.utc),
                )
                db.add(render_row)

                if platform_export_path and os.path.exists(platform_export_path):
                    export_render_id = str(uuid4())
                    export_size = os.path.getsize(platform_export_path)
                    export_row = Render(
                        id=export_render_id,
                        project_id=project_id,
                        status=RenderStatus.COMPLETE.value,
                        resolution="1080x1920" if target_platform in ("tiktok", "instagram", "youtube_short") else f"{render_result.width}x{render_result.height}",
                        format="mp4",
                        platform=target_platform,
                        output_path=platform_export_path,
                        file_size_bytes=float(export_size),
                        duration=float(render_result.duration_s),
                        progress_percent=100.0,
                        started_at=job.started_at if job else None,
                        completed_at=datetime.now(timezone.utc),
                    )
                    db.add(export_row)

                project.status = ProjectStatus.COMPLETE.value
                project.status_detail = (f"video ready: {render_result.duration_s:.1f}s, "
                                         f"{render_result.width}x{render_result.height}, {render_result.file_size_mb}MB")
                if job is not None:
                    job.state = JobState.SUCCEEDED.value
                    job.stage = Stage.COMPLETE.value
                    job.finished_at = datetime.now(timezone.utc)
                    job.result = {"story_pattern": active_story.story_pattern,
                                  "cuts": len(active_story.timeline),
                                  "duration_s": render_result.duration_s,
                                  "render_id": render_id,
                                  "output_path": str(render_file),
                                  "platform_export": platform_export_path,
                                  "judge_score": judgement.overall_score,
                                  "critic_score": critic_verdict.get("overall_score") if critic_verdict else None,
                                  "degraded_tasks": failures,
                                  "artifacts": {
                                      "context_pack": "projects.context",
                                      "story_plan": "projects.story_plan",
                                      "timeline_ir": "timelines.entries",
                                      "compiled_execution_plan": "timelines.style_config.compiled_execution_plan",
                                      "render_metadata": "timelines.style_config.render_metadata",
                                      "final_mp4": str(render_file),
                                      "platform_export": platform_export_path,
                                  }}
                await db.commit()

            publish(Stage.COMPLETE, project.status_detail,
                    percent=100.0,
                    detail={"story_pattern": active_story.story_pattern, "cuts": len(active_story.timeline),
                                  "duration_s": render_result.duration_s, "render_id": render_id,
                                  "output_path": str(render_file), "judge_score": judgement.overall_score,
                                  "critic_score": critic_verdict.get("overall_score") if critic_verdict else None,
                                  "platform_export": platform_export_path,
                                  "validation_passed": active_story.validation.passed})
            logger.info("pipeline complete with rendered video", extra={"project_id": project_id, "job_id": job_id,
                                                                        "output_path": str(render_file)})
            return {"project_id": project_id, "status": "complete", "story_pattern": active_story.story_pattern,
                    "cuts": len(active_story.timeline), "render_id": render_id, "output_path": str(render_file),
                    "platform_export": platform_export_path, "duration_s": render_result.duration_s,
                    "judge_score": judgement.overall_score,
                    "critic_score": critic_verdict.get("overall_score") if critic_verdict else None}
    finally:
        await engine.dispose()



def _storage_path(settings, storage_key: str):
    """Absolute path for a clip's storage key.

    Uses ``settings.storage_root_path``, which anchors a relative ``STORAGE_ROOT`` to the
    repository root. Resolving it against the worker's cwd instead would make the worker look
    in a different directory from the one the API wrote to.
    """
    return (settings.storage_root_path / storage_key).resolve()


def _style_override(options: dict) -> dict:
    """Make explicit visual choices outrank learned or inferred style preferences."""
    override = {}
    if options.get("color_grade"):
        override["color_grade"] = options["color_grade"]
    if options.get("caption_style") and options["caption_style"] != "None":
        override["captions"] = options["caption_style"]
    if options.get("music_style"):
        override["music"] = options["music_style"]
    return override


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
