"""
MONTA — Clip Model
====================
One uploaded clip: what the Media Gateway (Layer 2) proved about the file, plus
the analysis Layer 6 later attaches.

Status is a ``String`` column validated by the ``ClipStatus`` enum rather than a
database enum type: adding a state must not require an ``ALTER TYPE`` migration
on a live table.
"""

import enum

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ClipStatus(str, enum.Enum):
    UPLOADED = "uploaded"      # bytes stored, metadata proven, quota accepted
    ANALYZING = "analyzing"    # Layer 6 is working on it
    ANALYZED = "analyzed"      # ClipIntelligence attached
    REJECTED = "rejected"      # validation refused it (kept for the UI, no bytes on disk)
    FAILED = "failed"          # analysis failed after retries


class Clip(Base):
    __tablename__ = "clips"
    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_clips_project_sha256"),
        Index("ix_clips_project_created", "project_id", "created_at"),
        Index("ix_clips_sha256", "sha256"),
    )

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(16), nullable=False, default=ClipStatus.UPLOADED.value)

    # --- Layer 2: file facts ---
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    storage_key = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False)
    duplicate_of = Column(String(64), ForeignKey("clips.id", ondelete="SET NULL"), nullable=True)
    container = Column(String(64), nullable=True)
    format = Column(String(16), nullable=False)
    video_codec = Column(String(32), nullable=True)
    fps = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    resolution = Column(String(16), nullable=True)
    resolution_class = Column(String(8), nullable=True)
    duration = Column(Float, nullable=True)
    bitrate = Column(Integer, nullable=True)
    file_size_bytes = Column(Integer, nullable=False, default=0)
    has_audio = Column(Boolean, nullable=False, default=False)
    audio_streams = Column(JSON, nullable=True)
    rejection_issues = Column(JSON, nullable=True)

    # --- Layer 6: analysis ---
    scene_tags = Column(JSON, nullable=True)
    actions = Column(JSON, nullable=True)
    quality_score = Column(Float, nullable=True)
    quality_details = Column(JSON, nullable=True)
    emotion_tags = Column(JSON, nullable=True)
    intelligence = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    project = relationship("Project", back_populates="clips")
