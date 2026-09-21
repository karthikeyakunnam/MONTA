"""MONTA — OpenCV Scene Detection."""


class SceneDetector:
    """Detect scene changes in video using OpenCV."""

    async def detect_cuts(self, video_path: str, threshold: float = 30.0) -> list:
        """Detect scene cuts based on frame difference."""
        # TODO: Frame-by-frame difference analysis
        return []  # List of timestamps
