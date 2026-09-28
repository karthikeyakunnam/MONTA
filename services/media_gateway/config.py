"""
MONTA — Media Gateway Limits (Layer 2)
========================================
Every ingest rule in one place, as data. The API, the validation engine and
the tests read the same object, so a limit can never drift between them.

Resolution classes carry their own project budget. A project that mixes HD and
4K must satisfy both budgets: 4K material is expensive to decode and analyze,
so its budget stays small even when the HD budget is untouched.
"""

from dataclasses import dataclass, field
from typing import Literal

MIB = 1024 * 1024
ResolutionClassName = Literal["1080p", "4k"]


@dataclass(frozen=True)
class ResolutionClass:
    """One resolution bucket and the project budget that applies to it."""

    name: ResolutionClassName
    max_pixels: int
    max_clips: int
    max_total_seconds: float

    @property
    def max_total_minutes(self) -> float:
        return self.max_total_seconds / 60


HD = ResolutionClass(name="1080p", max_pixels=1920 * 1080, max_clips=20, max_total_seconds=600)
UHD = ResolutionClass(name="4k", max_pixels=3840 * 2160, max_clips=4, max_total_seconds=240)


@dataclass(frozen=True)
class GatewayLimits:
    """Ingest policy for one deployment."""

    # ffprobe reports a comma-separated list for container families; any overlap is accepted.
    allowed_containers: frozenset[str] = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"})
    allowed_extensions: frozenset[str] = frozenset({"mp4", "mov", "mkv"})
    allowed_video_codecs: frozenset[str] = frozenset({"h264", "hevc", "av1", "vp9", "mpeg4", "prores"})
    max_file_bytes: int = 500 * MIB
    max_request_bytes: int = 4 * 1024 * MIB
    max_clip_seconds: float = 240.0
    min_clip_seconds: float = 0.4
    classes: tuple[ResolutionClass, ...] = field(default_factory=lambda: (HD, UHD))
    max_filename_length: int = 180

    def resolution_class(self, width: int, height: int) -> ResolutionClass | None:
        """Smallest class that can hold ``width × height``; None when the frame is too large."""
        pixels = width * height
        for cls in sorted(self.classes, key=lambda c: c.max_pixels):
            if pixels <= cls.max_pixels:
                return cls
        return None

    def class_by_name(self, name: str) -> ResolutionClass:
        return next(c for c in self.classes if c.name == name)

    @property
    def max_pixels(self) -> int:
        return max(c.max_pixels for c in self.classes)

    @property
    def extension_list(self) -> str:
        return ", ".join(sorted(self.allowed_extensions))


DEFAULT_LIMITS = GatewayLimits()
