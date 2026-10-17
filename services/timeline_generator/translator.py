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
        audio_segments: Optional[Sequence[AudioSegmentIR]] = None,
        segment_crops: dict[int, tuple[int, int, int, int]] | None = None,
    ) -> TimelineIR:
        """Deterministically builds TimelineIR from story plan decisions and source media."""
        # 1. Determine Aspect Ratio and Resolution from target platform
        platform = pack.platform.platform if pack.platform else Platform.TIKTOK
        is_vertical = platform in (Platform.TIKTOK, Platform.INSTAGRAM, Platform.YOUTUBE_SHORT)

        aspect_choices = {
            "9:16": (AspectRatio.PORTRAIT_9_16, 1080, 1920),
            "16:9": (AspectRatio.LANDSCAPE_16_9, 1920, 1080),
            "1:1": (AspectRatio.SQUARE_1_1, 1080, 1080),
            "4:5": (AspectRatio.PORTRAIT_4_5, 1080, 1350),
        }
        chosen_aspect = (style_override or {}).get("aspect_ratio")
        if chosen_aspect in aspect_choices:
            aspect_ratio, width, height = aspect_choices[chosen_aspect]
        elif is_vertical:
            aspect_ratio, width, height = AspectRatio.PORTRAIT_9_16, 1080, 1920
        else:
            aspect_ratio, width, height = AspectRatio.LANDSCAPE_16_9, 1920, 1080

        # 2. Build Clip Path Lookup Table
        clip_paths: dict[str, str] = {}
        for meta in pack.video_metadata:
            clip_paths[meta.clip_id] = meta.path

        # 3. Resolve unified style from StyleMapper (Layer 8)
        from services.style_engine.mapper import StyleMapper
        resolved_style = StyleMapper.resolve_style_for_pack(pack, style_override)

        color_adj = StyleMapper.resolve_color_adjustment(resolved_style.get("color_grade"))
        grade_values = color_adj.model_dump(mode="python") if color_adj else ColorAdjustment().model_dump(mode="python")
        explicit_color = {key: resolved_style[key] for key in (
            "brightness", "contrast", "saturation", "gamma", "shadow_coolness", "highlight_warmth", "vignette"
        ) if key in resolved_style}
        if resolved_style.get("shadow_coolness") is not None:
            explicit_color["shadow_coolness"] = resolved_style["shadow_coolness"]
        if explicit_color:
            grade_values.update(explicit_color)
        color_adj = ColorAdjustment.model_validate(grade_values)
        scale_mode = StyleMapper.resolve_scale_mode(resolved_style.get("scale_mode"))
        act_ids = list(dict.fromkeys(cut.act_id for cut in story.timeline))
        act_rationales = {act.act_id: act for act in story.reasoning.acts}

        # 4. Translate the *actual* ordered StoryPlan cuts.
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

            trans_type, trans_ms = cls._transition_for(resolved_style, i, output_duration_ms)
            fade_in_ms = 250 if i == 0 else 0
            fade_out_ms = 250 if i == len(story.timeline) - 1 else 0

            segment_color = color_adj
            brief = pack.intent.creative_brief
            if (
                i == len(story.timeline) - 1
                and resolved_style.get("ending_highlight_warmth")
                and not explicit_color.get("highlight_warmth")
            ):
                segment_color = ColorAdjustment.model_validate({
                    **color_adj.model_dump(mode="python"),
                    "highlight_warmth": resolved_style["ending_highlight_warmth"],
                })
            act_index = act_ids.index(cut.act_id) if cut.act_id in act_ids else 0
            act_count = len(act_ids)
            emotional_stage = "opening" if act_index == 0 else (
                "resolution" if act_index == act_count - 1 else "development"
            )
            rationale = act_rationales.get(cut.act_id)
            seg_ir = TimelineSegmentIR(
                segment_id=f"seg_{cut.index:03d}_{cut.clip_id}",
                clip_id=cut.clip_id,
                source_path=source_path,
                source_in_ms=source_in_ms,
                source_out_ms=source_out_ms,
                source_crop=(segment_crops or {}).get(cut.index, metadata.content_crop),
                timeline_start_ms=seg_start_ms,
                timeline_end_ms=seg_end_ms,
                speed=round(source_duration_ms / output_duration_ms, 4),
                has_audio=metadata.has_audio,
                volume=1.0,
                fade_in_ms=fade_in_ms,
                fade_out_ms=fade_out_ms,
                transition_in=trans_type,
                transition_in_ms=trans_ms,
                color=segment_color,
                is_hero=cut.is_hero,
                act_id=cut.act_id,
                narrative_role=cut.purpose or (rationale.purpose if rationale else None),
                emotional_stage=emotional_stage,
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
            scale_mode=scale_mode,
            video_segments=tuple(video_segments),
            audio_segments=tuple(audio_segments or ()),
            render_profile=render_profile or RenderProfileIR(),
            total_duration_ms=total_duration_ms,
        )

    @classmethod
    def _resolve_color_adjustment(
        cls,
        pack: ContextPack,
        style_override: Optional[dict] = None,
    ) -> Optional[ColorAdjustment]:
        from services.style_engine.mapper import StyleMapper
        resolved = StyleMapper.resolve_style_for_pack(pack, style_override)
        return StyleMapper.resolve_color_adjustment(resolved.get("color_grade"))

    @staticmethod
    def _transition_for(style_override: Optional[dict], index: int, duration_ms: int) -> tuple[TransitionType, int]:
        """Map an explicit style directive; StoryPlan itself otherwise means cut."""
        if index == 0 or not style_override:
            return TransitionType.CUT, 0
        from services.style_engine.mapper import StyleMapper
        raw = str(style_override.get("transitions", style_override.get("transition", "cut"))).lower()
        trans_ms = int(style_override.get("transition_ms", 250))
        trans_type, t_dur = StyleMapper.resolve_transition(raw, default_ms=trans_ms)
        if trans_type == TransitionType.CUT:
            return TransitionType.CUT, 0
        return trans_type, max(1, min(t_dur, duration_ms // 3))
