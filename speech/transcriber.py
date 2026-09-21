"""
MONTA — Whisper Transcriber
==============================
Audio transcription using OpenAI Whisper.
"""


class WhisperTranscriber:
    """Transcribe audio/video using Whisper."""

    def __init__(self, model_size: str = "large-v3"):
        self.model_size = model_size
        self.model = None
        # TODO: Load Whisper model

    async def transcribe(self, audio_path: str, language: str = None) -> dict:
        """Transcribe audio file."""
        # TODO: Run Whisper inference
        return {"text": "", "segments": [], "language": ""}

    async def transcribe_video(self, video_path: str) -> dict:
        """Extract audio from video and transcribe."""
        # TODO: Extract audio with FFmpeg, then transcribe
        return {"text": "", "segments": []}
