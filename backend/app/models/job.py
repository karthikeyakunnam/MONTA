"""
MONTA — Job Model
===================
A queued unit of work handed to the Celery worker. The row is written *before*
the task is submitted, so a submission that fails mid-flight is still visible
and retryable instead of vanishing.

``id`` is the tracking id returned to the client; ``task_id`` is the broker's
own id, kept for operators.
"""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class JobKind(str, enum.Enum):
    PIPELINE = "pipeline"   # Layers 3–7 for one project


class JobState(str, enum.Enum):
    PENDING = "pending"        # row written, not yet handed to the broker
    QUEUED = "queued"          # broker accepted it
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUBMIT_FAILED = "submit_failed"   # the broker was unreachable


TERMINAL_STATES = frozenset({JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.SUBMIT_FAILED.value})


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_project_created", "project_id", "created_at"),
                      Index("ix_jobs_state", "state"))

    id = Column(String(64), primary_key=True)
    project_id = Column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(16), nullable=False, default=JobKind.PIPELINE.value)
    state = Column(String(16), nullable=False, default=JobState.PENDING.value)
    stage = Column(String(32), nullable=True)   # last stage the worker reported
    task_id = Column(String(64), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    trace_id = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="jobs")
