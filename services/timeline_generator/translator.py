"""
MONTA — Story Plan to Timeline IR Translator
=============================================
Authoritative bridge converting creative StoryPlan decisions (Layer 7) and ContextPack
into an executable, millisecond-accurate Timeline IR (Milestone 2).
"""

import logging
from typing import Optional

from shared.contracts.context import ContextPack
from shared.contracts.story import StoryPlan
from shared.contracts.timeline import (
    AspectRatio,
    AudioSegmentIR,
    ColorAdjustment,
    RenderProfileIR,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)
from shared.contracts.vocab import Platform

logger = logging.getLogger("monta.timeline.translator")


class StoryToTimelineTranslator:
    """Translates StoryPlan and ContextPack into TimelineIR."""

    @classmethod
    def translate(
        cls,
        story: StoryPlan,
        pack: ContextPack,
        style_override: Optional[dict] = None,
        render_profile: Optional[RenderProfileIR] = None,
    ) -> TimelineIR:
        """Deterministically builds TimelineIR from story plan decisions and source media."""
        # 1. Determine Aspect Ratio and Resolution from target platform
        platform = pack.platform.platform if pack.platform else Platform.TIKTOK
        is_vertical = platform in (Platform.TIKTOK, Platform.INSTAGRAM, Platform.YOUTUBE_SHORT)

        if is_vertical:
            aspect_ratio = AspectRatio.PORTRAIT_9_16
            width = 1080
            height = 1920
        else:
            aspect_ratio = AspectRatio.LANDSCAPE_16_9
            width = 1920
            height = 1080

        # 2. Build Clip Path Lookup Table
        clip_paths: dict[str, str] = {}
        for meta in pack.video_metadata:
            clip_paths[meta.clip_id] = meta.path

        # 3. Determine Color Mood from intent / style.
        color_adj = cls._resolve_color_adjustment(pack, style_override)

        # 4. Translate the *actual* ordered StoryPlan cuts.  TimelineSegment
        # carries both an output interval and a source interval.  Keeping both
        # lets a future Story Architect express a speed ramp without inventing a
        # second StoryPlan shape: speed is source_duration / output_duration.
        video_segments: list[TimelineSegmentIR] = []
        previous_end_ms = 0

        metadata_by_id = {meta.clip_id: meta for meta in pack.video_metadata}
        for i, cut in enumerate(story.timeline):
            if cut.index != i:
                raise ValueError(f"StoryPlan timeline is not ordered: expected cut index {i}, got {cut.index}")
            source_path = clip_paths.get(cut.clip_id)
            if not source_path:
                raise ValueError(f"Source file path for clip '{cut.clip_id}' not found in ContextPack")
            metadata = metadata_by_id[cut.clip_id]

            # Convert the StoryPlan's explicit source and output ranges to
            # integer milliseconds.  Do not rebuild timing from source ranges:
            # that would silently discard a story-level pacing or speed decision.
            source_in_ms = int(round(cut.source_in * 1000))
            source_out_ms = int(round(cut.source_out * 1000))
            seg_start_ms = int(round(cut.start * 1000))
            seg_end_ms = int(round(cut.end * 1000))
            if seg_start_ms != previous_end_ms:
                raise ValueError(
                    f"StoryPlan has a gap or overlap before cut {cut.index}: "
                    f"expected {previous_end_ms}ms, got {seg_start_ms}ms"
                )
            source_duration_ms = source_out_ms - source_in_ms
            output_duration_ms = seg_end_ms - seg_start_ms
            if source_duration_ms <= 0 or output_duration_ms <= 0:
                raise ValueError(f"StoryPlan cut {cut.index} has an invalid source or output range")

            # StoryPlan does not currently carry transition directives.  A cut is
            # therefore the only truthful default; automatic crossfades would
            # change the architect's requested duration.  Style callers can pass
            # an explicit compatible transition later without a second plan type.
            trans_type, trans_ms = cls._transition_for(style_override, i, output_duration_ms)
            fade_in_ms = 250 if i == 0 else 0
            fade_out_ms = 250 if i == len(story.timeline) - 1 else 0

            seg_ir = TimelineSegmentIR(
                segment_id=f"seg_{cut.index:03d}_{cut.clip_id}",
                clip_id=cut.clip_id,
                source_path=source_path,
                source_in_ms=source_in_ms,
                source_out_ms=source_out_ms,
                timeline_start_ms=seg_start_ms,
                timeline_end_ms=seg_end_ms,
                speed=round(source_duration_ms / output_duration_ms, 4),
                has_audio=metadata.has_audio,
                volume=1.0,
                fade_in_ms=fade_in_ms,
                fade_out_ms=fade_out_ms,
                transition_in=trans_type,
                transition_in_ms=trans_ms,
                color=color_adj,
                is_hero=cut.is_hero,
            )
            video_segments.append(seg_ir)
            previous_end_ms = seg_end_ms

        total_duration_ms = int(round(story.total_duration_s * 1000))
        if total_duration_ms != previous_end_ms:
            raise ValueError(
                f"StoryPlan total duration ({total_duration_ms}ms) does not match its final cut ({previous_end_ms}ms)"
            )

        return TimelineIR(
            project_id=pack.project_id,
            width=width,
            height=height,
            fps=30.0,
            aspect_ratio=aspect_ratio,
            scale_mode=ScaleMode.FIT,
            video_segments=tuple(video_segments),
            render_profile=render_profile or RenderProfileIR(),
            total_duration_ms=total_duration_ms,
        )

    @classmethod
    def _resolve_color_adjustment(
        cls,
        pack: ContextPack,
        style_override: Optional[dict] = None,
    ) -> Optional[ColorAdjustment]:
        style = style_override or {}
        requested_grade = pack.intent.color_grade.value.value if pack.intent.color_grade else ""
        color_theme = str(style.get("color_grade", requested_grade)).lower()
        if "cinematic" in color_theme or "mood" in color_theme:
            return ColorAdjustment(brightness=-0.02, contrast=1.12, saturation=1.08)
        if "vibrant" in color_theme or "hype" in color_theme:
            return ColorAdjustment(brightness=0.02, contrast=1.15, saturation=1.20)
        return None

    @staticmethod
    def _transition_for(style_override: Optional[dict], index: int, duration_ms: int) -> tuple[TransitionType, int]:
        """Map an explicit style directive; StoryPlan itself otherwise means cut."""
        if index == 0 or not style_override:
            return TransitionType.CUT, 0
        raw = str(style_override.get("transition", "cut")).lower()
        try:
            transition = TransitionType(raw)
        except ValueError as exc:
            raise ValueError(f"Unsupported style transition '{raw}'") from exc
        if transition == TransitionType.CUT:
            return transition, 0
        requested_ms = int(style_override.get("transition_ms", 250))
        return transition, max(1, min(requested_ms, duration_ms // 3))
