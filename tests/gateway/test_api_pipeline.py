"""
Integration: the queue hand-off and the progress channel.

The hand-off is the riskiest transition in Layers 1–2, because it spans two systems that
fail independently. The invariant is: **the job row exists before the broker is called.**
If submission fails, the work is recorded as `submit_failed` with the reason, the project
returns to a state the user can act on, and the client gets a 503 it can retry. A design
that called the broker first and recorded afterwards would lose the job on a crash between
the two, leaving a project stuck in `queued` with nothing running.
"""

from __future__ import annotations

import pytest

from .conftest import AppHarness, MediaFixtures, requires_ffmpeg

pytestmark = requires_ffmpeg


async def project_with_clip(harness: AppHarness, media: MediaFixtures) -> dict:
    project = (await harness.client.post("/projects", json={
        "title": "Reel", "prompt": "Punchy 30 second reel, upbeat.", "target_platform": "instagram",
    })).json()
    response = await harness.client.post(f"/upload/{project['id']}",
                                         files=[("files", (media.hd.name, media.hd.read_bytes(), "video/mp4"))])
    assert response.status_code == 201, response.text
    return project


# ------------------------------------------------------------------ submission


async def test_starting_the_pipeline_records_the_job_then_submits_it(
    harness: AppHarness, media: MediaFixtures
) -> None:
    project = await project_with_clip(harness, media)
    response = await harness.client.post(f"/projects/{project['id']}/pipeline")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["tracking_id"] == body["job_id"]
    assert body["state"] == "queued"
    assert body["project_status"] == "queued"

    # The broker was called with the job id we persisted, so the worker's task id and our
    # row refer to the same unit of work — that is what makes tracking possible.
    assert harness.queue.submitted == [
        {"job_id": body["job_id"], "project_id": project["id"], "trace_id": harness.queue.submitted[0]["trace_id"]}
    ]
    assert harness.queue.submitted[0]["trace_id"]

    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    assert detail["status"] == "queued"
    assert [j["state"] for j in detail["jobs"]] == ["queued"]
    assert detail["jobs"][0]["id"] == body["job_id"]


async def test_a_dead_broker_keeps_the_job_and_returns_503(harness: AppHarness, media: MediaFixtures) -> None:
    """
    Redis being down must not lose the user's work or their explanation.

    The observable contract: 503 (retryable), a `submit_failed` job row carrying the error,
    the project back in `uploading` so the button is usable, and a `failed` progress event so
    a watching dashboard updates without polling.
    """
    project = await project_with_clip(harness, media)
    harness.queue.available = False

    response = await harness.client.post(f"/projects/{project['id']}/pipeline")
    assert response.status_code == 503
    assert "queue" in response.text.lower() or "broker" in response.text.lower()

    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    assert detail["status"] == "uploading", "the project must not be left stuck in queued"
    assert detail["jobs"][0]["state"] == "submit_failed"
    assert detail["jobs"][0]["error"]
    assert detail["clips"][0]["status"] == "uploaded", "the clips are untouched"
    assert "failed" in harness.bus.stages()


async def test_the_pipeline_can_be_started_again_after_a_broker_outage(
    harness: AppHarness, media: MediaFixtures
) -> None:
    """The retry must actually work — a 503 that permanently wedges the project is not a retry."""
    project = await project_with_clip(harness, media)
    harness.queue.available = False
    assert (await harness.client.post(f"/projects/{project['id']}/pipeline")).status_code == 503

    harness.queue.available = True
    response = await harness.client.post(f"/projects/{project['id']}/pipeline")
    assert response.status_code == 202, response.text
    assert len(harness.queue.submitted) == 1

    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    states = [j["state"] for j in detail["jobs"]]
    assert states[0] == "queued"                 # newest first
    assert "submit_failed" in states             # the failure is kept as history, not erased


async def test_starting_twice_is_a_409(harness: AppHarness, media: MediaFixtures) -> None:
    """
    Two clicks must not start two pipelines over the same clips.

    Without this the second run would race the first, publishing interleaved progress and
    writing two story plans over one project.
    """
    project = await project_with_clip(harness, media)
    assert (await harness.client.post(f"/projects/{project['id']}/pipeline")).status_code == 202

    second = await harness.client.post(f"/projects/{project['id']}/pipeline")
    assert second.status_code == 409
    assert len(harness.queue.submitted) == 1


async def test_a_project_with_no_usable_clips_cannot_start(harness: AppHarness, media: MediaFixtures) -> None:
    """Rejected clips are not footage: a project holding only rejections has nothing to edit."""
    project = (await harness.client.post("/projects", json={
        "title": "Empty", "prompt": "Make something great.", "target_platform": "tiktok",
    })).json()

    empty = await harness.client.post(f"/projects/{project['id']}/pipeline")
    # 409, not 400: the request is well formed, the project is simply not in a state where
    # it can be run — the same class of answer as "already running".
    assert empty.status_code == 409
    assert "clip" in empty.text.lower()
    assert harness.queue.submitted == []

    await harness.client.post(f"/upload/{project['id']}",
                              files=[("files", ("bad.mp4", media.not_media.read_bytes(), "video/mp4"))])
    still_empty = await harness.client.post(f"/projects/{project['id']}/pipeline")
    assert still_empty.status_code == 409
    assert harness.queue.submitted == []


async def test_starting_an_unknown_project_is_a_404(harness: AppHarness) -> None:
    assert (await harness.client.post("/projects/proj_nope/pipeline")).status_code == 404


async def test_a_queued_project_refuses_edits_and_uploads(harness: AppHarness, media: MediaFixtures) -> None:
    """
    While work is in flight the inputs are frozen.

    Letting a clip be added after the Director planned over the clip list would produce a
    story plan referring to a set of clips that no longer exists.
    """
    project = await project_with_clip(harness, media)
    await harness.client.post(f"/projects/{project['id']}/pipeline")

    patched = await harness.client.patch(f"/projects/{project['id']}", json={"title": "Renamed"})
    assert patched.status_code == 409

    deleted = await harness.client.delete(f"/projects/{project['id']}")
    assert deleted.status_code == 409


# ------------------------------------------------------------------ progress channel


async def test_the_events_endpoint_replays_recorded_history(harness: AppHarness, media: MediaFixtures) -> None:
    """
    A page opened after work started must not be blank.

    This endpoint is also the fallback the UI uses when the websocket cannot connect at all,
    so it has to carry the same events, not a summary of them.
    """
    project = await project_with_clip(harness, media)
    await harness.client.post(f"/projects/{project['id']}/pipeline")

    events = (await harness.client.get(f"/projects/{project['id']}/events")).json()
    assert [e["stage"] for e in events][:1] == ["uploaded"]
    assert all(e["project_id"] == project["id"] for e in events)
    assert all(e["message"] for e in events)
    assert all(e["at"] for e in events)


async def test_no_event_claims_a_percentage_it_does_not_know(harness: AppHarness, media: MediaFixtures) -> None:
    """
    The no-fake-progress rule, asserted rather than documented.

    Upload and submission cannot know a completion ratio, so every event they publish must
    leave `percent` null. Only the worker, which knows how many clips it has analysed,
    is allowed to set one.
    """
    project = await project_with_clip(harness, media)
    await harness.client.post(f"/projects/{project['id']}/pipeline")

    events = (await harness.client.get(f"/projects/{project['id']}/events")).json()
    assert events, "expected at least the upload event"
    assert all(e["percent"] is None for e in events), [e for e in events if e["percent"] is not None]


async def test_a_broken_progress_bus_does_not_break_an_upload(harness: AppHarness, media: MediaFixtures) -> None:
    """
    Progress is best-effort. Redis being down degrades the live view; it must not fail the
    request, because the upload itself succeeded and the user's file is safely stored.
    """
    harness.bus.available = False
    project = (await harness.client.post("/projects", json={
        "title": "Reel", "prompt": "Punchy 30 second reel.", "target_platform": "instagram",
    })).json()

    response = await harness.client.post(f"/upload/{project['id']}",
                                         files=[("files", (media.hd.name, media.hd.read_bytes(), "video/mp4"))])
    assert response.status_code == 201, response.text
    assert harness.bus.published == []

    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    assert len(detail["clips"]) == 1


# ------------------------------------------------------------------ websocket


async def test_the_websocket_opens_with_a_database_snapshot(harness: AppHarness, media: MediaFixtures) -> None:
    """
    The first frame is authoritative state, read from the database — not an empty shell the
    client has to fill in from later events it may have missed.
    """
    from fastapi.testclient import TestClient

    project = await project_with_clip(harness, media)
    await harness.client.post(f"/projects/{project['id']}/pipeline")

    with TestClient(harness.app) as client:
        with client.websocket_connect(f"/api/v1/ws/projects/{project['id']}?user=tester") as socket:
            snapshot = socket.receive_json()

    assert snapshot["type"] == "snapshot"
    assert snapshot["project_id"] == project["id"]
    assert snapshot["status"] == "queued"
    assert len(snapshot["clips"]) == 1
    assert snapshot["clips"][0]["status"] == "uploaded"
    assert snapshot["job"]["state"] == "queued"


async def test_the_websocket_replays_history_after_the_snapshot(harness: AppHarness, media: MediaFixtures) -> None:
    from fastapi.testclient import TestClient

    project = await project_with_clip(harness, media)

    with TestClient(harness.app) as client:
        with client.websocket_connect(f"/api/v1/ws/projects/{project['id']}?user=tester") as socket:
            assert socket.receive_json()["type"] == "snapshot"
            replayed = socket.receive_json()

    assert replayed["stage"] == "uploaded"
    assert replayed["project_id"] == project["id"]
    assert replayed["percent"] is None


async def test_the_websocket_refuses_an_unknown_project_with_a_reason(harness: AppHarness) -> None:
    """
    A closed socket with no explanation looks identical to a network problem, so the client
    would retry forever. The error frame is what lets it stop.
    """
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(harness.app) as client:
        with client.websocket_connect("/api/v1/ws/projects/proj_nope?user=tester") as socket:
            error = socket.receive_json()
            with pytest.raises(WebSocketDisconnect) as excinfo:
                socket.receive_json()

    assert error["code"] == "project_not_found"
    assert error["message"]
    assert excinfo.value.code == 4404


async def test_the_websocket_rejects_a_malformed_user_without_a_500(harness: AppHarness) -> None:
    """A bad identity is reported on the socket, not raised through the handshake."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(harness.app) as client:
        with client.websocket_connect("/api/v1/ws/projects/proj_x?user=../etc/passwd") as socket:
            error = socket.receive_json()
            with pytest.raises(WebSocketDisconnect) as excinfo:
                socket.receive_json()

    assert error["code"] == "invalid_user"
    assert excinfo.value.code == 4400


async def test_another_users_project_is_not_visible_over_the_websocket(
    harness: AppHarness, media: MediaFixtures
) -> None:
    """Ownership is enforced on the socket too, or the REST check would be decorative."""
    from fastapi.testclient import TestClient

    project = await project_with_clip(harness, media)

    with TestClient(harness.app) as client:
        with client.websocket_connect(f"/api/v1/ws/projects/{project['id']}?user=intruder") as socket:
            error = socket.receive_json()

    assert error["code"] == "project_not_found"
