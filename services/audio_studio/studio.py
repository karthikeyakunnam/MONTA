"""
MONTA — Audio Studio (Layer 10)
================================
Authoritative audio production engine for background music generation,
speech-aware ducking, track fades, tempo matching, and accent sound effects.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from services.audio_studio.music_generator import MusicGenerator
from services.audio_studio.sound_fx import SoundFX
from services.audio_studio.tempo_matcher import TempoMatcher
from shared.contracts.timeline import AudioSegmentIR, TimelineIR, TransitionType

logger = logging.getLogger("monta.audio.studio")


class AudioStudio:
    """Layer 10: Audio Studio engine.

    Orchestrates music generation, ducking configuration, fades, and SFX
    into structured AudioSegmentIR tracks ready for deterministic FFmpeg compilation.
    """

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        output_dir: Optional[str] = None,
    ):
        self.ffmpeg_bin = ffmpeg_bin
        self.output_dir = output_dir or tempfile.gettempdir()
        self.music_generator = MusicGenerator(ffmpeg_bin=ffmpeg_bin, output_dir=self.output_dir)
        self.sound_fx = SoundFX(ffmpeg_bin=ffmpeg_bin, output_dir=self.output_dir)
        self.tempo_matcher = TempoMatcher(ffmpeg_bin=ffmpeg_bin, output_dir=self.output_dir)

    async def create_soundtrack(
        self,
        timeline: TimelineIR,
        style: str = "cinematic",
        enable_ducking: bool = True,
        music_volume: float = 0.35,
        fade_in_ms: int = 1000,
        fade_out_ms: int = 1500,
        add_sfx_on_hero: bool = True,
    ) -> tuple[AudioSegmentIR, ...]:
        """Creates complete background soundtrack with speech-aware ducking and sound effects.

        Args:
            timeline: The video timeline IR to score.
            style: Music style/genre (e.g. 'cinematic', 'lo_fi', 'hype', 'ambient', 'intense').
            enable_ducking: Automatically duck music when video dialogue/audio is active.
            music_volume: Base volume multiplier (0.0 to 1.0).
            fade_in_ms: Music fade-in duration in milliseconds.
            fade_out_ms: Music fade-out duration in milliseconds.
            add_sfx_on_hero: Whether to place transition/impact SFX on hero cuts.

        Returns:
            Tuple of AudioSegmentIR tracks ready to attach to the TimelineIR.
        """
        total_duration_s = max(1.0, timeline.total_duration_ms / 1000.0)

        # 1. Determine if foreground video contains audio
        has_foreground_audio = any(seg.has_audio for seg in timeline.video_segments)
        ducking = enable_ducking and has_foreground_audio

        # If no foreground audio, music can sit at normal loudness (e.g. 0.70)
        # If foreground audio is present, base music volume sits at softer level (e.g. 0.35)
        effective_vol = music_volume if has_foreground_audio else min(1.0, music_volume * 2.0)

        # 2. Generate or select background music bed
        music_path = await self.music_generator.generate(
            mood=style,
            duration=total_duration_s,
            style=style,
        )

        audio_tracks: list[AudioSegmentIR] = []

        bg_track = AudioSegmentIR(
            track_id="bg_music_main",
            source_path=music_path,
            source_in_ms=0,
            source_out_ms=timeline.total_duration_ms,
            timeline_start_ms=0,
            timeline_end_ms=timeline.total_duration_ms,
            volume=effective_vol,
            fade_in_ms=fade_in_ms,
            fade_out_ms=fade_out_ms,
            loop=True,
            ducking=ducking,
            duck_threshold=0.08,
            duck_ratio=4.0,
            duck_attack_ms=50,
            duck_release_ms=300,
        )
        audio_tracks.append(bg_track)

        # 3. Add transition / hero sound effects if requested
        if add_sfx_on_hero:
            for seg in timeline.video_segments:
                if seg.is_hero and seg.timeline_start_ms > 0:
                    try:
                        impact_path = await self.sound_fx.generate_fx("impact")
                        impact_end = min(
                            timeline.total_duration_ms,
                            seg.timeline_start_ms + 1200,
                        )
                        sfx_track = AudioSegmentIR(
                            track_id=f"sfx_impact_{seg.segment_id}",
                            source_path=impact_path,
                            source_in_ms=0,
                            timeline_start_ms=seg.timeline_start_ms,
                            timeline_end_ms=impact_end,
                            volume=0.6,
                            fade_in_ms=50,
                            fade_out_ms=200,
                            ducking=False,
                        )
                        audio_tracks.append(sfx_track)
                    except Exception as e:
                        logger.warning("Failed to generate hero SFX: %s", e)

        logger.info(
            "Created %d audio tracks for project '%s' (style=%s, ducking=%s)",
            len(audio_tracks),
            timeline.project_id,
            style,
            ducking,
        )
        return tuple(audio_tracks)
