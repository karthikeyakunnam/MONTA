"""
MONTA — Media Gateway: Storage
================================
Layer 2: File storage abstraction.
"""


class StorageService:
    """Abstraction over file storage (local, S3, GCS)."""

    def __init__(self, storage_type: str = "local", base_path: str = "./storage"):
        self.storage_type = storage_type
        self.base_path = base_path

    async def store(self, file_path: str, destination: str) -> str:
        """Store a file and return its storage URL/path."""
        # TODO: Implement local storage
        # TODO: Add S3/GCS support
        return destination

    async def retrieve(self, storage_path: str) -> str:
        """Retrieve a file from storage."""
        # TODO: Return local path or download from cloud
        return storage_path

    async def delete(self, storage_path: str):
        """Delete a file from storage."""
        # TODO: Remove file
        pass

    async def generate_thumbnail(self, video_path: str) -> str:
        """Generate a thumbnail from the first frame."""
        # TODO: Use FFmpeg to extract first frame
        return ""
