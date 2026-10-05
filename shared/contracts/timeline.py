"""
MONTA — Timeline Intermediate Representation (Timeline IR)
============================================================
The canonical, deterministic, and serializable representation of an edit.
Bridges creative intelligence (Story Architect / Style Engine) and media execution (FFmpeg).
Uses integer milliseconds (TimeMs) to eliminate cumulative floating-point timing drift.
"""

from enum import StrEnum
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

TIMELINE_IR_VERSION = "timeline.v1"

TimeMs = Annotated[int, Field(ge=0, description="Time in integer milliseconds")]


class AspectRatio(StrEnum):
    LANDSCAPE_16_9 = "16:9"
    PORTRAIT_9_16 = "9:16"
    SQUARE_1_1 = "1:1"
    PORTRAIT_4_5 = "4:5"


class ScaleMode(StrEnum):
    FIT = "fit"      # Pad with black bars (pillarbox/letterbox) to maintain full content
    FILL = "fill"    # Center-crop to fill target aspect ratio
    STRETCH = "stretch"


class TransitionType(StrEnum):
    CUT = "cut"
    CROSSFADE = "crossfade"
    FADE_BLACK = "fade_black"
    FADE_WHITE = "fade_white"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"


class ColorAdjustment(BaseModel):
    """Basic color parameters mapped to FFmpeg eq filter."""

    model_config = ConfigDict(frozen=True)

    brightness: float = Field(0.0, ge=-1.0, le=1.0, description="-1.0 to 1.0 (default: 0.0)")
    contrast: float = Field(1.0, ge=0.0, le=3.0, description="0.0 to 3.0 (default: 1.0)")
    saturation: float = Field(1.0, ge=0.0, le=3.0, description="0.0 to 3.0 (default: 1.0)")
    gamma: float = Field(1.0, ge=0.1, le=10.0, description="0.1 to 10.0 (default: 1.0)")


class VideoSegmentIR(BaseModel):
    """One video segment cut in the timeline."""

    model_config = ConfigDict(frozen=True)

    segment_id: str = Field(..., min_length=1)
    clip_id: str = Field(..., min_length=1)
    source_path: str = Field(..., min_length=1)
    source_in_ms: TimeMs
    source_out_ms: TimeMs
    timeline_start_ms: TimeMs
    timeline_end_ms: TimeMs
    speed: float = Field(1.0, gt=0.1, le=10.0, description="Playback speed multiplier (1.0 = normal)")
    volume: float = Field(1.0, ge=0.0, le=4.0, description="Audio volume multiplier for clip audio (0.0 = muted)")
    fade_in_ms: TimeMs = 0
    fade_out_ms: TimeMs = 0
    transition_in: TransitionType = TransitionType.CUT
    transition_in_ms: TimeMs = 0
    color: Optional[ColorAdjustment] = None
    is_hero: bool = False

    @property
    def source_duration_ms(self) -> int:
        return self.source_out_ms - self.source_in_ms

    @property
    def timeline_duration_ms(self) -> int:
        return self.timeline_end_ms - self.timeline_start_ms

    @model_validator(mode="after")
    def _validate_segment(self):
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError(f"segment {self.segment_id}: source_out_ms ({self.source_out_ms}) must be > source_in_ms ({self.source_in_ms})")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError(f"segment {self.segment_id}: timeline_end_ms ({self.timeline_end_ms}) must be > timeline_start_ms ({self.timeline_start_ms})")
        return self


class AudioSegmentIR(BaseModel):
    """Optional background music / voiceover audio track segment."""

    model_config = ConfigDict(frozen=True)

    track_id: str = Field(..., min_length=1)
    source_path: str = Field(..., min_length=1)
    source_in_ms: TimeMs = 0
    source_out_ms: Optional[TimeMs] = None
    timeline_start_ms: TimeMs = 0
    timeline_end_ms: TimeMs
    volume: float = Field(1.0, ge=0.0, le=4.0)
    fade_in_ms: TimeMs = 0
    fade_out_ms: TimeMs = 0
    loop: bool = False


class RenderProfileIR(BaseModel):
    """Render encoding profile settings."""

    model_config = ConfigDict(frozen=True)

    name: str = "standard"
    crf: int = Field(23, ge=0, le=51)
    preset: str = "medium"
    video_bitrate: Optional[str] = None
    audio_bitrate: str = "192k"
    pixel_format: str = "yuv420p"
    audio_sample_rate: int = 48000
    preferred_encoder: Optional[str] = None
    fallback_encoder: str = "libx264"


class TimelineIR(BaseModel):
    """Authoritative, complete edit timeline specification."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["timeline.v1"] = TIMELINE_IR_VERSION
    project_id: str = Field(..., min_length=1)
    width: int = Field(1920, gt=0)
    height: int = Field(1080, gt=0)
    fps: float = Field(30.0, gt=0.0, le=120.0)
    aspect_ratio: AspectRatio = AspectRatio.LANDSCAPE_16_9
    scale_mode: ScaleMode = ScaleMode.FIT
    video_segments: tuple[VideoSegmentIR, ...] = Field(..., min_length=1)
    audio_segments: tuple[AudioSegmentIR, ...] = ()
    render_profile: RenderProfileIR = RenderProfileIR()
    total_duration_ms: TimeMs

    @property
    def duration_s(self) -> float:
        return self.total_duration_ms / 1000.0

    @model_validator(mode="after")
    def _validate_timeline(self):
        if not self.video_segments:
            raise ValueError("Timeline must contain at least one video segment")
        calculated_end = max(s.timeline_end_ms for s in self.video_segments)
        if self.total_duration_ms < calculated_end:
            raise ValueError(
                f"total_duration_ms ({self.total_duration_ms}) is less than last segment end ({calculated_end})"
            )
        return self


TimelineSegmentIR = VideoSegmentIR

