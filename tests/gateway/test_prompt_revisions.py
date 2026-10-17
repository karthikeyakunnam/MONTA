"""Same-project prompt revisions, retries, cancellation, and render preservation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from .conftest import AppHarness, MediaFixtures, requires_ffmpeg

pytestmark = requires_ffmpeg


async def _project_with_clip(harness: AppHarness, media: MediaFixtures, *, options: dict | None = None) -> dict:
    project = (await harness.client.post("/projects", json={
        "title": "Same project", "prompt": "A calm 15 second story", "target_platform": "instagram",
        "edit_options": options or {"target_duration_s": 15, "color_grade": "warm", "add_music": False},
    })).json()
    response = await harness.client.post(
        f"/upload/{project['id']}", files=[("files", (media.hd.name, media.hd.read_bytes(), "video/mp4"))]
    )
    assert response.status_code == 201, response.text
    return project


async def _seed_successful_render(harness: AppHarness, project_id: str, job_id: str) -> str:
    from app.models import Job, Project
    from app.models.render import Render

    render_id = "render_revision_previous"
    render_path = harness.storage.root / "renders" / project_id / "previous.mp4"
    render_path.parent.mkdir(parents=True)
    render_path.write_bytes(b"previous-good-render" * 300)
    async with harness.session_maker() as db:
        job = await db.get(Job, job_id)
        project = await db.get(Project, project_id)
        job.state = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        project.status = "complete"
        project.story_judgement = {"overall_score": 7.0, "current_render_id": render_id}
        db.add(Render(id=render_id, project_id=project_id, job_id=job.id, status="complete",
                      resolution="1080x1920", format="mp4", output_path=str(render_path),
                      file_size_bytes=render_path.stat().st_size, duration=15.0,
                      created_at=datetime.now(timezone.utc)))
        await db.commit()
    return render_id


@pytest.mark.asyncio
async def test_revision_keeps_project_uploads_options_and_previous_render(
    harness: AppHarness, media: MediaFixtures,
) -> None:
    project = await _project_with_clip(harness, media)
    initial = await harness.client.post(f"/projects/{project['id']}/pipeline")
    assert initial.status_code == 202
    render_id = await _seed_successful_render(harness, project["id"], initial.json()["job_id"])
    before = (await harness.client.get(f"/projects/{project['id']}" )).json()
    clip_ids = [clip["id"] for clip in before["clips"]]

    payload = {
        "prompt": "A calm 30 second story with a hopeful middle and a happy ending",
        "target_platform": "youtube_short",
        "edit_options": {"target_duration_s": 30, "color_grade": "vibrant", "add_music": True,
                          "music_style": "emotional piano", "caption_style": "Minimal", "aspect_ratio": "9:16",
                          "style_preset": "cinematic", "pace": "fast", "transition": "crossfade",
                          "transition_duration_ms": 325, "scale_mode": "fill", "brightness": -0.04,
                          "contrast": 1.2, "saturation": 0.9, "gamma": 0.98,
                          "shadow_coolness": 0.2, "highlight_warmth": 0.3, "vignette": 0.15},
        "idempotency_key": "revise-version-two-001",
    }
    response = await harness.client.post(f"/projects/{project['id']}/revisions", json=payload)
    assert response.status_code == 202, response.text
    revision = response.json()
    detail = (await harness.client.get(f"/projects/{project['id']}" )).json()
    assert detail["id"] == project["id"]
    assert detail["prompt_version"] == 2
    assert detail["raw_prompt"] == payload["prompt"]
    assert detail["status"] == "queued"
    assert [clip["id"] for clip in detail["clips"]] == clip_ids
    assert detail["render_url"] == f"/api/v1/render/{render_id}/download"
    assert next(render for render in detail["renders"] if render["id"] == render_id)["is_current"] is True
    assert detail["jobs"][0]["id"] == revision["job_id"]
    assert detail["jobs"][0]["prompt_snapshot"] == payload["prompt"]
    assert detail["jobs"][0]["edit_options_snapshot"]["color_grade"] == "vibrant"
    for name, value in {
        "style_preset": "cinematic", "pace": "fast", "transition": "crossfade",
        "transition_duration_ms": 325, "scale_mode": "fill", "brightness": -0.04,
        "contrast": 1.2, "saturation": 0.9, "gamma": 0.98,
        "shadow_coolness": 0.2, "highlight_warmth": 0.3, "vignette": 0.15,
    }.items():
        assert detail["jobs"][0]["edit_options_snapshot"][name] == value
    assert detail["jobs"][1]["prompt_snapshot"] == "A calm 15 second story"
    assert detail["jobs"][1]["edit_options_snapshot"]["color_grade"] == "warm"
    assert detail["renders"][0]["job_id"] == initial.json()["job_id"]

    # A failed revision still leaves the same completed render addressable.
    from app.models import Job, Project
    async with harness.session_maker() as db:
        revision_job = await db.get(Job, revision["job_id"])
        existing_project = await db.get(Project, project["id"])
        revision_job.state = "failed"
        revision_job.error = "FFmpeg failed on the new version"
        existing_project.status = "failed"
        await db.commit()
    failed_detail = (await harness.client.get(f"/projects/{project['id']}" )).json()
    assert failed_detail["render_url"] == f"/api/v1/render/{render_id}/download"
    assert failed_detail["renders"][0]["job_id"] == initial.json()["job_id"]
    assert next(render for render in failed_detail["renders"] if render["id"] == render_id)["is_current"] is True


@pytest.mark.asyncio
async def test_manual_revision_validates_sources_and_preserves_prior_render(
    harness: AppHarness, media: MediaFixtures,
) -> None:
    project = await _project_with_clip(harness, media)
    initial = await harness.client.post(f"/projects/{project['id']}/pipeline")
    assert initial.status_code == 202
    prior_render = await _seed_successful_render(harness, project["id"], initial.json()["job_id"])
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    clip = detail["clips"][0]
    edit = {"shots": [{"clip_id": clip["id"], "source_in_ms": 0, "source_out_ms": 1000,
                       "transition_in": "cut", "transition_in_ms": 0, "volume": 0.5}],
            "captions": [{"start": 0.1, "end": 0.8, "text": "Edited words"}]}
    payload = {"prompt": detail["raw_prompt"], "target_platform": detail["target_platform"],
               "edit_options": {"add_music": False, "caption_style": "Minimal", "manual_timeline": edit},
               "expected_prompt_version": detail["prompt_version"], "idempotency_key": "manual-revision-001"}
    bad = await harness.client.post(f"/projects/{project['id']}/revisions", json={
        **payload, "edit_options": {"manual_timeline": {"shots": [
            {"clip_id": "unowned", "source_in_ms": 0, "source_out_ms": 1000}]}},
        "idempotency_key": "manual-revision-bad",
    })
    assert bad.status_code == 409
    stale = await harness.client.post(f"/projects/{project['id']}/revisions", json={
        **payload, "expected_prompt_version": 99, "idempotency_key": "manual-revision-stale",
    })
    assert stale.status_code == 409
    bad_caption = await harness.client.post(f"/projects/{project['id']}/revisions", json={
        **payload, "edit_options": {"caption_style": "Minimal", "manual_timeline": {
            **edit, "captions": [{"start": 0.5, "end": 1.5, "text": "Too late"}]}},
        "idempotency_key": "manual-caption-bad",
    })
    assert bad_caption.status_code == 409
    accepted = await harness.client.post(f"/projects/{project['id']}/revisions", json=payload)
    assert accepted.status_code == 202, accepted.text
    updated = (await harness.client.get(f"/projects/{project['id']}")).json()
    stored = updated["edit_options"]["manual_timeline"]
    assert stored["shots"][0]["clip_id"] == clip["id"]
    assert stored["shots"][0]["source_out_ms"] == 1000
    assert stored["shots"][0]["volume"] == 0.5
    assert stored["captions"] == edit["captions"]
    assert updated["jobs"][0]["edit_options_snapshot"]["manual_timeline"] == stored
    assert updated["render_url"] == f"/api/v1/render/{prior_render}/download"


@pytest.mark.asyncio
async def test_clip_preview_serves_only_owned_project_uploads(harness: AppHarness, media: MediaFixtures) -> None:
    project = await _project_with_clip(harness, media)
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    clip = detail["clips"][0]
    assert clip["preview_url"].endswith(f"/clips/{clip['id']}/preview")
    route = clip["preview_url"].removeprefix("/api/v1")
    response = await harness.client.get(route)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("video/")
    assert len(response.content) == clip["file_size_bytes"]
    denied = await harness.client.get(route, headers={"X-MONTA-User": "another-user"})
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_revision_idempotency_and_active_job_serialization(harness: AppHarness, media: MediaFixtures) -> None:
    project = await _project_with_clip(harness, media)
    payload = {
        "prompt": "A punchy 30 second reel with a hopeful finish", "target_platform": "instagram",
        "edit_options": {"target_duration_s": 30, "add_music": True, "aspect_ratio": "9:16"},
        "idempotency_key": "same-revision-request-001",
    }
    first = await harness.client.post(f"/projects/{project['id']}/revisions", json=payload)
    duplicate = await harness.client.post(f"/projects/{project['id']}/revisions", json=payload)
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["job_id"] == duplicate.json()["job_id"]
    assert len(harness.queue.submitted) == 1
    different = {**payload, "idempotency_key": "different-revision-request-002", "prompt": "Another edit"}
    assert (await harness.client.post(f"/projects/{project['id']}/revisions", json=different)).status_code == 409


@pytest.mark.asyncio
async def test_failed_submit_can_retry_and_queued_retry_can_cancel(harness: AppHarness, media: MediaFixtures) -> None:
    project = await _project_with_clip(harness, media)
    harness.queue.available = False
    response = await harness.client.post(f"/projects/{project['id']}/revisions", json={
        "prompt": "An emotional 20 second edit", "target_platform": "instagram",
        "edit_options": {"target_duration_s": 20, "add_music": False},
        "idempotency_key": "failed-submit-request-001",
    })
    assert response.status_code == 503
    detail = (await harness.client.get(f"/projects/{project['id']}" )).json()
    failed_job = detail["jobs"][0]
    assert failed_job["state"] == "submit_failed"
    assert failed_job["prompt_snapshot"] == "An emotional 20 second edit"

    harness.queue.available = True
    retried = await harness.client.post(
        f"/projects/{project['id']}/jobs/{failed_job['id']}/retry",
        json={"idempotency_key": "retry-submit-request-001"},
    )
    assert retried.status_code == 202, retried.text
    retry_job_id = retried.json()["job_id"]
    detail = (await harness.client.get(f"/projects/{project['id']}" )).json()
    retry_job = next(job for job in detail["jobs"] if job["id"] == retry_job_id)
    assert retry_job["retry_of_job_id"] == failed_job["id"]
    assert retry_job["prompt_snapshot"] == failed_job["prompt_snapshot"]
    assert retry_job["edit_options_snapshot"] == failed_job["edit_options_snapshot"]

    cancelled = await harness.client.post(f"/projects/{project['id']}/jobs/{retry_job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    final_detail = (await harness.client.get(f"/projects/{project['id']}" )).json()
    assert next(job for job in final_detail["jobs"] if job["id"] == retry_job_id)["state"] == "cancelled"
    assert final_detail["clips"][0]["status"] == "uploaded"
    assert harness.queue.cancelled == [retry_job_id]
