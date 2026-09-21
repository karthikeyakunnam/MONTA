"""
MONTA — Media Gateway: Upload Handler
========================================
Layer 2: Handles file reception and initial processing.
"""

import os
import uuid
from typing import BinaryIO


class UploadHandler:
    """Handles incoming video file uploads."""

    def __init__(self, upload_dir: str = "./storage/uploads"):
        self.upload_dir = upload_dir
        os.makedirs(upload_dir, exist_ok=True)

    async def receive_file(self, file: BinaryIO, filename: str) -> dict:
        """Receive and store an uploaded file."""
        clip_id = f"clip_{uuid.uuid4().hex[:8]}"
        ext = filename.rsplit(".", 1)[-1].lower()
        dest_path = os.path.join(self.upload_dir, clip_id, f"original.{ext}")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # TODO: Stream file to disk
        # TODO: Calculate checksum

        return {
            "clip_id": clip_id,
            "file_path": dest_path,
            "filename": filename,
        }
