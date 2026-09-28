"""
MONTA — Project Model
=======================
A project owns the prompt, the uploaded clips, the jobs run for it and the
resulting timeline and renders.

``status`` mirrors the states the UI shows. It is a ``String`` column validated
by ``ProjectStatus`` so new states ship without a database enum migration.
"""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"                    # created, no clips yet
    UPLOADING = "uploading"            # at least one clip stored
    QUEUED = "queued"                  # pipeline job accepted by the queue
    ANALYZING = "analyzing"            # Layer 6 running
    STORY_BUILDING = "story_building"  # Layer 7 running
    RENDERING = "rendering"            # Layers 8–13
    COMPLETE = "complete"
    FAILED = "failed"


# States the API refuses to start a new pipeline from (one run at a time per project).
ACTIVE_STATUSES = frozenset({ProjectStatus.QUEUED.value, ProjectStatus.ANALYZING.value,
                             ProjectStatus.STORY_BUILDING.value, ProjectStatus.RENDERING.value})


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_user_created", "user_id", "created_at"),)

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=ProjectStatus.DRAFT.value)
    status_detail = Column(Text, nullable=True)

    raw_prompt = Column(Text, nullable=True)
    parsed_intent = Column(JSON, nullable=True)
    context = Column(JSON, nullable=True)
    story_plan = Column(JSON, nullable=True)
    story_judgement = Column(JSON, nullable=True)
    target_platform = Column(String(32), nullable=False, default="instagram")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="projects")
    clips = relationship("Clip", back_populates="project", cascade="all, delete-orphan")
    jobs = relationship("Job", back_populates="project", cascade="all, delete-orphan")
    timeline = relationship("Timeline", back_populates="project", uselist=False, cascade="all, delete-orphan")
    renders = relationship("Render", back_populates="project", cascade="all, delete-orphan")
