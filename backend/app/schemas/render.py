"""
MONTA — Render Schemas
========================
Pydantic models for render jobs (Layer 13).
"""

from pydantic import BaseModel
from typing import List, Optional


class RenderRequest(BaseModel):
    """Request to start a render job."""
    project_id: str
    resolutions: List[str] = ["1080p"]
    format: str = "mp4"
    platform: Optional[str] = None


class RenderProgress(BaseModel):
    """Render progress update."""
    render_id: str
    status: str
    progress_percent: float
    current_step: str  # "encoding", "color_grading", "audio_sync"
    eta_seconds: Optional[float] = None


class RenderComplete(BaseModel):
    """Completed render output."""
    render_id: str
    output_url: str
    resolution: str
    format: str
    duration: float
    file_size_mb: float
