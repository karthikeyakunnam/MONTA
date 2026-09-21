"""
MONTA — Media Gateway: Metadata Extraction
=============================================
Layer 2: Extracts video metadata using FFprobe.

Output format:
{
    "fps": 30,
    "resolution": "1920x1080",
    "duration": 183,
    "clip_id": "clip_17"
}
"""

import subprocess
import json


class MetadataExtractor:
    """Extracts technical metadata from video files."""

    async def extract(self, file_path: str) -> dict:
        """Extract metadata using FFprobe."""
        # TODO: Run ffprobe command
        # cmd = [
        #     "ffprobe", "-v", "quiet", "-print_format", "json",
        #     "-show_format", "-show_streams", file_path
        # ]
        return {
            "fps": 0,
            "resolution": "unknown",
            "duration": 0,
            "codec": "unknown",
            "bitrate": 0,
            "audio_codec": "unknown",
            "audio_sample_rate": 0,
        }

    def parse_resolution(self, width: int, height: int) -> str:
        """Format resolution string."""
        return f"{width}x{height}"

    def is_4k(self, width: int) -> bool:
        """Check if resolution is 4K."""
        return width >= 3840
