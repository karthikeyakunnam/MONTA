"""
Integration: the upload API as Layer 1 actually calls it.

Real ASGI app, real SQLite database, real gateway, real ffmpeg-produced files. Only the
broker and the progress bus are doubles, because starting Redis and a Celery worker to
assert an HTTP status code would make these tests untrustworthy in CI rather than more
realistic.

The contract under test is the one the frontend depends on:

* 201 every file accepted, 207 mixed, 422 all rejected — each with per-file results;
* every rejected file carries a machine code *and* a sentence;
* a rejected file still appears in the project, so the UI can explain why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import AppHarness, MediaFixtures, requires_ffmpeg

pytestmark = requires_ffmpeg


async def create_project(harness: AppHarness, **overrides) -> dict:
    body = {"title": "Bali trip", "prompt": "Make a punchy 30 second reel, upbeat, cut on the beat.",
            "target_platform": "instagram"}
    body.update(overrides)
    response = await harness.client.post("/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def files_for(*paths: Path) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (p.name, p.read_bytes(), "video/mp4")) for p in paths]


# ------------------------------------------------------------------ project lifecycle


async def test_create_read_update_and_list_a_project(harness: AppHarness) -> None:
    project = await create_project(harness)
    assert project["status"] == "draft"
    assert project["clip_count"] == 0
    assert project["raw_prompt"].startswith("Make a punchy")

    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    assert detail["clips"] == []
    assert detail["total_duration_s"] == 0
    assert {q["resolution_class"] for q in detail["quota"]} == {"1080p", "4k"}

    patched = await harness.client.patch(f"/projects/{project['id']}", json={"title": "Bali, final"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Bali, final"

    listing = (await harness.client.get("/projects")).json()
    assert [p["id"] for p in listing] == [project["id"]]


@pytest.mark.parametrize(
    "body,field",
    [
        ({"title": "", "prompt": "a valid prompt"}, "title"),
        ({"title": "ok", "prompt": "no"}, "prompt"),
        ({"title": "   ", "prompt": "a valid prompt"}, "title"),
        ({"title": "ok", "prompt": "a valid prompt", "target_platform": "myspace"}, "target_platform"),
    ],
)
async def test_invalid_project_input_is_a_422_naming_the_field(harness: AppHarness, body: dict, field: str) -> None:
    response = await harness.client.post("/projects", json=body)
    assert response.status_code == 422
    assert field in response.text


async def test_a_project_belongs_to_its_creator(harness: AppHarness) -> None:
    """Another user's id must not be able to read or upload to this project."""
    project = await create_project(harness)
    other = {"X-MONTA-User": "someone-else"}

    assert (await harness.client.get(f"/projects/{project['id']}", headers=other)).status_code == 404
    assert (await harness.client.get("/projects", headers=other)).json() == []
    upload = await harness.client.post(f"/upload/{project['id']}", files=files_for(Path(__file__)), headers=other)
    assert upload.status_code == 404


async def test_a_malformed_user_header_is_a_400_not_a_500(harness: AppHarness) -> None:
    """An unusable identity is the client's mistake; it must not page an on-call engineer."""
    response = await harness.client.get("/projects", headers={"X-MONTA-User": "../etc/passwd"})
    assert response.status_code == 400
    assert "X-MONTA-User" in response.text


async def test_deleting_a_project_removes_its_clips_and_bytes(harness: AppHarness, media: MediaFixtures) -> None:
    project = await create_project(harness)
    await harness.client.post(f"/upload/{project['id']}", files=files_for(media.hd))
    stored = list((harness.storage.root / "uploads" / project["id"]).glob("*"))
    assert len(stored) == 1

    assert (await harness.client.delete(f"/projects/{project['id']}")).status_code == 204
    assert (await harness.client.get(f"/projects/{project['id']}")).status_code == 404
    assert not (harness.storage.root / "uploads" / project["id"]).exists()


# ------------------------------------------------------------------ upload happy paths


async def test_uploading_one_real_clip_returns_201_with_probed_metadata(
    harness: AppHarness, media: MediaFixtures
) -> None:
    project = await create_project(harness)
    response = await harness.client.post(f"/upload/{project['id']}", files=files_for(media.hd))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["accepted"] == 1 and body["rejected"] == 0
    assert body["project_status"] == "uploading"

    clip = body["results"][0]["clip"]
    assert clip["status"] == "uploaded"
    assert clip["original_filename"] == "hd_clip.mp4"
    assert clip["resolution"] == "1920x1080"
    assert clip["resolution_class"] == "1080p"
    assert clip["video_codec"] == "h264"
    assert clip["fps"] == pytest.approx(30.0, abs=0.1)
    assert clip["duration"] == pytest.approx(2.0, abs=0.2)
    assert clip["has_audio"] is True
    assert len(clip["sha256"]) == 64
    assert clip["file_size_bytes"] == media.hd.stat().st_size

    # The bytes are where the contract says they are.
    assert (harness.storage.root / "uploads" / project["id"] / f"{clip['id']}.mp4").is_file()

    # And the project reflects it.
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    assert detail["clip_count"] == 1
    assert detail["total_duration_s"] == pytest.approx(2.0, abs=0.2)
    hd_quota = next(q for q in detail["quota"] if q["resolution_class"] == "1080p")
    assert hd_quota["clips_used"] == 1


async def test_uploading_several_files_in_one_request(harness: AppHarness, media: MediaFixtures) -> None:
    project = await create_project(harness)
    response = await harness.client.post(f"/upload/{project['id']}",
                                         files=files_for(media.hd, media.hd_silent, media.mov))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["accepted"] == 3
    assert [r["filename"] for r in body["results"]] == ["hd_clip.mp4", "hd_silent.mp4", "clip.mov"]
    assert len({r["clip"]["id"] for r in body["results"]}) == 3
    assert len(list((harness.storage.root / "uploads" / project["id"]).glob("*"))) == 3


async def test_upload_publishes_a_real_progress_event(harness: AppHarness, media: MediaFixtures) -> None:
    """The UI's first state comes from here, so the event must be published, not implied."""
    project = await create_project(harness)
    await harness.client.post(f"/upload/{project['id']}", files=files_for(media.hd))

    assert "uploaded" in harness.bus.stages()
    event = next(e for e in harness.bus.published if str(e.stage) == "uploaded")
    assert event.project_id == project["id"]
    assert event.clip_id is not None
    assert event.message


async def test_limits_endpoint_matches_what_the_server_enforces(harness: AppHarness) -> None:
    """Layer 1 pre-checks against this; if it drifts, the UI lies about what will be accepted."""
    limits = (await harness.client.get("/upload/limits")).json()
    assert sorted(limits["allowed_extensions"]) == ["mkv", "mov", "mp4"]
    assert limits["max_file_bytes"] == 500 * 1024 * 1024
    assert limits["max_files_per_request"] == 25
    classes = {c["name"]: c for c in limits["resolution_classes"]}
    assert classes["1080p"]["max_clips"] == 20
    assert classes["1080p"]["max_total_seconds"] == 600
    assert classes["4k"]["max_clips"] == 4
    assert classes["4k"]["max_total_seconds"] == 240


# ------------------------------------------------------------------ upload failure paths


async def test_an_executable_renamed_to_mp4_is_a_422_with_an_explainable_reason(
    harness: AppHarness, media: MediaFixtures
) -> None:
    project = await create_project(harness)
    response = await harness.client.post(f"/upload/{project['id']}", files=files_for(media.not_media))

    assert response.status_code == 422
    result = response.json()["results"][0]
    assert result["accepted"] is False
    assert result["clip"] is None
    assert result["issues"][0]["code"] == "unreadable_media"
    assert result["issues"][0]["message"]

    # The rejection is recorded so the dashboard can show it, and no bytes were kept.
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    rejected = [c for c in detail["clips"] if c["status"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["rejection_issues"][0]["code"] == "unreadable_media"
    assert detail["total_duration_s"] == 0
    assert list((harness.storage.root / "uploads" / project["id"]).glob("*")) == []


async def test_a_real_executable_extension_is_rejected_by_name(harness: AppHarness, tmp_path: Path) -> None:
    project = await create_project(harness)
    payload = tmp_path / "payload.exe"
    payload.write_bytes(b"MZ\x90\x00" + b"\x00" * 1024)

    response = await harness.client.post(f"/upload/{project['id']}", files=files_for(payload))
    assert response.status_code == 422
    assert response.json()["results"][0]["issues"][0]["code"] == "unsupported_extension"


async def test_a_zip_is_rejected(harness: AppHarness, tmp_path: Path) -> None:
    project = await create_project(harness)
    archive = tmp_path / "clips.zip"
    archive.write_bytes(b"PK\x03\x04" + b"\x00" * 1024)
    response = await harness.client.post(f"/upload/{project['id']}", files=files_for(archive))
    assert response.status_code == 422
    assert response.json()["results"][0]["issues"][0]["code"] == "unsupported_extension"


async def test_a_mixed_request_returns_207_and_decides_each_file_independently(
    harness: AppHarness, media: MediaFixtures
) -> None:
    """
    This is the case that makes retry possible.

    One bad file in a selection of five must not fail the other four, and the response must
    say which was which.
    """
    project = await create_project(harness)
    response = await harness.client.post(
        f"/upload/{project['id']}", files=files_for(media.hd, media.not_media, media.hd_silent, media.truncated),
    )

    assert response.status_code == 207
    body = response.json()
    assert body["accepted"] == 2 and body["rejected"] == 2
    verdicts = {r["filename"]: r["accepted"] for r in body["results"]}
    assert verdicts == {"hd_clip.mp4": True, "payload.mp4": False,
                        "hd_silent.mp4": True, "truncated.mp4": False}
    assert len(list((harness.storage.root / "uploads" / project["id"]).glob("*"))) == 2


async def test_a_clip_that_is_too_short_names_the_duration_rule(harness: AppHarness, media: MediaFixtures) -> None:
    project = await create_project(harness)
    response = await harness.client.post(f"/upload/{project['id']}", files=files_for(media.tiny))
    issue = response.json()["results"][0]["issues"][0]
    assert issue["code"] == "clip_too_short"
    assert "0.2" in issue["message"] or "0.20" in issue["message"]


async def test_exceeding_the_4k_clip_budget_is_rejected_with_the_numbers(
    harness: AppHarness, make_clip
) -> None:
    """
    Four 4K clips are allowed; the fifth is not, and the message must carry the numbers.

    Each clip is genuinely different footage — identical bytes would be deduplicated and
    never reach the quota check at all.
    """
    project = await create_project(harness)
    for i in range(4):
        clip = make_clip(seed=i, width=3840, height=2160, seconds=1.0, fps=24)
        response = await harness.client.post(
            f"/upload/{project['id']}", files=[("files", (f"shot{i}.mp4", clip.read_bytes(), "video/mp4"))],
        )
        assert response.status_code == 201, f"upload {i} failed: {response.text}"

    fifth = make_clip(seed=99, width=3840, height=2160, seconds=1.0, fps=24)
    response = await harness.client.post(
        f"/upload/{project['id']}", files=[("files", ("shot4.mp4", fifth.read_bytes(), "video/mp4"))],
    )
    assert response.status_code == 422, response.text
    issue = response.json()["results"][0]["issues"][0]
    assert issue["code"] == "project_clip_limit"
    assert issue["detail"]["max_clips"] == 4
    assert issue["detail"]["resolution_class"] == "4k"
    assert "4" in issue["message"]

    # The rejection did not consume a 4K slot, and the 1080p budget is untouched.
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    quota = {q["resolution_class"]: q for q in detail["quota"]}
    assert quota["4k"]["clips_used"] == 4
    assert quota["1080p"]["clips_used"] == 0


async def test_the_1080p_budget_is_twenty_clips(harness: AppHarness, make_clip) -> None:
    """
    The other half of the resolution policy, and the one a real session hits first.

    Twenty 1080p clips fit; the twenty-first does not. Kept to short clips so the duration
    budget (10 minutes) is not what rejects it — the clip *count* is.
    """
    project = await create_project(harness)
    for i in range(20):
        clip = make_clip(seed=100 + i, width=1920, height=1080, seconds=1.0)
        response = await harness.client.post(
            f"/upload/{project['id']}", files=[("files", (f"hd{i}.mp4", clip.read_bytes(), "video/mp4"))],
        )
        assert response.status_code == 201, f"upload {i} failed: {response.text}"

    extra = make_clip(seed=999, width=1920, height=1080, seconds=1.0)
    response = await harness.client.post(
        f"/upload/{project['id']}", files=[("files", ("hd20.mp4", extra.read_bytes(), "video/mp4"))],
    )
    assert response.status_code == 422, response.text
    issue = response.json()["results"][0]["issues"][0]
    assert issue["code"] == "project_clip_limit"
    assert issue["detail"]["max_clips"] == 20
    # And a 4K clip is still welcome: the budgets are independent.
    uhd = make_clip(seed=7, width=3840, height=2160, seconds=1.0, fps=24)
    accepted = await harness.client.post(
        f"/upload/{project['id']}", files=[("files", ("uhd.mp4", uhd.read_bytes(), "video/mp4"))],
    )
    assert accepted.status_code == 201, accepted.text


async def test_a_quota_rejection_in_a_batch_still_accepts_what_fits(
    harness: AppHarness, make_clip
) -> None:
    """
    Files in one request are decided in order, so the budget can run out mid-batch.

    The files that fit must be kept: discarding the whole batch because the last file
    overflowed would make an almost-full project impossible to finish filling.
    """
    project = await create_project(harness)
    for i in range(3):
        clip = make_clip(seed=200 + i, width=3840, height=2160, seconds=1.0, fps=24)
        assert (await harness.client.post(
            f"/upload/{project['id']}", files=[("files", (f"uhd{i}.mp4", clip.read_bytes(), "video/mp4"))],
        )).status_code == 201

    batch = [
        ("files", ("fits.mp4", make_clip(seed=210, width=3840, height=2160, seconds=1.0, fps=24).read_bytes(),
                   "video/mp4")),
        ("files", ("overflows.mp4", make_clip(seed=211, width=3840, height=2160, seconds=1.0, fps=24).read_bytes(),
                   "video/mp4")),
    ]
    response = await harness.client.post(f"/upload/{project['id']}", files=batch)

    assert response.status_code == 207, response.text
    verdicts = {r["filename"]: r["accepted"] for r in response.json()["results"]}
    assert verdicts == {"fits.mp4": True, "overflows.mp4": False}
    quota = {q["resolution_class"]: q for q in (await harness.client.get(f"/projects/{project['id']}")).json()["quota"]}
    assert quota["4k"]["clips_used"] == 4


async def test_too_many_files_in_one_request_is_a_413(harness: AppHarness, media: MediaFixtures) -> None:
    project = await create_project(harness)
    payload = media.tiny.read_bytes()
    many = [("files", (f"c{i}.mp4", payload, "video/mp4")) for i in range(26)]
    response = await harness.client.post(f"/upload/{project['id']}", files=many)
    assert response.status_code == 413
    assert "25" in response.text


async def test_uploading_to_an_unknown_project_is_a_404(harness: AppHarness, media: MediaFixtures) -> None:
    response = await harness.client.post("/upload/proj_does_not_exist", files=files_for(media.hd))
    assert response.status_code == 404


async def test_a_request_with_no_files_is_a_422(harness: AppHarness) -> None:
    project = await create_project(harness)
    response = await harness.client.post(f"/upload/{project['id']}")
    assert response.status_code == 422


# ------------------------------------------------------------------ dedupe over HTTP


async def test_re_uploading_the_same_bytes_returns_the_clip_that_is_already_there(
    harness: AppHarness, media: MediaFixtures
) -> None:
    """
    Creators re-drag the same folder constantly, so this is a routine action, not an edge case.

    One file means one clip: the upload is accepted, the response points at the clip that
    already exists, and nothing is added — not a second row (the schema's
    UNIQUE(project_id, sha256) forbids it) and not a second copy on disk.
    """
    project = await create_project(harness)
    first = await harness.client.post(f"/upload/{project['id']}",
                                      files=[("files", ("take1.mp4", media.hd.read_bytes(), "video/mp4"))])
    second = await harness.client.post(f"/upload/{project['id']}",
                                       files=[("files", ("take1-copy.mp4", media.hd.read_bytes(), "video/mp4"))])

    assert first.status_code == 201 and second.status_code == 201, second.text
    first_clip = first.json()["results"][0]["clip"]
    second_result = second.json()["results"][0]
    assert second_result["accepted"] is True
    assert second_result["duplicate_of"] == first_clip["id"]
    assert second_result["clip"]["id"] == first_clip["id"]
    assert second_result["clip"]["sha256"] == first_clip["sha256"]

    # One clip row, one file on disk, and the quota charged once.
    assert len(list((harness.storage.root / "uploads" / project["id"]).glob("*"))) == 1
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    assert len(detail["clips"]) == 1
    assert detail["total_duration_s"] == pytest.approx(2.0, abs=0.2)
    hd_quota = next(q for q in detail["quota"] if q["resolution_class"] == "1080p")
    assert hd_quota["clips_used"] == 1


async def test_deleting_a_clip_never_removes_bytes_another_clip_still_points_at(
    harness: AppHarness, media: MediaFixtures
) -> None:
    """
    The dedupe hazard, across projects this time.

    Two projects that uploaded identical footage each keep their own file, so deleting one
    must not touch the other. `delete_clip` also guards the case where two rows share a
    storage key: without that check, removing one would leave the other pointing at a
    missing file and the worker would fail on it much later, far from the cause.
    """
    project_a = await create_project(harness, title="A")
    project_b = await create_project(harness, title="B")
    upload_a = await harness.client.post(f"/upload/{project_a['id']}",
                                         files=[("files", ("a.mp4", media.hd.read_bytes(), "video/mp4"))])
    upload_b = await harness.client.post(f"/upload/{project_b['id']}",
                                         files=[("files", ("b.mp4", media.hd.read_bytes(), "video/mp4"))])
    clip_a = upload_a.json()["results"][0]["clip"]
    clip_b = upload_b.json()["results"][0]["clip"]
    assert clip_a["sha256"] == clip_b["sha256"]
    assert clip_a["id"] != clip_b["id"]

    assert (await harness.client.delete(f"/upload/{project_b['id']}/{clip_b['id']}")).status_code == 204

    assert (harness.storage.root / "uploads" / project_a["id"] / f"{clip_a['id']}.mp4").is_file()
    detail = (await harness.client.get(f"/projects/{project_a['id']}")).json()
    assert [c["id"] for c in detail["clips"]] == [clip_a["id"]]


async def test_deleting_a_clip_removes_its_bytes(harness: AppHarness, media: MediaFixtures) -> None:
    project = await create_project(harness)
    upload = await harness.client.post(f"/upload/{project['id']}", files=files_for(media.hd))
    clip_id = upload.json()["results"][0]["clip"]["id"]

    assert (await harness.client.delete(f"/upload/{project['id']}/{clip_id}")).status_code == 204
    assert not (harness.storage.root / "uploads" / project["id"] / f"{clip_id}.mp4").exists()
    assert (await harness.client.delete(f"/upload/{project['id']}/{clip_id}")).status_code == 404


async def test_a_rejected_clip_can_be_deleted_too(harness: AppHarness, media: MediaFixtures) -> None:
    """A rejected row is real state; the user must be able to clear it from the dashboard."""
    project = await create_project(harness)
    await harness.client.post(f"/upload/{project['id']}", files=files_for(media.not_media))
    detail = (await harness.client.get(f"/projects/{project['id']}")).json()
    rejected_id = detail["clips"][0]["id"]

    assert (await harness.client.delete(f"/upload/{project['id']}/{rejected_id}")).status_code == 204
    assert (await harness.client.get(f"/projects/{project['id']}")).json()["clips"] == []


# ------------------------------------------------------------------ observability


async def test_every_response_carries_a_request_id(harness: AppHarness) -> None:
    """Without this, a user's bug report cannot be found in the logs."""
    response = await harness.client.get("/projects")
    assert response.headers.get("X-Request-ID")


async def test_health_reports_each_dependency_separately(harness: AppHarness) -> None:
    body = (await harness.client.get("/health")).json()
    assert body["database"] is True
    assert body["storage"] is True
    assert body["ffprobe"] is True
    assert body["queue"] is True
    assert body["status"] == "ok"


async def test_health_is_degraded_when_the_queue_is_down(harness: AppHarness) -> None:
    harness.queue.available = False
    body = (await harness.client.get("/health")).json()
    assert body["queue"] is False
    assert body["status"] == "degraded"
    assert body["database"] is True          # one broken dependency must not mask the others
