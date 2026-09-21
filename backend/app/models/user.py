"""
MONTA — User Model
====================
"""

from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    projects = relationship("Project", back_populates="user")

    # User preferences (stored in Qdrant for vector search)
    preferred_style = Column(String, nullable=True)  # cinematic, vlog, hype
    preferred_color_grade = Column(String, nullable=True)  # orange_teal, bw, vibrant
