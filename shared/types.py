"""MONTA — Shared Type Definitions."""

from typing import TypedDict, List, Optional


class ClipMetadata(TypedDict):
    fps: float
    resolution: str
    duration: float
    clip_id: str
    format: str


class EditingIntent(TypedDict):
    genre: str
    pacing: str
    color: str
    emotion: str
    target: str


class TimelineEntry(TypedDict):
    clip_id: str
    start: float
    end: float
    transition: str


class StoryAct(TypedDict):
    act_number: int
    theme: str
    clips: List[str]
    mood: str
