"""
MONTA — Clip Model
====================
Represents an uploaded video clip with metadata and analysis results.
"""

from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Clip(Base):
    __tablename__ = "clips"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    
    # File metadata (Layer 2 — Media Gateway output)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    format = Column(String, nullable=False)  # mp4, mov
    fps = Column(Float, nullable=True)
    resolution = Column(String, nullable=True)  # "1920x1080"
    duration = Column(Float, nullable=True)  # seconds
    file_size_bytes = Column(Integer, nullable=True)

    # Analysis results (Layer 6 — Video Intelligence output)
    scene_tags = Column(JSON, nullable=True)     # ["gym", "outdoor", "people"]
    actions = Column(JSON, nullable=True)         # ["bench_press", "running"]
    quality_score = Column(Float, nullable=True)  # 0-10
    quality_details = Column(JSON, nullable=True) # {"lighting": 8, "stability": 9, ...}
    emotion_tags = Column(JSON, nullable=True)    # ["hype", "victory"]
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    project = relationship("Project", back_populates="clips")
