"""
MONTA — Qwen-VL Model
========================
Multimodal vision-language model for scene/action understanding.
"""


class QwenVLModel:
    """Wrapper around Qwen-VL for video understanding."""

    def __init__(self, model_path: str = None):
        self.model = None
        self.processor = None
        # TODO: Load Qwen-VL model and processor

    async def analyze_frame(self, frame, prompt: str) -> str:
        """Analyze a single frame with a text prompt."""
        # TODO: Run inference
        return ""

    async def analyze_video(self, video_path: str, prompt: str, sample_rate: int = 1) -> list:
        """Analyze video by sampling frames."""
        # TODO: Sample frames and run inference on each
        return []
