"""
MONTA — Render Model
======================
Tracks render jobs and their output (Layer 13).
"""

from sqlalchemy import Column, String, Float, ForeignKey, DateTime, JSON, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class RenderStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Render(Base):
    __tablename__ = "renders"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    
    status = Column(Enum(RenderStatus), default=RenderStatus.QUEUED)
    resolution = Column(String, nullable=False)  # 720p, 1080p, 4k
    format = Column(String, default="mp4")
    
    # Output
    output_path = Column(String, nullable=True)
    file_size_bytes = Column(Float, nullable=True)
    duration = Column(Float, nullable=True)
    
    # Progress
    progress_percent = Column(Float, default=0.0)
    celery_task_id = Column(String, nullable=True)
    
    # Platform export
    platform = Column(String, nullable=True)  # instagram, youtube_short, tiktok
    platform_config = Column(JSON, nullable=True)
    
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    project = relationship("Project", back_populates="renders")
