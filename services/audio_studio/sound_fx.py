"""
MONTA Audio — Sound Effects
=============================
Generates and mixes transition and impact sound effects using local FFmpeg
audio synthesis. 100% offline and deterministic.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monta.audio.sound_fx")

_FX_RECIPES = {
    "whoosh": {
        "duration": 0.8,
        "filter": "anoisesrc=d=0.8:c=pink:r=48000,bandpass=f=900:width_type=q:w=1.8,afade=t=in:ss=0:d=0.3,afade=t=out:st=0.4:d=0.4,volume=0.9",
    },
    "impact": {
        "duration": 1.2,
        "filter": "aevalsrc='0.8*sin(2*PI*(140-100*t)*t)*exp(-6*t)':s=48000:d=1.2,lowpass=f=300,volume=1.0",
    },
    "bass_drop": {
        "duration": 2.0,
        "filter": "aevalsrc='0.9*sin(2*PI*(130-45*t)*t)*exp(-1.5*t)':s=48000:d=2.0,lowpass=f=180,volume=1.0",
    },
    "riser": {
        "duration": 2.5,
        "filter": "aevalsrc='0.6*sin(2*PI*(80+250*t*t)*t)':s=48000:d=2.5,afade=t=in:ss=0:d=2.0,volume=0.85",
    },
    "chime": {
        "duration": 1.8,
        "filter": "aevalsrc='0.25*sin(2*PI*1046.5*t)+0.2*sin(2*PI*1318.5*t)+0.2*sin(2*PI*1567.98*t)':s=48000:d=1.8,afade=t=out:st=0.2:d=1.6,volume=0.8",
    },
    "glitch": {
        "duration": 0.5,
        "filter": "aevalsrc='0.4*sgn(sin(2*PI*350*t))*mod(t*25,1)':s=48000:d=0.5,lowpass=f=3000,volume=0.7",
    },
}


class SoundFX:
    """Generates and manages sound effects for video transitions and accents."""

    FX_LIBRARY = list(_FX_RECIPES.keys())

    def __init__(self, ffmpeg_bin: str = "ffmpeg", output_dir: Optional[str] = None):
        self.ffmpeg_bin = ffmpeg_bin
        self.output_dir = output_dir or tempfile.gettempdir()

    async def generate_fx(
        self,
        fx_type: str = "whoosh",
        output_path: Optional[str] = None,
    ) -> str:
        """Generate a sound effect audio file.

        Args:
            fx_type: One of 'whoosh', 'impact', 'bass_drop', 'riser', 'chime', 'glitch'
            output_path: Optional output path.

        Returns:
            Path to the generated audio file.
        """
        fx_key = fx_type.lower().strip()
        recipe = _FX_RECIPES.get(fx_key, _FX_RECIPES["whoosh"])
        dur = recipe["duration"]
        filter_str = recipe["filter"]

        if not output_path:
            out_file = Path(self.output_dir) / f"sfx_{fx_key}_{os.getpid()}.m4a"
            out_path = str(out_file)
        else:
            out_path = str(Path(output_path).resolve())

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-v", "error",
            "-f", "lavfi",
            "-i", filter_str,
            "-t", f"{dur:.3f}",
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
            raise RuntimeError(f"FFmpeg SFX generation failed: {err}")

        return out_path
