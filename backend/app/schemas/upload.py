"""
MONTA — Upload Schemas
========================
Pydantic models for upload request/response validation.
"""

from pydantic import BaseModel
from typing import Optional, List


class ClipMetadata(BaseModel):
    """Metadata extracted from an uploaded clip (Layer 2 output)."""
    fps: float
    resolution: str  # "1920x1080"
    duration: float  # seconds
    clip_id: str
    format: str
    file_size_bytes: int


class UploadResponse(BaseModel):
    """Response after successful upload."""
    status: str
    clip_id: str
    metadata: Optional[ClipMetadata] = None


class BatchUploadResponse(BaseModel):
    """Response after batch upload."""
    status: str
    clips: List[UploadResponse]
    total_count: int


class ValidationError(BaseModel):
    """Upload validation error."""
    field: str
    message: str
    # e.g. {"field": "duration", "message": "Clip exceeds 4 minute limit"}
