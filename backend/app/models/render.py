"""
MONTA — Render Model
======================
Tracks render jobs and their output (Layer 13).
"""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, JSON, String
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
    __table_args__ = (Index("ix_renders_project_created", "project_id", "created_at"),)

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    
    status = Column(String(16), nullable=False, default=RenderStatus.QUEUED.value)
    resolution = Column(String(16), nullable=False)
    format = Column(String(16), nullable=False, default="mp4")
    
    # Output
    output_path = Column(String(512), nullable=True)
    file_size_bytes = Column(Float, nullable=True)
    duration = Column(Float, nullable=True)
    
    # Progress
    progress_percent = Column(Float, default=0.0)
    celery_task_id = Column(String(64), nullable=True)
    
    # Platform export
    platform = Column(String(32), nullable=True)
    platform_config = Column(JSON, nullable=True)
    
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    project = relationship("Project", back_populates="renders")
