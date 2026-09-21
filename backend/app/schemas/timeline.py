"""
MONTA — Timeline Schemas
==========================
Pydantic models for timeline data (Layer 9).
"""

from pydantic import BaseModel
from typing import List, Optional


class TimelineEntry(BaseModel):
    """Single entry in the edit timeline."""
    clip_id: str
    start: float  # seconds
    end: float
    transition: str = "cut"  # cut, fade, dissolve, zoom
    effects: Optional[List[str]] = None


class StoryAct(BaseModel):
    """A story act in the narrative structure (Layer 7)."""
    act_number: int
    theme: str  # struggle, training, growth, result
    clip_ids: List[str]
    mood: str


class TimelineResponse(BaseModel):
    """Complete timeline with story and style info."""
    project_id: str
    story_acts: List[StoryAct]
    entries: List[TimelineEntry]
    total_duration: float
    style_config: dict
