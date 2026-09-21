"""
MONTA — Gemini Vision Client
================================
Google Gemini API client for visual analysis.
"""


class GeminiVisionClient:
    """Client for Google Gemini Vision API."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
        # TODO: Initialize google.generativeai client

    async def analyze_image(self, image_path: str, prompt: str) -> str:
        """Analyze an image with Gemini."""
        # TODO: Call Gemini API
        return ""

    async def analyze_video(self, video_path: str, prompt: str) -> str:
        """Analyze a video with Gemini."""
        # TODO: Upload video to Gemini and analyze
        return ""
