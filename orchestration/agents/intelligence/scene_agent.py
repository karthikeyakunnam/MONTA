"""
MONTA — Scene Agent
=====================
Detects: people, gym, cars, travel, speaking.
"""


class SceneAgent:
    """Analyzes video frames for scene classification."""

    SCENE_LABELS = [
        "people", "gym", "outdoor", "indoor", "cars", "travel",
        "speaking", "sports", "nature", "urban", "food", "studio",
    ]

    def __init__(self, vision_model=None):
        self.vision_model = vision_model
        # TODO: Initialize Qwen-VL or Gemini Vision

    async def analyze(self, clip_path: str) -> dict:
        """Detect scenes in a video clip."""
        # TODO: Sample frames and classify
        return {"scenes": [], "confidence": {}}
