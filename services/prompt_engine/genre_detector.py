"""MONTA — Genre Detector."""


class GenreDetector:
    """Detects video genre from prompt context."""

    async def detect(self, prompt: str, clip_analysis: dict = None) -> str:
        """Detect genre from prompt and optional clip analysis."""
        # TODO: LLM genre detection
        return "cinematic"
