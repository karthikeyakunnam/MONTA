"""
MONTA — Whisper Transcriber
=============================
Audio and speech transcription using local OpenAI Whisper.
Runs offline with local weights. Whisper chooses CUDA when available and CPU otherwise.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("monta.speech.transcriber")


class LocalWhisperModelUnavailable(RuntimeError):
    """The requested ASR weights are absent from local storage."""


class LocalWhisperModelCorrupt(RuntimeError):
    """The local checkpoint cannot be read as a Whisper model."""


class LocalWhisperModelIncompatible(RuntimeError):
    """The checkpoint needs a different Whisper or PyTorch version."""


class LocalTranscriptionFailed(RuntimeError):
    """Inference failed after a compatible model was loaded."""


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

    def model_path(self) -> Path:
        """Resolve a local checkpoint without asking Whisper to download anything."""
        try:
            import whisper
        except ImportError as exc:
            raise LocalWhisperModelIncompatible(
                "The openai-whisper package is not installed in the worker environment."
            ) from exc

        if self.model_size in whisper._MODELS:
            cache_root = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "whisper"
            return cache_root / Path(urlparse(whisper._MODELS[self.model_size]).path).name
        return Path(self.model_size).expanduser().resolve()

    def available_model_path(self) -> Path:
        model_file = self.model_path()
        if not model_file.is_file():
            raise LocalWhisperModelUnavailable(
                f"Local Whisper '{self.model_size}' weights are unavailable at {model_file}; "
                "speech transcription and sentence-aware cuts are unavailable. "
                "Install the model weights explicitly; MONTA will not download them during a render."
            )
        if model_file.stat().st_size == 0:
            raise LocalWhisperModelCorrupt(f"Whisper checkpoint is empty: {model_file}")
        return model_file

    def _get_model(self):
        """Lazy loader for a verified local path only."""
        if self._model is None:
            import whisper

            model_file = self.available_model_path()
            logger.info("Loading local Whisper '%s' model from %s", self.model_size, model_file)
            try:
                self._model = whisper.load_model(str(model_file))
            except (ImportError, ModuleNotFoundError) as exc:
                raise LocalWhisperModelIncompatible(
                    f"Whisper checkpoint {model_file} needs an unavailable Python dependency: {exc}"
                ) from exc
            except (KeyError, AttributeError, TypeError, ValueError) as exc:
                raise LocalWhisperModelIncompatible(
                    f"Whisper checkpoint {model_file} is incompatible with installed Whisper/PyTorch: {exc}"
                ) from exc
            except (OSError, EOFError, RuntimeError) as exc:
                raise LocalWhisperModelCorrupt(
                    f"Whisper checkpoint {model_file} cannot be loaded: {exc}"
                ) from exc
            logger.info("Whisper '%s' model loaded successfully.", self.model_size)
        return self._model

    async def extract_audio_from_video(self, video_path: str, output_path: Optional[str] = None) -> str:
        """Extract a 16kHz mono WAV audio track from video suitable for Whisper."""
        video_file = Path(video_path).resolve()
        if not video_file.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        created_temp = not output_path
        if created_temp:
            with tempfile.NamedTemporaryFile(prefix="monta_asr_", suffix=".wav", delete=False) as handle:
                output_path = handle.name

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
            if created_temp:
                Path(output_path).unlink(missing_ok=True)
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
                options = {"task": "transcribe", "word_timestamps": True}
                if language:
                    options["language"] = language
                return model.transcribe(str(audio_file), **options)

            try:
                result = await loop.run_in_executor(None, _run)
            except Exception as exc:
                raise LocalTranscriptionFailed(f"Local Whisper inference failed for {audio_file}: {exc}") from exc

        try:
            segments = []
            for s in result.get("segments", []):
                start, end = float(s["start"]), float(s["end"])
                if not all(math.isfinite(value) for value in (start, end)) or start < 0:
                    raise ValueError("non-finite or negative segment timestamp")
                if end <= start or not str(s.get("text", "")).strip():
                    continue
                words = []
                for word in s.get("words") or []:
                    word_start, word_end = float(word["start"]), float(word["end"])
                    if (not all(math.isfinite(value) for value in (word_start, word_end))
                            or word_start < 0 or word_end < word_start):
                        raise ValueError("invalid word timestamp")
                    if word_end > word_start:
                        words.append({"start": word_start, "end": word_end,
                                      "text": str(word.get("word", "")).strip()})
                segments.append({"id": s.get("id", len(segments)), "start": start,
                                 "end": end, "text": str(s["text"]).strip(), "words": words})
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalTranscriptionFailed(
                f"Local Whisper produced malformed timestamps for {audio_file}: {exc}"
            ) from exc

        return {
            "text": result.get("text", "").strip(),
            "language": result.get("language"),
            "segments": segments,
        }

    async def transcribe_video_cached(self, video_path: str, cache_dir: str | Path,
                                      language: Optional[str] = None) -> dict[str, Any]:
        """Cache source-relative ASR by source bytes, model identity and language."""
        import whisper

        model_file = self.available_model_path()
        source = Path(video_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Source media not found: {source}")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        model_stat = model_file.stat()
        identity = json.dumps({"source_sha256": digest.hexdigest(), "model": str(model_file),
                               "model_size": model_stat.st_size, "model_mtime_ns": model_stat.st_mtime_ns,
                               "whisper_version": getattr(whisper, "__version__", None),
                               "language": language, "word_timestamps": True}, sort_keys=True)
        cache_key = hashlib.sha256(identity.encode()).hexdigest()
        cache_root = Path(cache_dir)
        cache_file = cache_root / f"{cache_key}.json"
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text())
                if cached.get("provenance", {}).get("identity") == identity:
                    return cached
            except (OSError, ValueError):
                logger.warning("Ignoring unreadable ASR cache %s", cache_file)
        result = await self.transcribe_video(str(source), language=language)
        result["provenance"] = {"identity": identity, "source_path": str(source),
                                "source_sha256": digest.hexdigest(), "model_path": str(model_file),
                                "model": self.model_size, "whisper_version": getattr(whisper, "__version__", None),
                                "time_base": "source_seconds"}
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_root,
                                         prefix="asr_", suffix=".tmp", delete=False) as handle:
            json.dump(result, handle)
            temporary = Path(handle.name)
        temporary.replace(cache_file)
        return result

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
