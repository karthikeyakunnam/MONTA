"""
Layer 2 validation rules.

These are the tests that matter most for security, because `sanitize_filename` and the
extension check are the only things between an attacker-controlled string and the
filesystem. Every case here is a real attack shape or a real creator mistake, not a
synthetic permutation.
"""

from __future__ import annotations

import pytest

from services.media_gateway import DEFAULT_LIMITS, ClipQuota, FileRejected, GatewayLimits, ValidationCode
from services.media_gateway.config import ResolutionClass
from services.media_gateway.metadata import AudioStreamInfo, MediaMetadata
from services.media_gateway.validation import (
    check_extension,
    check_size,
    resolution_class_for,
    sanitize_filename,
    validate_identifier,
    validate_media,
    validate_project_quota,
)


def meta(**overrides) -> MediaMetadata:
    """A valid 1080p H.264 probe result; override one field per test."""
    base = dict(container="mov,mp4,m4a,3gp,3g2,mj2", duration_s=6.0, fps=30.0, video_codec="h264",
                width=1920, height=1080, bitrate=8_000_000, video_bitrate=7_500_000, rotation=0,
                file_size_bytes=6_000_000, audio_streams=[AudioStreamInfo(index=1, codec="aac", channels=2, sample_rate=48000)])
    base.update(overrides)
    return MediaMetadata(**base)


# ---------------------------------------------------------------- filename sanitisation


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("holiday.mp4", "holiday.mp4"),
        ("my clip (1).mp4", "my_clip_1.mp4"),
        # Runs of replaced characters collapse: the name is shown to the creator.
        ("shot   ***   two.mp4", "shot_two.mp4"),
        # POSIX traversal
        ("../../../etc/passwd.mp4", "passwd.mp4"),
        ("/absolute/path/clip.mp4", "clip.mp4"),
        # Windows traversal — an attack on a Linux server too, since the server never
        # treats backslash as a separator and would otherwise keep it in the name.
        (r"..\..\windows\system32\evil.mp4", "evil.mp4"),
        (r"C:\Users\me\clip.mp4", "clip.mp4"),
        # Unicode that normalises into ASCII, plus characters outside the allowlist
        # NFKD folds the accent; the em dash has no ASCII form and is dropped.
        ("vidéo—final.mp4", "videofinal.mp4"),
        # A name with no Latin characters keeps its *type* and gets a neutral stem,
        # rather than folding to the bare string "mp4" and failing the extension check.
        ("клип.mp4", "clip.mp4"),
        ("日本語.mov", "clip.mov"),
        # Dot runs must not survive in any arrangement
        ("a..b...c.mp4", "a.b.c.mp4"),
        ("....mp4", "mp4"),
        # Null byte truncation attempt
        # A null byte cannot truncate the name into something with a different type.
        ("clip.mp4\x00.exe", "clip.mp4.exe"),
        # A dotfile keeps its name instead of having "bashrc" read as its extension.
        (".bashrc", "bashrc"),
    ],
)
def test_sanitize_filename_neutralises_paths_and_unicode(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "...", "/", "..", "././.", "\x00", "🎬"])
def test_sanitize_filename_rejects_names_with_nothing_usable(raw: str) -> None:
    with pytest.raises(FileRejected) as excinfo:
        sanitize_filename(raw)
    assert excinfo.value.issues[0].code == ValidationCode.FILENAME_INVALID


def test_sanitize_filename_never_returns_a_path_separator() -> None:
    """Property: whatever goes in, what comes out cannot escape a directory."""
    for raw in ["a/b/c.mp4", r"a\b\c.mp4", "..%2f..%2fx.mp4", "x/../../y.mp4", "./././z.mp4"]:
        result = sanitize_filename(raw)
        assert "/" not in result and "\\" not in result
        assert not result.startswith(".")
        assert ".." not in result


def test_sanitize_filename_caps_length_but_keeps_the_extension() -> None:
    name = sanitize_filename("x" * 500 + ".mp4")
    assert len(name) <= DEFAULT_LIMITS.max_filename_length
    assert name.endswith(".mp4")


# ---------------------------------------------------------------- extension and size


@pytest.mark.parametrize("filename", ["clip.mp4", "clip.MP4", "clip.mov", "clip.mkv"])
def test_allowed_extensions_pass(filename: str) -> None:
    assert check_extension(filename) is None


@pytest.mark.parametrize("filename", ["payload.exe", "archive.zip", "script.sh", "clip.avi", "clip.webm", "noext"])
def test_disallowed_extensions_are_named_in_the_error(filename: str) -> None:
    issue = check_extension(filename)
    assert issue is not None
    assert issue.code == ValidationCode.UNSUPPORTED_EXTENSION
    # The message must tell the creator what *is* allowed, not just that they were wrong.
    assert "mp4" in issue.message.lower()
    assert sorted(DEFAULT_LIMITS.allowed_extensions) == issue.detail["allowed"]


def test_size_rules() -> None:
    assert check_size(1024) is None
    assert check_size(0).code == ValidationCode.EMPTY_FILE
    assert check_size(-1).code == ValidationCode.EMPTY_FILE
    too_big = check_size(DEFAULT_LIMITS.max_file_bytes + 1)
    assert too_big.code == ValidationCode.FILE_TOO_LARGE
    assert too_big.detail["max_bytes"] == DEFAULT_LIMITS.max_file_bytes


# ---------------------------------------------------------------- probe-dependent rules


def test_valid_media_passes_every_rule() -> None:
    assert validate_media(meta()) == []


def test_container_mismatch_is_rejected() -> None:
    """A .mp4 extension on an AVI payload: the extension check passed, this must not."""
    issues = validate_media(meta(container="avi"))
    assert [i.code for i in issues] == [ValidationCode.UNSUPPORTED_CONTAINER]


def test_unsupported_codec_names_the_codec_and_the_fix() -> None:
    issues = validate_media(meta(video_codec="vp8"))
    assert issues[0].code == ValidationCode.UNSUPPORTED_VIDEO_CODEC
    assert "vp8" in issues[0].message
    assert issues[0].detail["codec"] == "vp8"


def test_duration_bounds() -> None:
    assert validate_media(meta(duration_s=DEFAULT_LIMITS.max_clip_seconds + 1))[0].code == ValidationCode.CLIP_TOO_LONG
    assert validate_media(meta(duration_s=0.1))[0].code == ValidationCode.CLIP_TOO_SHORT
    # Exactly on the boundary is allowed: a 240.0s clip is not "longer than 240s".
    assert validate_media(meta(duration_s=DEFAULT_LIMITS.max_clip_seconds)) == []


def test_resolution_above_the_largest_class_is_rejected() -> None:
    issues = validate_media(meta(width=7680, height=4320))
    assert issues[0].code == ValidationCode.RESOLUTION_TOO_HIGH
    assert resolution_class_for(meta(width=7680, height=4320)) is None


def test_every_broken_rule_is_reported_at_once() -> None:
    """One round trip must list every problem; drip-feeding one error per upload is hostile."""
    issues = validate_media(meta(container="avi", video_codec="vp8", duration_s=9999, width=7680, height=4320))
    assert {i.code for i in issues} == {
        ValidationCode.UNSUPPORTED_CONTAINER,
        ValidationCode.UNSUPPORTED_VIDEO_CODEC,
        ValidationCode.CLIP_TOO_LONG,
        ValidationCode.RESOLUTION_TOO_HIGH,
    }


def test_rotated_portrait_video_is_classified_by_its_displayed_size() -> None:
    """A phone clip stores 1920x1080 with rotation=90. Its class must not depend on the tag."""
    upright = meta(width=1920, height=1080, rotation=0)
    rotated = meta(width=1920, height=1080, rotation=90)
    assert rotated.display_size == (1080, 1920)
    assert resolution_class_for(rotated).name == resolution_class_for(upright).name
    assert validate_media(rotated) == []


# ---------------------------------------------------------------- project budgets


HD = ResolutionClass(name="1080p", max_pixels=1920 * 1080, max_clips=20, max_total_seconds=600)
UHD = ResolutionClass(name="4k", max_pixels=3840 * 2160, max_clips=4, max_total_seconds=240)


def test_hd_budget_allows_twenty_clips_and_rejects_the_twenty_first() -> None:
    existing = [ClipQuota("1080p", 10.0) for _ in range(19)]
    assert validate_project_quota(existing, HD, 10.0) == []
    existing.append(ClipQuota("1080p", 10.0))
    issues = validate_project_quota(existing, HD, 10.0)
    assert issues[0].code == ValidationCode.PROJECT_CLIP_LIMIT
    assert issues[0].detail["max_clips"] == 20


def test_hd_duration_budget_is_ten_minutes() -> None:
    existing = [ClipQuota("1080p", 60.0) for _ in range(9)]      # 540s used
    assert validate_project_quota(existing, HD, 60.0) == []      # 600s exactly: allowed
    issues = validate_project_quota(existing, HD, 61.0)          # 601s: not
    assert issues[0].code == ValidationCode.PROJECT_DURATION_LIMIT
    assert issues[0].detail["max_seconds"] == pytest.approx(600.0)


def test_4k_budget_is_four_clips_and_four_minutes() -> None:
    existing = [ClipQuota("4k", 30.0) for _ in range(3)]
    assert validate_project_quota(existing, UHD, 30.0) == []
    assert validate_project_quota(existing + [ClipQuota("4k", 30.0)], UHD, 30.0)[0].code == (
        ValidationCode.PROJECT_CLIP_LIMIT
    )
    assert validate_project_quota([ClipQuota("4k", 120.0), ClipQuota("4k", 119.0)], UHD, 2.0)[0].code == (
        ValidationCode.PROJECT_DURATION_LIMIT
    )


def test_budgets_are_per_class_and_do_not_interfere() -> None:
    """A project full of 1080p clips must still accept a 4K clip, and vice versa."""
    hd_full = [ClipQuota("1080p", 30.0) for _ in range(20)]
    assert validate_project_quota(hd_full, UHD, 30.0) == []
    uhd_full = [ClipQuota("4k", 60.0) for _ in range(4)]
    assert validate_project_quota(uhd_full, HD, 30.0) == []


def test_quota_check_is_a_no_op_without_a_class() -> None:
    """A file whose resolution has no class was already rejected; quota must not crash on it."""
    assert validate_project_quota([ClipQuota("1080p", 10.0)], None, 10.0) == []


# ---------------------------------------------------------------- identifiers


@pytest.mark.parametrize("value", ["local", "user-1", "proj_abc123", "A" * 64])
def test_valid_identifiers(value: str) -> None:
    assert validate_identifier(value, "user id") == value


@pytest.mark.parametrize("value", ["", "a" * 65, "../etc", "a/b", "a b", "drop table;", "a\x00b", "é"])
def test_identifiers_reject_anything_that_could_reach_a_path_or_a_query(value: str) -> None:
    with pytest.raises(ValueError):
        validate_identifier(value, "user id")


def test_limits_can_be_tightened_per_deployment() -> None:
    """Policy is data: a stricter deployment changes limits, not validation code."""
    strict = GatewayLimits(allowed_extensions=frozenset({"mp4"}), max_clip_seconds=30.0,
                           classes=(ResolutionClass(name="720p", max_pixels=1280 * 720, max_clips=5,
                                                    max_total_seconds=120),))
    assert check_extension("clip.mov", strict).code == ValidationCode.UNSUPPORTED_EXTENSION
    assert validate_media(meta(duration_s=45.0), strict)[0].code == ValidationCode.CLIP_TOO_LONG
    assert resolution_class_for(meta(), strict) is None      # 1080p exceeds a 720p-only deployment
