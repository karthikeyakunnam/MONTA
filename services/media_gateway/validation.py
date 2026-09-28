"""
MONTA — Validation Engine (Layer 2)
=====================================
Every rejection is a coded, user-facing issue: what was wrong, the actual
value, the allowed value. The UI renders these verbatim, so they never say
"invalid file".

Order matters, because each step is cheaper than the next:
filename → extension → size → probe → media rules → project quota.
"""

import os
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Sequence

from services.media_gateway.config import DEFAULT_LIMITS, GatewayLimits, ResolutionClass
from services.media_gateway.metadata import MediaMetadata

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_DOTS = re.compile(r"\.{2,}")
_UNDERSCORES = re.compile(r"_{2,}")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Stem used when a name is entirely characters we cannot keep — a purely Cyrillic, CJK or
#: emoji file name. The file itself is fine, so substituting a stem is correct; rejecting
#: would fail a valid upload and blame the wrong thing.
FALLBACK_STEM = "clip"


class ValidationCode(StrEnum):
    FILENAME_INVALID = "filename_invalid"
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    UNSUPPORTED_CONTAINER = "unsupported_container"
    UNSUPPORTED_VIDEO_CODEC = "unsupported_video_codec"
    UNREADABLE_MEDIA = "unreadable_media"
    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    CLIP_TOO_LONG = "clip_too_long"
    CLIP_TOO_SHORT = "clip_too_short"
    RESOLUTION_TOO_HIGH = "resolution_too_high"
    PROJECT_CLIP_LIMIT = "project_clip_limit"
    PROJECT_DURATION_LIMIT = "project_duration_limit"


@dataclass(frozen=True)
class ValidationIssue:
    """One reason a file was rejected, ready to show to the creator."""

    code: ValidationCode
    message: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "detail": self.detail}


class FileRejected(Exception):
    """Raised when ingest must stop. Carries every issue found so far."""

    def __init__(self, issues: Sequence[ValidationIssue]):
        self.issues = list(issues)
        super().__init__("; ".join(i.message for i in self.issues))

    def as_dicts(self) -> list[dict]:
        return [i.as_dict() for i in self.issues]


@dataclass(frozen=True)
class ClipQuota:
    """An already-accepted clip, as the quota check sees it."""

    resolution_class: str
    duration_s: float


def _clean_part(part: str) -> str:
    """ASCII-fold one path component and keep only characters that are safe everywhere."""
    folded = unicodedata.normalize("NFKD", part).encode("ascii", "ignore").decode()
    collapsed = _UNDERSCORES.sub("_", _SAFE_CHARS.sub("_", folded))
    return _DOTS.sub(".", collapsed).strip("._-")


def sanitize_filename(raw: str, limits: GatewayLimits = DEFAULT_LIMITS) -> str:
    """Return a safe basename, or raise ``FileRejected``.

    Order is deliberate:

    1. Strip directory components from the *original* string, POSIX **and** Windows
       (``..\\..\\x.mp4`` is an attack on a Linux server too). Doing this after the ASCII
       fold would let a non-Latin directory name collapse into the file name.
    2. Split the extension with ``splitext`` semantics, so a dotfile (``.bashrc``) keeps its
       name instead of having "bashrc" mistaken for its type.
    3. Clean stem and extension independently: ASCII-fold, replace anything outside
       ``[A-Za-z0-9._-]``, collapse ``_`` and ``.`` runs so ``..`` cannot survive.
    4. If the stem cleaned away to nothing but a usable extension survived, substitute
       ``FALLBACK_STEM``. A file called ``клип.mp4`` is a perfectly good upload; folding it
       to the bare string ``mp4`` used to make the *extension* check fail, rejecting a valid
       video with a message about unsupported formats. With no surviving extension either,
       there is nothing to work with and the name really is invalid.
    """
    base = PureWindowsPath(PurePosixPath((raw or "").strip()).name).name
    stem, dot_ext = os.path.splitext(base)
    stem = _clean_part(stem)
    extension = _clean_part(dot_ext.lstrip("."))

    if not stem:
        if not extension:
            raise FileRejected([ValidationIssue(
                ValidationCode.FILENAME_INVALID,
                "This file name cannot be used. Rename the file using letters, numbers, dots or dashes.",
                {"received": (raw or "")[:120]},
            )])
        stem = FALLBACK_STEM

    keep = limits.max_filename_length - (len(extension) + 1 if extension else 0)
    stem = stem[: max(1, keep)]
    return f"{stem}.{extension}" if extension else stem


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def check_extension(filename: str, limits: GatewayLimits = DEFAULT_LIMITS) -> ValidationIssue | None:
    ext = extension_of(filename)
    if ext in limits.allowed_extensions:
        return None
    return ValidationIssue(
        ValidationCode.UNSUPPORTED_EXTENSION,
        f"{'.' + ext if ext else 'This file'} is not a supported video format. Upload {limits.extension_list}.",
        {"received": ext, "allowed": sorted(limits.allowed_extensions)},
    )


def check_size(size_bytes: int, limits: GatewayLimits = DEFAULT_LIMITS) -> ValidationIssue | None:
    if size_bytes <= 0:
        return ValidationIssue(ValidationCode.EMPTY_FILE, "This file is empty.", {"bytes": size_bytes})
    if size_bytes > limits.max_file_bytes:
        return ValidationIssue(
            ValidationCode.FILE_TOO_LARGE,
            f"This file is {size_bytes / (1024 * 1024):.0f} MB. The limit is "
            f"{limits.max_file_bytes / (1024 * 1024):.0f} MB per clip.",
            {"bytes": size_bytes, "max_bytes": limits.max_file_bytes},
        )
    return None


def validate_media(meta: MediaMetadata, limits: GatewayLimits = DEFAULT_LIMITS) -> list[ValidationIssue]:
    """Rules that need the probe: container, codec, duration, resolution."""
    issues: list[ValidationIssue] = []
    if not (meta.containers & limits.allowed_containers):
        issues.append(ValidationIssue(
            ValidationCode.UNSUPPORTED_CONTAINER,
            f"This file is not a supported video container. Upload {limits.extension_list}.",
            {"container": meta.container, "allowed": sorted(limits.allowed_containers)},
        ))
    if meta.video_codec not in limits.allowed_video_codecs:
        issues.append(ValidationIssue(
            ValidationCode.UNSUPPORTED_VIDEO_CODEC,
            f"The video codec '{meta.video_codec}' is not supported. Re-export as H.264 or HEVC.",
            {"codec": meta.video_codec, "allowed": sorted(limits.allowed_video_codecs)},
        ))
    if meta.duration_s > limits.max_clip_seconds:
        issues.append(ValidationIssue(
            ValidationCode.CLIP_TOO_LONG,
            f"This clip is {meta.duration_s / 60:.1f} minutes. The limit is "
            f"{limits.max_clip_seconds / 60:.0f} minutes per clip.",
            {"duration_s": round(meta.duration_s, 2), "max_seconds": limits.max_clip_seconds},
        ))
    if meta.duration_s < limits.min_clip_seconds:
        issues.append(ValidationIssue(
            ValidationCode.CLIP_TOO_SHORT,
            f"This clip is {meta.duration_s:.2f}s. Clips must be at least {limits.min_clip_seconds:.1f}s.",
            {"duration_s": round(meta.duration_s, 2), "min_seconds": limits.min_clip_seconds},
        ))
    width, height = meta.display_size
    if limits.resolution_class(width, height) is None:
        biggest = max(limits.classes, key=lambda c: c.max_pixels)
        issues.append(ValidationIssue(
            ValidationCode.RESOLUTION_TOO_HIGH,
            f"{width}×{height} is above the {biggest.name} limit. Export at {biggest.name} or smaller.",
            {"resolution": meta.resolution, "max_class": biggest.name, "max_pixels": biggest.max_pixels},
        ))
    return issues


def resolution_class_for(meta: MediaMetadata, limits: GatewayLimits = DEFAULT_LIMITS) -> ResolutionClass | None:
    width, height = meta.display_size
    return limits.resolution_class(width, height)


def validate_project_quota(
    existing: Sequence[ClipQuota],
    candidate_class: ResolutionClass | None,
    candidate_duration_s: float,
    limits: GatewayLimits = DEFAULT_LIMITS,
) -> list[ValidationIssue]:
    """Per-resolution-class budget: clip count and total duration, counting the candidate.

    ``candidate_class`` is ``None`` for a file whose resolution fits no class. That file was
    already rejected by :func:`validate_media` with ``RESOLUTION_TOO_HIGH``, so there is no
    budget to charge it against and this returns no issues. The parameter is typed optional
    because that is what callers hold — ``resolution_class_for`` returns an optional — and a
    dereference here would turn a clean 422 into a 500.
    """
    if candidate_class is None:
        return []
    same = [c for c in existing if c.resolution_class == candidate_class.name]
    count = len(same) + 1
    total = sum(c.duration_s for c in same) + candidate_duration_s
    issues: list[ValidationIssue] = []
    if count > candidate_class.max_clips:
        issues.append(ValidationIssue(
            ValidationCode.PROJECT_CLIP_LIMIT,
            f"This project already has {len(same)} {candidate_class.name} clips. "
            f"The limit is {candidate_class.max_clips}.",
            {"resolution_class": candidate_class.name, "existing": len(same), "max_clips": candidate_class.max_clips},
        ))
    if total > candidate_class.max_total_seconds:
        issues.append(ValidationIssue(
            ValidationCode.PROJECT_DURATION_LIMIT,
            f"This clip would bring the project to {total / 60:.1f} minutes of {candidate_class.name} footage. "
            f"The limit is {candidate_class.max_total_minutes:.0f} minutes.",
            {"resolution_class": candidate_class.name, "total_seconds": round(total, 2),
             "max_seconds": candidate_class.max_total_seconds},
        ))
    return issues


def validate_identifier(value: str, kind: str) -> str:
    """Identifiers reach the filesystem, so only ``[A-Za-z0-9_-]`` is allowed."""
    if not ID_PATTERN.match(value or ""):
        raise ValueError(f"invalid {kind}: {value!r}")
    return value
