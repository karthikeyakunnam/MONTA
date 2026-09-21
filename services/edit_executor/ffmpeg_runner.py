"""
MONTA — FFmpeg Runner
=======================
Wrapper around FFmpeg for video processing operations.
"""

import subprocess
from typing import List, Optional


class FFmpegRunner:
    """Execute FFmpeg commands for video editing."""

    def __init__(self, threads: int = 4):
        self.threads = threads

    async def cut_clip(self, input_path: str, start: float, end: float, output_path: str) -> str:
        """Cut a clip to specified time range."""
        # TODO: Run ffmpeg -ss {start} -to {end} -i {input} -c copy {output}
        return output_path

    async def concat_clips(self, clip_paths: List[str], output_path: str) -> str:
        """Concatenate multiple clips into one."""
        # TODO: Create concat file and run ffmpeg
        return output_path

    async def apply_transition(self, clip_a: str, clip_b: str, transition: str, output: str) -> str:
        """Apply a transition between two clips."""
        # TODO: Use ffmpeg xfade filter
        return output

    async def add_audio(self, video_path: str, audio_path: str, output_path: str) -> str:
        """Mux audio onto video."""
        # TODO: ffmpeg -i video -i audio -c:v copy -c:a aac output
        return output_path

    async def apply_lut(self, input_path: str, lut_path: str, output_path: str) -> str:
        """Apply a color LUT to a video."""
        # TODO: ffmpeg -i input -vf lut3d=lut_path output
        return output_path

    async def resize(self, input_path: str, width: int, height: int, output_path: str) -> str:
        """Resize video to specified dimensions."""
        # TODO: ffmpeg scale filter
        return output_path
