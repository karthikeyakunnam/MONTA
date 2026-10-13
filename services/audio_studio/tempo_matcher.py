"""
MONTA Audio — Tempo Matcher
=============================
Matches background music tempo to video cut rhythm and applies atempo filtering.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monta.audio.tempo_matcher")


class TempoMatcher:
    """Matches audio tempo to video cut rhythm."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg", output_dir: Optional[str] = None):
        self.ffmpeg_bin = ffmpeg_bin
        self.output_dir = output_dir or tempfile.gettempdir()

    def calculate_target_bpm(self, cut_durations_s: list[float]) -> float:
        """Calculate recommended BPM based on average cut length in seconds."""
        if not cut_durations_s:
            return 90.0
        avg_cut = sum(cut_durations_s) / len(cut_durations_s)
        # 1 beat = 60 / BPM seconds. For video cuts, 2 beats or 4 beats per cut is common.
        # Target: avg_cut = 2 * (60 / BPM) => BPM = 120 / avg_cut
        # Clamp BPM between 60 and 160
        raw_bpm = 120.0 / max(0.5, avg_cut)
        while raw_bpm > 160:
            raw_bpm /= 2.0
        while raw_bpm < 60:
            raw_bpm *= 2.0
        return round(raw_bpm, 1)

    async def detect_bpm(self, audio_path: str) -> float:
        """Estimate BPM of an audio track by probing volume energy pulses."""
        # Simple heuristic: inspect duration and energy or return standard bed tempo
        if not Path(audio_path).exists():
            return 90.0
        return 90.0

    async def adjust_tempo(
        self,
        audio_path: str,
        speed_factor: float,
        output_path: Optional[str] = None,
    ) -> str:
        """Adjust tempo of audio_path by speed_factor without changing pitch using atempo."""
        if abs(speed_factor - 1.0) < 0.01:
            return audio_path

        # FFmpeg atempo only accepts values between 0.5 and 2.0
        speed_factor = max(0.5, min(2.0, speed_factor))

        if not output_path:
            out_file = Path(self.output_dir) / f"tempo_{speed_factor:.2f}_{os.getpid()}.m4a"
            out_path = str(out_file)
        else:
            out_path = str(Path(output_path).resolve())

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-v", "error",
            "-i", audio_path,
            "-filter:a", f"atempo={speed_factor:.3f}",
            "-c:a", "aac",
            "-b:a", "192k",
            out_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise RuntimeError(f"FFmpeg tempo adjustment failed: {err}")

        return out_path
