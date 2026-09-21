"""
MONTA — Project Model
=======================
"""

from sqlalchemy import Column, String, ForeignKey, DateTime, JSON, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.db.base import Base

class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    UPLOADING = "uploading"
    ANALYZING = "analyzing"
    EDITING = "editing"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    status = Column(Enum(ProjectStatus), default=ProjectStatus.DRAFT)
    
    raw_prompt = Column(String, nullable=True)
    parsed_intent = Column(JSON, nullable=True)
    context = Column(JSON, nullable=True)
    target_platform = Column(String, default="instagram")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="projects")
    clips = relationship("Clip", back_populates="project")
    timeline = relationship("Timeline", back_populates="project", uselist=False)
    renders = relationship("Render", back_populates="project")
