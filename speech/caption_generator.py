"""MONTA — Caption Generator from Whisper output."""


class CaptionGenerator:
    """Generate formatted captions from transcription."""

    async def generate(self, segments: list, style: str = "minimal") -> list:
        """Generate styled captions from transcription segments."""
        # TODO: Format captions based on style (bold, minimal, etc.)
        return []

    async def to_srt(self, captions: list) -> str:
        """Convert captions to SRT format."""
        # TODO: Format as SRT
        return ""

    async def to_ass(self, captions: list, style: dict = None) -> str:
        """Convert captions to ASS format with styling."""
        # TODO: Format as ASS with visual effects
        return ""
