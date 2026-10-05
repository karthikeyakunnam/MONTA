"""
MONTA Backend — Application Configuration
==========================================
Centralized settings loaded from environment variables.
"""

from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings

#: Repository root: backend/app/config.py -> backend/app -> backend -> <root>
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # General
    APP_ENV: str = "development"
    APP_NAME: str = "monta"
    SECRET_KEY: str = "change-me-in-production"
    DEBUG: bool = True

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug_environment(cls, value):
        """Tolerate common process-level DEBUG values without breaking startup.

        Deployment shells often set ``DEBUG=release``/``production`` for tools
        unrelated to MONTA.  Treat those as a normal false value instead of
        preventing every API or test process from importing its settings.
        """
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "production", "prod", "off"}:
                return False
            if normalized in {"development", "dev", "debug", "on"}:
                return True
        return value

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://monta_user:monta_pass@localhost:5432/monta"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Qdrant
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "monta_memory"

    # Storage (Layer 2). STORAGE_ROOT holds uploads/ and .incoming/.
    STORAGE_ROOT: str = "./storage"
    RENDER_DIR: str = "./storage/renders"
    MAX_UPLOAD_SIZE_MB: int = 500
    MAX_REQUEST_SIZE_MB: int = 4096

    # Media tools (Layer 2 uses ffprobe only; the worker uses ffmpeg too)
    FFPROBE_PATH: str = "ffprobe"
    FFMPEG_PATH: str = "ffmpeg"
    PROBE_TIMEOUT_SECONDS: float = 30.0
    # Ceiling on concurrent ffprobe processes. 0 means "derive it from the core count".
    PROBE_MAX_CONCURRENCY: int = 0

    # Video Processing
    MAX_CLIP_DURATION_SECONDS: int = 240
    SUPPORTED_FORMATS: str = "mp4,mov,mkv"
    MAX_CLIPS_1080P: int = 20
    MAX_MINUTES_1080P: float = 10.0
    MAX_CLIPS_4K: int = 4
    MAX_MINUTES_4K: float = 4.0

    # Queue (Layer 2 → worker). The backend submits by task name and never imports worker code.
    QUEUE_ENABLED: bool = True
    PIPELINE_TASK_NAME: str = "tasks.run_pipeline"
    QUEUE_SUBMIT_TIMEOUT_SECONDS: float = 5.0

    # Progress fan-out (worker → Redis pub/sub → websocket)
    PROGRESS_CHANNEL_PREFIX: str = "monta:progress"
    PROGRESS_HISTORY_KEY_PREFIX: str = "monta:progress:history"
    PROGRESS_HISTORY_LENGTH: int = 50

    # Identity. Real auth is not built yet: the API accepts an X-MONTA-User header and
    # falls back to this id, so rows are always owned by a real user record.
    DEFAULT_USER_ID: str = "local"
    DEFAULT_USER_EMAIL: str = "local@monta.test"

    # Vision
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # Whisper
    WHISPER_MODEL_SIZE: str = "large-v3"

    # Render
    RENDER_RESOLUTIONS: str = "720p,1080p,4k"
    DEFAULT_RENDER_FORMAT: str = "mp4"

    @property
    def storage_root_path(self) -> Path:
        """Absolute storage root.

        A relative ``STORAGE_ROOT`` is resolved against the repository root, never the current
        working directory. The API runs from ``backend/`` and the Celery worker from the repo
        root: with cwd-relative resolution the two processes pointed at *different*
        directories, so every file the API stored was invisible to the worker that had to read
        it. One anchor, one storage tree.
        """
        root = Path(self.STORAGE_ROOT).expanduser()
        return root if root.is_absolute() else (REPO_ROOT / root).resolve()

    @property
    def render_dir_path(self) -> Path:
        """Absolute render output directory, anchored the same way as the storage root."""
        path = Path(self.RENDER_DIR).expanduser()
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    model_config = {
        # Absolute, so the app loads the same configuration whether uvicorn was started from
        # the repo root, from backend/, or from a container's WORKDIR. A relative "./.env"
        # made the effective configuration depend on the launch directory.
        "env_file": str(REPO_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        # One .env serves the backend, the workers and the orchestration layer, so it contains
        # keys this model does not declare (POSTGRES_*, QDRANT_*, MONTA_TEXT_PROVIDERS, …).
        # Forbidding extras made the backend refuse to start — 39 validation errors — the
        # moment a real .env was present. Unknown keys belong to another layer; ignore them.
        "extra": "ignore",
    }


settings = Settings()
