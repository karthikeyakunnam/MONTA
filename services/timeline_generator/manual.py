"""Validated user shot overrides applied to the existing Timeline IR."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts.base import revalidate
from shared.contracts.timeline import CaptionCueIR, ColorAdjustment, TimelineIR, TransitionType, VideoSegmentIR
from services.edit_executor.validator import EditPlanValidator


class ManualShot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clip_id: str = Field(min_length=1)
    source_in_ms: int = Field(ge=0)
    source_out_ms: int = Field(gt=0)
    transition_in: TransitionType = TransitionType.CUT
    transition_in_ms: int = Field(0, ge=0, le=2000)
    volume: float = Field(1, ge=0, le=4)
    fade_in_ms: int = Field(0, ge=0, le=2000)
    fade_out_ms: int = Field(0, ge=0, le=2000)
    color: ColorAdjustment | None = None

    @model_validator(mode="after")
    def check_range(self):
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("shot out point must follow in point")
        if self.transition_in == TransitionType.CUT and self.transition_in_ms:
            raise ValueError("cut transition duration must be zero")
        return self


class ManualCaption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check_timing(self):
        if self.end <= self.start:
            raise ValueError("caption end must follow its start")
        if not self.text.strip():
            raise ValueError("caption text must not be blank")
        return self


class ManualTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    shots: tuple[ManualShot, ...] = Field(min_length=1, max_length=100)
    music_volume: float | None = Field(None, ge=0, le=4)
    music_ducking: bool | None = None
    captions: tuple[ManualCaption, ...] | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def check_captions(self):
        if self.captions is not None and any(
            later.start < earlier.start for earlier, later in zip(self.captions, self.captions[1:])
        ):
            raise ValueError("captions must be ordered by start time")
        return self


def apply_manual_timeline(base: TimelineIR, manual: ManualTimeline,
                          source_durations_ms: dict[str, int],
                          clip_sources: dict[str, tuple[str, bool, tuple[int, int, int, int] | None]],
                          window_crops: dict[int, tuple[int, int, int, int] | None] | None = None) -> TimelineIR:
    """Build an ordered edit using only authorized clips and a validated IR."""
    templates = {segment.clip_id: segment for segment in base.video_segments}
    segments: list[VideoSegmentIR] = []
    cursor = 0
    for index, shot in enumerate(manual.shots):
        if shot.clip_id not in clip_sources or shot.clip_id not in source_durations_ms:
            raise ValueError(f"clip {shot.clip_id} is unavailable in this project")
        if shot.source_out_ms > source_durations_ms[shot.clip_id]:
            raise ValueError(f"clip {shot.clip_id} out point exceeds source duration")
        duration = shot.source_out_ms - shot.source_in_ms
        if shot.fade_in_ms + shot.fade_out_ms > duration:
            raise ValueError("shot fades exceed the shot duration")
        if shot.transition_in_ms and (index == 0 or shot.transition_in_ms * 3 > duration
                                      or shot.transition_in_ms * 3 > segments[-1].timeline_duration_ms):
            raise ValueError("transition must fit both adjacent shots")
        path, has_audio, crop = clip_sources[shot.clip_id]
        prior = templates.get(shot.clip_id)
        segments.append(VideoSegmentIR(
            segment_id=f"manual_{index:03d}_{shot.clip_id}", clip_id=shot.clip_id,
            source_path=path, source_in_ms=shot.source_in_ms, source_out_ms=shot.source_out_ms,
            source_crop=(window_crops or {}).get(index, crop),
            timeline_start_ms=cursor, timeline_end_ms=cursor + duration,
            has_audio=has_audio, volume=shot.volume,
            fade_in_ms=shot.fade_in_ms, fade_out_ms=shot.fade_out_ms,
            transition_in=shot.transition_in, transition_in_ms=shot.transition_in_ms,
            color=shot.color or (prior.color if prior else None),
            narrative_role=prior.narrative_role if prior else None,
            act_id=prior.act_id if prior else None,
            emotional_stage=prior.emotional_stage if prior else None,
        ))
        cursor += duration
    if cursor > 600_000:
        raise ValueError("manual edit exceeds the 10 minute project limit")
    if manual.captions is not None:
        rendered_duration_s = (cursor - sum(shot.transition_in_ms for shot in manual.shots)) / 1000
        if any(caption.end > rendered_duration_s for caption in manual.captions):
            raise ValueError("caption timing exceeds the edited video duration")
    audio = tuple(revalidate(track, timeline_end_ms=min(cursor, track.timeline_end_ms),
                             volume=manual.music_volume if manual.music_volume is not None else track.volume,
                             ducking=manual.music_ducking if manual.music_ducking is not None else track.ducking)
                  for track in base.audio_segments if track.timeline_start_ms < cursor)
    captions = (tuple(CaptionCueIR(start_ms=round(c.start * 1000), end_ms=round(c.end * 1000),
                                   text=c.text, origin="user_override") for c in manual.captions)
                if manual.captions is not None else ())
    edited = revalidate(base, video_segments=tuple(segments), audio_segments=audio,
                        caption_cues=captions, total_duration_ms=cursor)
    EditPlanValidator().validate(edited, source_durations_ms=source_durations_ms).raise_if_invalid()
    return edited
