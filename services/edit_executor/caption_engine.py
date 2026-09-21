"""
MONTA — Caption Engine
========================
Generates and renders captions on video using Whisper transcription.
"""


class CaptionEngine:
    """Generate and burn-in captions."""

    async def transcribe(self, video_path: str) -> list:
        """Transcribe audio from video using Whisper."""
        # TODO: Use Whisper for transcription
        return []  # List of {text, start, end}

    async def style_captions(self, captions: list, style: str) -> list:
        """Apply visual style to captions (bold, minimal, casual, impact)."""
        # TODO: Map style to font, size, position, animation
        return captions

    async def burn_captions(self, video_path: str, captions: list, output_path: str) -> str:
        """Render captions onto video."""
        # TODO: Use FFmpeg drawtext or ASS subtitles
        return output_path
