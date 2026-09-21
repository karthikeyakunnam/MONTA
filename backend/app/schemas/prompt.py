"""
MONTA — Prompt Schemas
========================
Pydantic models for prompt intelligence (Layer 3).
"""

from pydantic import BaseModel
from typing import Optional


class EditingIntent(BaseModel):
    """Structured editing intent parsed from natural language prompt."""
    genre: str  # transformation, vlog, tutorial, cinematic
    pacing: str  # slow, dynamic, fast, aggressive
    color: str  # orange_teal, bw, vibrant, dark, natural
    emotion: str  # motivational, calm, aggressive, sad, hype
    target: str  # instagram, youtube_short, tiktok, general


class PromptAnalysisRequest(BaseModel):
    """Request to analyze a natural language editing prompt."""
    prompt: str
    project_id: str
    target_platform: str = "instagram"


class PromptAnalysisResponse(BaseModel):
    """Response from prompt analysis."""
    raw_prompt: str
    intent: EditingIntent
    confidence: float = 0.0
    suggestions: Optional[list] = None
