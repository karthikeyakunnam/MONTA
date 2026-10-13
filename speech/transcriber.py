"""
MONTA — Whisper Transcriber
=============================
Audio and speech transcription using local OpenAI Whisper.
Runs 100% offline and locally on CPU or Apple Silicon MPS.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("monta.speech.transcriber")


class WhisperTranscriber:
    """Transcribe audio/video using local Whisper model."""

    def __init__(self, model_size: str = "base", ffmpeg_bin: str = "ffmpeg"):
        """
        Args:
            model_size: Whisper model size ('tiny', 'base', 'small', 'medium', 'large-v3').
                        Defaults to 'base' for fast local inference.
            ffmpeg_bin: Path to FFmpeg executable.
        """
        self.model_size = model_size
        self.ffmpeg_bin = ffmpeg_bin
        self._model = None
        self._lock = asyncio.Lock()

    def _get_model(self):
        """Lazy loader for the Whisper model."""
        if self._model is None:
            import whisper
            logger.info("Loading Whisper '%s' model...", self.model_size)
            self._model = whisper.load_model(self.model_size)
            logger.info("Whisper '%s' model loaded successfully.", self.model_size)
        return self._model

    async def extract_audio_from_video(self, video_path: str, output_path: Optional[str] = None) -> str:
        """Extract a 16kHz mono WAV audio track from video suitable for Whisper."""
        video_file = Path(video_path).resolve()
        if not video_file.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if not output_path:
            out_file = Path(tempfile.gettempdir()) / f"whisper_audio_{video_file.stem}_{os.getpid()}.wav"
            output_path = str(out_file)

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-v", "error",
            "-i", str(video_file),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise RuntimeError(f"FFmpeg audio extraction failed: {err}")

        return str(output_path)

    async def transcribe(self, audio_path: str, language: Optional[str] = None) -> dict[str, Any]:
        """Transcribe an audio file using Whisper.

        Returns:
            Dict containing:
                - 'text': Full transcript string
                - 'language': Detected language code (e.g. 'en')
                - 'segments': List of segment dicts with 'start', 'end', 'text'
        """
        audio_file = Path(audio_path).resolve()
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        async with self._lock:
            loop = asyncio.get_running_loop()
            model = self._get_model()

            def _run():
                options = {"task": "transcribe"}
                if language:
                    options["language"] = language
                return model.transcribe(str(audio_file), **options)

            result = await loop.run_in_executor(None, _run)

        segments = []
        for s in result.get("segments", []):
            segments.append({
                "id": s.get("id", len(segments)),
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": s.get("text", "").strip(),
            })

        return {
            "text": result.get("text", "").strip(),
            "language": result.get("language", "en"),
            "segments": segments,
        }

    async def transcribe_video(self, video_path: str, language: Optional[str] = None) -> dict[str, Any]:
        """Extract audio from video and transcribe it."""
        wav_path = await self.extract_audio_from_video(video_path)
        try:
            return await self.transcribe(wav_path, language=language)
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
