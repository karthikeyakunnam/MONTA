"""
MONTA Backend — Application Configuration
==========================================
Centralized settings loaded from environment variables.
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # General
    APP_ENV: str = "development"
    APP_NAME: str = "monta"
    SECRET_KEY: str = "change-me-in-production"
    DEBUG: bool = True

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

    # Storage
    UPLOAD_DIR: str = "./storage/uploads"
    RENDER_DIR: str = "./storage/renders"
    MAX_UPLOAD_SIZE_MB: int = 500

    # Video Processing
    MAX_CLIP_DURATION_SECONDS: int = 240
    SUPPORTED_FORMATS: str = "mp4,mov"
    MAX_CLIPS_1080P: int = 20
    MAX_CLIPS_4K: int = 4

    # Vision
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # Whisper
    WHISPER_MODEL_SIZE: str = "large-v3"

    # Render
    RENDER_RESOLUTIONS: str = "720p,1080p,4k"
    DEFAULT_RENDER_FORMAT: str = "mp4"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


settings = Settings()
