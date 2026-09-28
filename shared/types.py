"""MONTA — Shared Type Definitions.

Layer 3–7 contracts (intent, clips, context, director, story) live in
``shared.contracts`` as validated pydantic models.
"""

from typing import TypedDict


class TimelineEntry(TypedDict):
    clip_id: str
    start: float
    end: float
    transition: str
