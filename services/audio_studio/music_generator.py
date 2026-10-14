"""
MONTA Audio — Music Generator
================================
Generates high-quality synthetic background music beds matching requested
mood, genre, and duration using deterministic FFmpeg audio synthesis.
No cloud dependencies required — works 100% locally and offline.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monta.audio.music_generator")


# Synthesizer recipe definitions for different styles and moods
_STYLE_RECIPES = {
    "cinematic": {
        "bpm": 80,
        "description": "Rich orchestral strings and low brass swell",
        # Layer 1: Low drone, Layer 2: Minor chords (A minor: 220, 261, 330), Layer 3: Sub bass
        "filter": (
            "aevalsrc='0.15*sin(2*PI*110*t)+0.1*sin(2*PI*220*t)+0.08*sin(2*PI*261.63*t)+0.08*sin(2*PI*329.63*t)':s=48000:d={dur},"
            "lowpass=f=800,chorus=0.7:0.9:55:0.4:0.25:2,"
            "volume=0.8,aformat=sample_rates=48000:channel_layouts=stereo"
        ),
    },
    "lo_fi": {
        "bpm": 75,
        "description": "Warm nostalgic lo-fi electric piano chords with vinyl warmth",
        "filter": (
            "aevalsrc='0.12*sin(2*PI*174.61*t)+0.12*sin(2*PI*220*t)+0.12*sin(2*PI*261.63*t)+0.1*sin(2*PI*349.23*t)':s=48000:d={dur},"
            "lowpass=f=1200,highpass=f=100,flanger=delay=4:depth=1.5:regen=20:width=80:speed=0.3,"
            "volume=0.75,aformat=sample_rates=48000:channel_layouts=stereo"
        ),
    },
    "hype": {
        "bpm": 140,
        "description": "High-energy pulsing electronic bass and rhythmic drive",
        "filter": (
            "aevalsrc='0.25*sin(2*PI*55*t*(1+0.05*sin(2*PI*2.33*t)))+0.1*sin(2*PI*110*t)+0.05*sin(2*PI*440*t*mod(t*2.33,1))':s=48000:d={dur},"
            "lowpass=f=2000,volume=0.85,aformat=sample_rates=48000:channel_layouts=stereo"
        ),
    },
    "ambient": {
        "bpm": 60,
        "description": "Peaceful atmospheric evolving soundscape",
        "filter": (
            "aevalsrc='0.15*sin(2*PI*130.81*t)+0.12*sin(2*PI*196*t)+0.1*sin(2*PI*261.63*t)+0.08*sin(2*PI*392*t)':s=48000:d={dur},"
            "chorus=0.8:0.9:60:0.4:0.25:2,lowpass=f=1000,volume=0.7,aformat=sample_rates=48000:channel_layouts=stereo"
        ),
    },
    "intense": {
        "bpm": 130,
        "description": "Driving aggressive rhythmic tension",
        "filter": (
            "aevalsrc='0.3*sin(2*PI*65.41*t)+0.15*sin(2*PI*130.81*t)+0.1*sin(2*PI*196*t*(1+0.1*sin(2*PI*4.33*t)))':s=48000:d={dur},"
            "lowpass=f=1500,volume=0.85,aformat=sample_rates=48000:channel_layouts=stereo"
        ),
    },
    "default": {
        "bpm": 90,
        "description": "Warm balanced background music bed",
        "filter": (
            "aevalsrc='0.15*sin(2*PI*164.81*t)+0.12*sin(2*PI*220*t)+0.1*sin(2*PI*329.63*t)':s=48000:d={dur},"
            "lowpass=f=1500,chorus=0.7:0.8:50:0.4:0.25:2,volume=0.75,aformat=sample_rates=48000:channel_layouts=stereo"
        ),
    },
}

# Style preset aliases
_STYLE_RECIPES["orchestral"] = _STYLE_RECIPES["cinematic"]
_STYLE_RECIPES["epic"] = _STYLE_RECIPES["intense"]
_STYLE_RECIPES["bass_heavy"] = _STYLE_RECIPES["hype"]
_STYLE_RECIPES["beat_sync"] = _STYLE_RECIPES["hype"]
_STYLE_ALIASES = {
    "epic_orchestral": "cinematic",
    "hard_trap": "hype",
    "cinematic_trailer": "epic",
    "emotional_piano": "lo_fi",
    "hybrid_cinematic": "cinematic",
    "sports_anthem": "hype",
    "acoustic": "default",
    "electronic": "hype",
}



class MusicGenerator:
    """Generates synthetic background music beds matching requested mood and duration."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg", output_dir: Optional[str] = None):
        self.ffmpeg_bin = ffmpeg_bin
        self.output_dir = output_dir or tempfile.gettempdir()

    async def generate(
        self,
        mood: str = "cinematic",
        duration: float = 10.0,
        style: str = "cinematic",
        output_path: Optional[str] = None,
    ) -> str:
        """Generate music matching mood, style, and duration in seconds.

        Returns:
            Absolute file path to the generated AAC/M4A audio file.
        """
        duration = max(1.0, float(duration))
        key = mood.lower().replace("-", "_").replace(" ", "_")
        key = _STYLE_ALIASES.get(key, key)
        if key not in _STYLE_RECIPES:
            key = style.lower().replace("-", "_").replace(" ", "_")
        if key not in _STYLE_RECIPES:
            for k in _STYLE_RECIPES:
                if k in key:
                    key = k
                    break
            else:
                key = "default"

        recipe = _STYLE_RECIPES.get(key, _STYLE_RECIPES["default"])
        filter_str = recipe["filter"].format(dur=duration)

        if not output_path:
            out_file = Path(self.output_dir) / f"bg_music_{key}_{int(duration)}s_{os.getpid()}.m4a"
            out_path = str(out_file)
        else:
            out_path = str(Path(output_path).resolve())

        # Construct safe FFmpeg invocation array (never execute raw shell strings)
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-v", "error",
            "-f", "lavfi",
            "-i", filter_str,
            "-t", f"{duration:.3f}",
            "-c:a", "aac",
            "-b:a", "192k",
            out_path,
        ]

        logger.info("Generating synthetic %s music bed (%.1fs) -> %s", key, duration, out_path)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode().strip()
            logger.error("FFmpeg music generation failed: %s", err_msg)
            raise RuntimeError(f"FFmpeg music generation failed: {err_msg}")

        return out_path
