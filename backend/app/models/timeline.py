"""
MONTA — Timeline Model
========================
Stores the generated edit timeline (Layer 9 output).
"""

from sqlalchemy import Column, String, ForeignKey, DateTime, JSON, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Timeline(Base):
    __tablename__ = "timelines"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, unique=True)
    
    # Story structure (Layer 7 output)
    story_acts = Column(JSON, nullable=True)
    # e.g. [{"act": 1, "theme": "struggle", "clips": [...]}, ...]
    
    # Style config (Layer 8 output)
    style_config = Column(JSON, nullable=True)
    # e.g. {"cut_speed": "fast", "zoom": "aggressive", "music": "epic"}
    
    # Timeline entries (Layer 9 output)
    entries = Column(JSON, nullable=True)
    # e.g. [{"clip_id": "clip_12", "start": 0.0, "end": 2.1, "transition": "cut"}, ...]
    
    total_duration = Column(Float, nullable=True)
    
    # Critic scores (Layer 12 output)
    critic_score = Column(Float, nullable=True)
    critic_feedback = Column(JSON, nullable=True)
    revision_count = Column(Float, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="timeline")
