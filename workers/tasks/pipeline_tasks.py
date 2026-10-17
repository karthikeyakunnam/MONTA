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
import re
from datetime import datetime, timezone

from celery import Task

from workers.celery_app import app

logger = logging.getLogger("monta.worker.pipeline")

MAX_RETRIES = 2
RETRY_BACKOFF_S = 10
DURATION_TOLERANCE_ABS_S = 0.5
DURATION_TOLERANCE_RELATIVE = 0.05


def _duration_limit_notice(requested_s: float | None, planned_s: float, rendered_s: float,
                           platform_max_s: float | None = None) -> str | None:
    """Surface material target shortfalls; transition overlap alone is within tolerance.

    MONTA accepts the larger of 0.5s or 5% duration deviation. That matches the
    output validator and prevents normal xfade overlap from being reported as a
    source limitation.
    """
    if requested_s is None or requested_s <= 0:
        return None
    tolerance = max(DURATION_TOLERANCE_ABS_S, requested_s * DURATION_TOLERANCE_RELATIVE)
    if requested_s - planned_s <= tolerance:
        return None
    platform_limited = (platform_max_s is not None and requested_s > platform_max_s + tolerance
                        and abs(planned_s - platform_max_s) <= max(DURATION_TOLERANCE_ABS_S,
                                                                   platform_max_s * DURATION_TOLERANCE_RELATIVE))
    cause = f"the platform limit ({platform_max_s:.1f}s)" if platform_limited else "available footage"
    return (f"Requested {requested_s:.1f}s; {cause} limited the plan to {planned_s:.1f}s "
            f"({rendered_s:.1f}s rendered after transitions).")


def _settings():
    from app.config import settings

    return settings


def _select_auto_sources(clips, edit_options: dict):
    """Avoid nesting prior MONTA exports in a fresh automatic edit when originals exist."""
    if edit_options.get("manual_timeline") or edit_options.get("include_prior_exports"):
        return list(clips)
    originals = [clip for clip in clips if not re.fullmatch(
        r"monta_edit_proj_[0-9a-f]{12}\.mp4", clip.original_filename or "", re.IGNORECASE)]
    return originals or list(clips)


async def _run(job_id: str, project_id: str, trace_id: str | None) -> dict:
    """Execute Layers 3–7 for one project. Returns a summary for the Celery result backend."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Clip, ClipStatus, Job, JobState, Project, ProjectStatus, Timeline
    from app.services.progress import ProgressEvent, Stage, publish_sync
    from orchestration.pipeline import build_pipeline
    from shared.contracts.clip import ClipSource
    from shared.contracts.context import ContextPack
    from shared.contracts.base import revalidate
    from shared.contracts.intent import IntentAnalysis
    from shared.contracts.story import StoryPlan
    from services.prompt_engine.engine import is_incremental_revision
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
                if job is None:
                    raise LookupError(f"job {job_id} no longer exists")
                if job.state in (JobState.CANCELLED.value, JobState.SUCCEEDED.value, JobState.FAILED.value):
                    return {"project_id": project_id, "job_id": job_id, "status": job.state,
                            "detail": "terminal job ignored by worker"}
                if job.prompt_version is not None and int(job.prompt_version) != int(project.prompt_version or 1):
                    job.state = JobState.CANCELLED.value
                    job.error = "Superseded by a newer project prompt version before worker start."
                    job.finished_at = datetime.now(timezone.utc)
                    await db.commit()
                    return {"project_id": project_id, "job_id": job_id, "status": "stale"}
                prior_judgement = project.story_judgement if isinstance(project.story_judgement, dict) else {}
                prior_current_render_id = prior_judgement.get("current_render_id")
                clips = list((await db.execute(
                    select(Clip).where(Clip.project_id == project_id, Clip.status != ClipStatus.REJECTED.value)
                    .order_by(Clip.created_at)
                )).scalars())
                if not clips:
                    raise ValueError("this project has no usable clips")
                job.state = JobState.RUNNING.value
                job.stage = Stage.ANALYZING.value
                job.attempts = (job.attempts or 0) + 1
                job.started_at = datetime.now(timezone.utc)
                project.status = ProjectStatus.ANALYZING.value
                prompt = job.prompt_snapshot if job.prompt_snapshot is not None else (project.raw_prompt or "")
                user_id = project.user_id
                target_platform = job.target_platform_snapshot or project.target_platform
                edit_options = dict(job.edit_options_snapshot or project.edit_options or {})
                prompt_version = int(job.prompt_version or project.prompt_version or 1)
                analysis_clips = _select_auto_sources(clips, edit_options)
                excluded_exports = [c.id for c in clips if c not in analysis_clips]
                project.status_detail = f"analyzing {len(analysis_clips)} clips"
                sources = [ClipSource(clip_id=c.id, path=str(_storage_path(settings, c.storage_key)))
                           for c in analysis_clips]

                cached_context = None
                cache_reused = False
                try:
                    candidate = ContextPack.model_validate(project.context) if project.context else None
                    if candidate and {meta.clip_id for meta in candidate.video_metadata} == {c.id for c in analysis_clips}:
                        from orchestration.agents.intelligence.media import fingerprint_file
                        metadata = {meta.clip_id: meta for meta in candidate.video_metadata}
                        cache_reused = all(
                            metadata[c.id].fingerprint == fingerprint_file(str(_storage_path(settings, c.storage_key)))
                            for c in analysis_clips
                        )
                        if cache_reused:
                            cached_context = candidate
                except Exception as cache_error:
                    logger.info("clip-analysis cache invalidated", extra={"project_id": project_id,
                                                                          "reason": str(cache_error)[:240]})

                previous_success = (await db.execute(
                    select(Job).where(Job.project_id == project_id, Job.state == JobState.SUCCEEDED.value,
                                      Job.id != job_id)
                    .order_by(Job.prompt_version.desc(), Job.created_at.desc(), Job.id.desc()).limit(1)
                )).scalar_one_or_none()
                revision_mode = "initial"
                revision_base_job_id = None
                revision_base_intent = None
                if previous_success:
                    revision_base_job_id = previous_success.id
                    previous_prompt = previous_success.prompt_snapshot or ""
                    if previous_prompt == prompt:
                        revision_mode = "same_prompt"
                    elif is_incremental_revision(previous_prompt, prompt):
                        revision_mode = "incremental"
                        prior_result = previous_success.result if isinstance(previous_success.result, dict) else {}
                        prior_intent = prior_result.get("intent_snapshot")
                        if prior_intent:
                            try:
                                revision_base_intent = IntentAnalysis.model_validate(prior_intent)
                            except Exception as intent_error:
                                logger.warning("prior revision intent unavailable for merge: %s", intent_error)
                                revision_mode = "incremental_without_base"
                    else:
                        revision_mode = "replacement"
                reuse_story = None
                if previous_success and isinstance(previous_success.result, dict):
                    same_story_inputs = (
                        previous_success.prompt_snapshot == prompt
                        and previous_success.target_platform_snapshot == target_platform
                        and float((previous_success.edit_options_snapshot or {}).get("target_duration_s") or 0)
                        == float(edit_options.get("target_duration_s") or 0)
                        and (previous_success.edit_options_snapshot or {}).get("pace")
                        == edit_options.get("pace")
                    )
                    story_data = previous_success.result.get("story_plan_snapshot")
                    if same_story_inputs and story_data and cache_reused:
                        try:
                            reuse_story = StoryPlan.model_validate(story_data)
                        except Exception:
                            reuse_story = None
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
            crop_probe_failures: list[str] = []
            vision_reanalysis_ids: list[str] = []
            reused_clip_analysis_ids = set(cached_context.clip_intelligence) if cached_context else set()

            # Old ContextPacks predate stable active-picture detection. Refresh only
            # that inexpensive technical metadata while retaining fingerprint-valid
            # semantic analysis; a completed scan with no crop is cached explicitly.
            if cached_context:
                old_metadata = {meta.clip_id: meta for meta in cached_context.video_metadata}
                crop_sources = [source for source in sources
                                if not old_metadata[source.clip_id].content_crop_analyzed]
                if crop_sources:
                    refreshed, probe_failures = await pipeline.probe(crop_sources)
                    refreshed_by_id = {meta.clip_id: meta for meta in refreshed}
                    source_by_id = {source.clip_id: source for source in sources}
                    crop_probe_failures = [failure.clip_id for failure in probe_failures]
                    metadata = tuple(
                        refreshed_by_id.get(meta.clip_id, meta).model_copy(
                            update={"path": source_by_id[meta.clip_id].path}
                        ) for meta in cached_context.video_metadata
                    )
                    cached_context = cached_context.model_copy(update={"video_metadata": metadata})

            # A fingerprint proves the uploaded bytes are unchanged, but it cannot
            # make signal-only legacy analysis equivalent to newly available vision.
            # Re-analyze just those clips when the real local vision stack is ready.
            if cached_context and pipeline.director.planner.vision_enabled:
                vision_reanalysis_ids = [clip_id for clip_id, intelligence in cached_context.clip_intelligence.items()
                                         if not intelligence.provenance.vision_model]
                if vision_reanalysis_ids:
                    retained = {clip_id: intelligence for clip_id, intelligence
                                in cached_context.clip_intelligence.items()
                                if clip_id not in set(vision_reanalysis_ids)}
                    cached_context = cached_context.model_copy(update={"clip_intelligence": retained})
                    reuse_story = None
            reused_clip_analysis_ids = set(cached_context.clip_intelligence) if cached_context else set()
            cache_reused = bool(reused_clip_analysis_ids)

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
            if (edit_options.get("manual_timeline") and cached_context and reuse_story
                    and previous_success and previous_success.prompt_snapshot == prompt
                    and previous_success.target_platform_snapshot == target_platform):
                publish(Stage.STORY_BUILDING, "Reusing analyzed footage for manual timeline edit")
                result = pipeline.manual_revision_from_cached(
                    cached_context, reuse_story, sources, target_platform, edit_options,
                )
            else:
                result = await pipeline.run(project_id=project_id, user_id=user_id, prompt=prompt, clips=sources,
                                            target_platform=target_platform, edit_options=edit_options,
                                            cached_context=cached_context, reuse_story_plan=reuse_story,
                                            revision_base_intent=revision_base_intent,
                                            manual_timeline_override=bool(edit_options.get("manual_timeline")))

            async with session_maker() as db:
                project = await db.get(Project, project_id)
                job = await db.get(Job, job_id)
                if (job is None or job.state != JobState.RUNNING.value or
                        (job.prompt_version is not None and int(job.prompt_version) != int(project.prompt_version or 1))):
                    return {"project_id": project_id, "job_id": job_id, "status": "stale",
                            "detail": "revision changed before pipeline results were persisted"}
                intent = result.intent.model_dump(mode="json")
                merge_note = next((note for note in result.intent.revisions
                                   if "targeted revision merged" in note), "")
                inherited_requirements = []
                if merge_note:
                    inherited_text = merge_note.partition("inherited unchanged requirements:")[2]
                    inherited_requirements = [item.strip() for item in inherited_text.split(",")
                                              if item.strip() and item.strip() != "none"]
                job.result = {
                    "prompt_version": prompt_version,
                    "revision_mode": revision_mode,
                    "revision_base_job_id": revision_base_job_id,
                    "inherited_requirements": inherited_requirements,
                    "intent_snapshot": intent,
                    "story_plan_snapshot": result.story.model_dump(mode="json") if result.story else None,
                    "input_sha256": {c.id: c.sha256 for c in clips},
                    "reused_clip_analysis": cache_reused,
                    "reused_clip_analysis_ids": sorted(reused_clip_analysis_ids),
                    "vision_reanalysis_ids": sorted(vision_reanalysis_ids),
                    "crop_probe_failures": crop_probe_failures,
                    "reused_story_plan": reuse_story is not None,
                }
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

                failures = [r.task_id for r in result.report.results if r.status.value == "failed"]
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
                project.story_judgement = {
                    **judgement.model_dump(mode="json"),
                    **({"current_render_id": prior_current_render_id} if prior_current_render_id else {}),
                }

                # Advance task checkpoint to timeline generation
                checkpoint.advance_to(TaskStage.TIMELINE_GENERATION)
                persistence.save(checkpoint)

                # --- Layer 8, 9 & 10: Timeline Building & Audio Studio ---
                publish(Stage.TIMELINE_BUILDING, "Generating timeline and compiling execution plan")
                style_override = _style_override(edit_options)
                selected_music = _resolved_music_style(result.context_pack, style_override)
                segment_crops = await pipeline.detect_segment_crops(result.story, result.context_pack)
                base_timeline = pipeline.generate_timeline_ir(result.story, result.context_pack,
                                                              style_override=style_override,
                                                              segment_crops=segment_crops)

                # Compose audio soundtrack with speech-aware ducking and hero accents
                audio_tracks = ()
                try:
                    publish(Stage.TIMELINE_BUILDING, "Composing background score and audio mixing")
                    if edit_options.get("add_music", True):
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
                        applied_focus = strategy.get("implemented_focus_areas", [])
                        deferred_focus = strategy.get("deferred_focus_areas", [])
                        if not applied_focus:
                            publish(
                                Stage.TIMELINE_BUILDING,
                                "Critic found no weak story or pacing dimension that this stage can revise",
                                detail={"attempt": attempt, "strategy": strategy},
                            )
                            break
                        focus_desc = ", ".join(applied_focus)
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
                            revised_crops = await pipeline.detect_segment_crops(
                                revised_story, result.context_pack,
                            )
                            revised_base = pipeline.generate_timeline_ir(
                                revised_story, result.context_pack, style_override=style_override,
                                segment_crops=revised_crops,
                            )
                            revised_audio = ()
                            if edit_options.get("add_music", True):
                                revised_audio = await pipeline.build_soundtrack(
                                    revised_base, result.context_pack,
                                    style=selected_music,
                                )
                            revised_timeline = pipeline.generate_timeline_ir(
                                revised_story,
                                result.context_pack,
                                style_override=style_override,
                                audio_segments=revised_audio if revised_audio else None,
                                segment_crops=revised_crops,
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

                if edit_options.get("manual_timeline"):
                    from services.timeline_generator.manual import ManualTimeline, apply_manual_timeline
                    metadata = {item.clip_id: item for item in result.context_pack.video_metadata}
                    manual_edit = ManualTimeline.model_validate(edit_options["manual_timeline"])
                    window_crops = {}
                    for index, shot in enumerate(manual_edit.shots):
                        meta = metadata.get(shot.clip_id)
                        if meta is not None:
                            window_crops[index] = meta.content_crop or await pipeline.media.detect_content_crop_for_window(
                                meta, shot.source_in_ms / 1000, shot.source_out_ms / 1000,
                            )
                    timeline_ir = apply_manual_timeline(
                        timeline_ir,
                        manual_edit,
                        {clip_id: round(item.duration_s * 1000) for clip_id, item in metadata.items()},
                        {clip_id: (item.path, item.has_audio, item.content_crop)
                         for clip_id, item in metadata.items()},
                        window_crops=window_crops,
                    )

                render_dir = settings.storage_root_path / "renders" / project_id
                render_dir.mkdir(parents=True, exist_ok=True)
                render_brief = _refresh_prompt_text_styles(result.context_pack.intent.creative_brief, prompt)
                overlays, grain_strength, unavailable_effects = _materialize_prompt_visuals(
                    render_brief,
                    timeline_ir.total_duration_ms,
                    render_dir,
                    job_id,
                    timeline_ir.width,
                    timeline_ir.height,
                    title_position=edit_options.get("title_position"),
                )
                timeline_ir = revalidate(
                    timeline_ir,
                    text_overlays=overlays,
                    grain_strength=grain_strength,
                )

                existing_timeline = (await db.execute(
                    select(Timeline).where(Timeline.project_id == project_id)
                )).scalar_one_or_none()
                timeline_row = existing_timeline or Timeline(id=f"timeline_{job_id}", project_id=project_id)
                timeline_row.story_acts = {"assignments": active_story.act_assignments, "hero_clip_id": active_story.hero_clip_id}
                timeline_row.style_config = {
                    "job_id": job_id,
                    "prompt_version": job.prompt_version if job is not None else project.prompt_version,
                    "edit_options": dict(edit_options),
                    "pace": active_story.pace.value.value,
                    "emotion": active_story.emotion.value.value,
                    "creative_brief": render_brief.model_dump(mode="json"),
                    "rendered_text_overlays": [overlay.model_dump(mode="json") for overlay in overlays],
                    "grain_strength": grain_strength,
                    "manual_timeline_applied": bool(edit_options.get("manual_timeline")),
                    "excluded_prior_exports": excluded_exports,
                    "shot_decisions": [
                        {"clip_id": cut.clip_id, "reason": cut.reasoning,
                         "origin": "ai_inference"}
                        for cut in active_story.timeline
                    ] if not edit_options.get("manual_timeline") else [
                        {"clip_id": segment.clip_id, "reason": "User selected or adjusted this shot.",
                         "origin": "user_override"}
                        for segment in timeline_ir.video_segments
                    ],
                    "unsupported_effects": unavailable_effects,
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
                manual_captions = (edit_options.get("manual_timeline") or {}).get("captions")
                if manual_captions and caption_style == "None":
                    raise ValueError("Manual captions require a caption style; select one in Revise edit")
                if manual_captions is not None or (caption_style and caption_style != "None"):
                    timeline_row.style_config = {
                        **(timeline_row.style_config or {}),
                        "caption_state": "requested",
                    }
                    await db.commit()
                    publish(Stage.RENDERING, "Burning edited captions" if manual_captions is not None
                            else "Transcribing speech and burning captions with local Whisper")
                    try:
                        from services.edit_executor.caption_engine import CaptionEngine
                        from services.render_farm.output_validator import OutputValidator

                        caption_engine = CaptionEngine(
                            model_size=settings.WHISPER_MODEL_SIZE,
                            ffmpeg_bin=settings.FFMPEG_PATH,
                            temp_dir=str(render_dir),
                        )
                        captions = manual_captions if manual_captions is not None else await caption_engine.transcribe(str(render_file))
                        timeline_row.style_config = {
                            **(timeline_row.style_config or {}),
                            "caption_segments": [
                                {"start": segment["start"], "end": segment["end"], "text": segment["text"]}
                                for segment in captions
                            ],
                            "caption_state": ("user_edited" if captions else "user_removed")
                            if manual_captions is not None else ("transcribed" if captions else "no_speech_detected"),
                        }
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
                        timeline_row.style_config = {
                            **(timeline_row.style_config or {}),
                            "caption_state": "failed",
                            "caption_error": str(caption_error)[:500],
                        }
                        await db.commit()
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
                        from services.render_farm.output_validator import OutputValidator
                        publish(Stage.RENDERING, f"Exporting optimized {target_platform} format")
                        export_file = render_dir / f"export_{target_platform}_{job_id}.mp4"
                        exporter = FormatExporter()
                        platform_export_path = await exporter.export_for_platform(
                            input_path=str(render_file),
                            output_path=str(export_file),
                            platform=target_platform,
                            resolution="1080p",
                        )
                        export_validation = await OutputValidator(ffprobe_bin=settings.FFPROBE_PATH).validate(
                            platform_export_path, timeline_ir, render_result.render_time_s, render_result.encoder_used,
                        )
                        if not export_validation.success or not export_validation.validation_passed:
                            raise RuntimeError(export_validation.error_message or "platform export failed validation")
                    except Exception as exp_err:
                        logger.warning("Platform export failed validation: %s", exp_err)
                        raise RuntimeError(f"The {target_platform} export could not be validated: {exp_err}") from exp_err

                # Re-read durable state after the long FFmpeg phase. A cancelled or
                # superseded task may leave an unreferenced file, but can never publish
                # it as the project's current result.
                await db.refresh(job)
                await db.refresh(project)
                if (job.state != JobState.RUNNING.value or
                        (job.prompt_version is not None and int(job.prompt_version) != int(project.prompt_version or 1))):
                    return {"project_id": project_id, "job_id": job_id, "status": "stale",
                            "detail": "job was cancelled or superseded before render commit"}

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
                        "audio_duration_s": render_result.audio_duration_s,
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
                    job_id=job_id,
                    status=RenderStatus.COMPLETE.value,
                    resolution=f"{render_result.width}x{render_result.height}",
                    format="mp4",
                    output_path=str(render_file),
                    file_size_bytes=float(render_result.file_size_bytes),
                    duration=float(render_result.duration_s),
                    progress_percent=100.0,
                    started_at=job.started_at if job else None,
                    completed_at=datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(render_row)

                # Compare critic scores only when every score input is identical.
                # A changed prompt or option set has no comparable baseline.
                from app.models.render import RenderStatus
                from app.services.storage_paths import resolve_stored_mp4

                prior_render = await db.get(Render, prior_current_render_id) if prior_current_render_id else None
                if prior_render is None and previous_success and isinstance(previous_success.result, dict):
                    previous_render_id = previous_success.result.get("render_id")
                    prior_render = await db.get(Render, previous_render_id) if previous_render_id else None
                prior_job = await db.get(Job, prior_render.job_id) if prior_render and prior_render.job_id else None
                prior_result = prior_job.result if prior_job and isinstance(prior_job.result, dict) else {}
                prior_critic_score = prior_result.get("critic_score")
                current_critic_score = critic_verdict.get("overall_score") if critic_verdict else None
                current_sha = {clip.id: clip.sha256 for clip in clips}
                prior_sha = prior_result.get("input_sha256")
                same_evaluation_inputs = bool(
                    prior_job and prior_sha == current_sha
                    and prior_job.prompt_snapshot == prompt
                    and prior_job.target_platform_snapshot == target_platform
                    and (prior_job.edit_options_snapshot or {}) == edit_options
                )
                prior_render_accessible = False
                if prior_render and prior_render.status == RenderStatus.COMPLETE.value and prior_render.output_path:
                    try:
                        prior_path = resolve_stored_mp4(prior_render.output_path, settings.storage_root_path)
                        prior_render_accessible = prior_path.is_file() and prior_path.stat().st_size >= 1024
                    except (OSError, ValueError):
                        prior_render_accessible = False
                retain_prior_render = bool(
                    same_evaluation_inputs and prior_render_accessible
                    and isinstance(prior_critic_score, (int, float))
                    and isinstance(current_critic_score, (int, float))
                    and float(current_critic_score) < float(prior_critic_score)
                )
                chosen_current_render_id = prior_render.id if retain_prior_render else render_id

                if platform_export_path and os.path.exists(platform_export_path):
                    export_render_id = str(uuid4())
                    export_size = os.path.getsize(platform_export_path)
                    export_row = Render(
                        id=export_render_id,
                        project_id=project_id,
                        job_id=job_id,
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
                        created_at=datetime.now(timezone.utc),
                    )
                    db.add(export_row)

                project.status = ProjectStatus.COMPLETE.value
                project.status_detail = (f"video ready: {render_result.duration_s:.1f}s, "
                                         f"{render_result.width}x{render_result.height}, {render_result.file_size_mb}MB")
                requested_duration_s = active_story.requested_duration_s
                duration_notice = _duration_limit_notice(
                    requested_duration_s,
                    timeline_ir.duration_s,
                    render_result.duration_s,
                    result.context_pack.platform.max_duration_s if result.context_pack.platform else None,
                )
                if duration_notice:
                    project.status_detail += f" · {duration_notice}"
                if retain_prior_render:
                    project.status_detail += (
                        f" · Previous render retained: critic score {float(current_critic_score):.2f} "
                        f"was below {float(prior_critic_score):.2f} for the same prompt and options."
                    )
                project.story_judgement = {
                    **(project.story_judgement or {}),
                    "current_render_id": chosen_current_render_id,
                }
                if not render_file.is_file() or render_file.stat().st_size < 1024:
                    raise RuntimeError("The validated MP4 disappeared or became inaccessible before completion.")
                if job is not None:
                    job.state = JobState.SUCCEEDED.value
                    job.stage = Stage.COMPLETE.value
                    job.finished_at = datetime.now(timezone.utc)
                    job.result = {**(job.result or {}),
                                  "story_pattern": active_story.story_pattern,
                                  "cuts": len(active_story.timeline),
                                  "duration_s": render_result.duration_s,
                                  "audio_duration_s": render_result.audio_duration_s,
                                  "requested_duration_s": requested_duration_s,
                                  "planned_duration_s": timeline_ir.duration_s,
                                  "expected_render_duration_s": timeline_ir.render_duration_s,
                                  "duration_tolerance_s": (max(DURATION_TOLERANCE_ABS_S,
                                                                requested_duration_s * DURATION_TOLERANCE_RELATIVE)
                                                            if requested_duration_s is not None else None),
                                  "duration_limited": duration_notice is not None,
                                  "duration_notice": duration_notice,
                                  "transition_overlap_s": timeline_ir.duration_s - timeline_ir.render_duration_s,
                                  "render_id": render_id,
                                  "output_path": str(render_file),
                                  "platform_export": platform_export_path,
                                  "judge_score": judgement.overall_score,
                                  "critic_score": critic_verdict.get("overall_score") if critic_verdict else None,
                                  "previous_critic_score": prior_critic_score if same_evaluation_inputs else None,
                                  "render_promoted": not retain_prior_render,
                                  "retained_render_id": prior_render.id if retain_prior_render else None,
                                  "reused_clip_analysis": cache_reused,
                                  "reused_clip_analysis_ids": sorted(reused_clip_analysis_ids),
                                  "vision_reanalysis_ids": sorted(vision_reanalysis_ids),
                                  "crop_probe_failures": crop_probe_failures,
                                  "reused_story_plan": reuse_story is not None,
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
    if options.get("aspect_ratio"):
        override["aspect_ratio"] = options["aspect_ratio"]
    if options.get("style_preset"):
        override["style_preset"] = options["style_preset"]
    if options.get("pace"):
        override["pace"] = options["pace"]
    if options.get("transition"):
        override["transitions"] = options["transition"]
    if options.get("transition_duration_ms") is not None:
        override["transition_ms"] = options["transition_duration_ms"]
    if options.get("scale_mode"):
        override["scale_mode"] = options["scale_mode"]
    for field in ("brightness", "contrast", "saturation", "gamma", "shadow_coolness", "highlight_warmth", "vignette"):
        if options.get(field) is not None:
            override[field] = options[field]
    return override


def _resolved_music_style(context_pack, style_override: dict) -> str | None:
    """Resolve the same preset and explicit overrides for soundtrack generation as video style."""
    from services.style_engine.mapper import StyleMapper

    return StyleMapper.resolve_style_for_pack(context_pack, style_override).get("music")


def _refresh_prompt_text_styles(brief, prompt: str):
    """Apply corrected prompt-local text styling to cached intent without losing timings."""
    from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
    from shared.contracts.base import revalidate

    parsed = LexicalIntentExtractor._text_overlay_requirements(prompt)
    if not parsed:
        return brief
    by_key = {(item.role, item.text): item for item in parsed}
    fields = ("position", "color", "font_family", "wide_tracking", "glow", "bold", "background")
    overlays = []
    existing_keys = set()
    for existing in brief.text_overlays:
        existing_keys.add((existing.role, existing.text))
        fresh = by_key.get((existing.role, existing.text))
        updates = {field: getattr(fresh, field) for field in fields
                   if fresh is not None and getattr(fresh, field) is not None}
        overlays.append(revalidate(existing, **updates) if updates else existing)
    overlays.extend(item for item in parsed if (item.role, item.text) not in existing_keys)
    return revalidate(brief, text_overlays=tuple(overlays))


def _materialize_prompt_visuals(brief, duration_ms: int, render_dir, job_id: str,
                                width: int = 1080, height: int = 1920,
                                title_position: str | None = None):
    """Rasterize exact prompt text and return compiler-ready overlays plus honest effect status."""
    from services.edit_executor.text_overlay import render_text_overlay_asset
    from shared.contracts.base import revalidate
    from shared.contracts.timeline import TextOverlayIR

    requirements = brief.text_overlays
    credits = [item for item in requirements if item.role == "credit"]
    credit_duration = min(2500, max(1000, duration_ms // max(2, len(credits) + 1))) if credits else 0
    overlays = []
    chosen_title_position = title_position or ("lower_third" if height > width else "center")

    for index, requirement in enumerate(requirements):
        if requirement.role == "title":
            placed = revalidate(requirement, position=chosen_title_position)
        elif requirement.role == "subtitle" and chosen_title_position == "lower_third":
            placed = revalidate(requirement, position="bottom")
        else:
            placed = requirement
        if requirement.role == "title":
            default_start, default_end = 0, min(duration_ms, 4500)
        elif requirement.role == "subtitle":
            default_start, default_end = min(350, max(0, duration_ms - 1)), min(duration_ms, 4500)
        elif requirement.role == "credit":
            credit_index = credits.index(requirement)
            default_start = max(0, duration_ms - (len(credits) - credit_index) * credit_duration)
            default_end = min(duration_ms, default_start + credit_duration)
        else:
            default_start = min(round(duration_ms * 0.42), max(0, duration_ms - 1))
            default_end = min(duration_ms, default_start + min(2500, duration_ms))

        start_ms = round(requirement.start_s * 1000) if requirement.start_s is not None else default_start
        end_ms = round(requirement.end_s * 1000) if requirement.end_s is not None else default_end
        if start_ms >= duration_ms:
            continue
        end_ms = min(duration_ms, end_ms)
        if end_ms <= start_ms:
            continue

        overlay_id = f"text-{index:02d}-{requirement.role}"
        asset_path = render_dir / f"overlay_{job_id}_{index:02d}.png"
        render_text_overlay_asset(placed, width, height, asset_path)
        overlays.append(TextOverlayIR(
            overlay_id=overlay_id,
            role=requirement.role,
            text=requirement.text,
            source_span=requirement.source_span,
            asset_path=str(asset_path),
            start_ms=start_ms,
            end_ms=end_ms,
            position=placed.position or {
                "title": "center", "subtitle": "below_title", "credit": "lower_third", "caption": "lower_third",
            }[requirement.role],
            color=requirement.color or ("magenta" if requirement.role == "title" else "white"),
            font_family=requirement.font_family or ("serif" if requirement.role == "title" else "sans_serif"),
            glow=requirement.glow is True,
            timing_source="prompt" if requirement.start_s is not None or requirement.end_s is not None
            else "presentation_default",
        ))

    unsupported_reasons = {
        "dust_particles": "No particle compositing generator is available in the local render pipeline.",
        "atmospheric_haze": "No volumetric fog/haze synthesis is available in the local render pipeline.",
        "rim_lighting": "The current color balance is global; masked subject relighting is unavailable.",
        "camera_motion": "Source motion is preserved; virtual pan/zoom generation is unavailable.",
    }
    unavailable = [
        {"effect": effect, "status": "unavailable", "reason": reason, "evidence": list(brief.evidence)}
        for effect, reason in unsupported_reasons.items()
        if effect in brief.visual_treatments
    ]
    grain_strength = 2 if "film_grain" in brief.visual_treatments else 0
    return tuple(overlays), grain_strength, unavailable


async def _mark_failed(project_id: str, job_id: str, message: str, trace_id: str | None) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Job, JobState, Project, ProjectStatus
    from app.services.progress import ProgressEvent, Stage, publish_sync

    settings = _settings()
    should_publish = False
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            project = await db.get(Project, project_id)
            job = await db.get(Job, job_id)
            if job is None or job.state in (JobState.CANCELLED.value, JobState.SUCCEEDED.value,
                                            JobState.FAILED.value):
                return
            if project is not None and (job.prompt_version is None or
                                        int(job.prompt_version) == int(project.prompt_version or 1)):
                project.status = ProjectStatus.FAILED.value
                project.status_detail = message[:500]
                should_publish = True
            elif project is not None:
                job.state = JobState.CANCELLED.value
                job.error = "Superseded by a newer project prompt version."
                job.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return
            if job is not None:
                job.state = JobState.FAILED.value
                job.error = message[:2000]
                job.finished_at = datetime.now(timezone.utc)
                should_publish = True
            await db.commit()
    finally:
        await engine.dispose()
    if should_publish:
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
