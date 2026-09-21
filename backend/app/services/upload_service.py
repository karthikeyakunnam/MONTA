"""
MONTA — Upload Service
========================
Handles file upload, validation, and metadata extraction.
Connects to Layer 2 (Media Gateway).
"""

import os
from typing import Optional

from app.config import settings


class UploadService:
    """Service for handling video uploads."""

    SUPPORTED_FORMATS = {"mp4", "mov"}
    MAX_CLIP_DURATION = settings.MAX_CLIP_DURATION_SECONDS  # 240 seconds = 4 min
    MAX_CLIPS_1080P = settings.MAX_CLIPS_1080P  # 20
    MAX_CLIPS_4K = settings.MAX_CLIPS_4K  # 4

    async def validate_file(self, filename: str, file_size: int) -> Optional[str]:
        """Validate uploaded file format and size."""
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_FORMATS:
            return f"Unsupported format: {ext}. Supported: {self.SUPPORTED_FORMATS}"
        if file_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            return f"File too large. Max: {settings.MAX_UPLOAD_SIZE_MB}MB"
        return None

    async def extract_metadata(self, file_path: str) -> dict:
        """
        Extract video metadata using FFprobe.
        
        Returns:
            {
                "fps": 30,
                "resolution": "1920x1080",
                "duration": 183,
                "clip_id": "clip_17"
            }
        """
        # TODO: Use ffprobe to extract metadata
        # TODO: Dispatch to media_gateway service
        return {
            "fps": 0,
            "resolution": "unknown",
            "duration": 0,
            "clip_id": "",
        }

    async def store_file(self, file_path: str, clip_id: str) -> str:
        """Store uploaded file and return storage path."""
        storage_dir = os.path.join(settings.UPLOAD_DIR, clip_id)
        os.makedirs(storage_dir, exist_ok=True)
        # TODO: Move file to storage
        # TODO: Generate thumbnail
        return storage_dir
