"""
MONTA — Prompt Schemas
========================
API schemas for prompt intelligence (Layer 3). The intent itself is the shared
``IntentAnalysis`` contract so the API, pipeline and Critic see one definition.
"""

from pydantic import BaseModel, Field

from shared.contracts.intent import IntentAnalysis


class PromptAnalysisRequest(BaseModel):
    """A creator's editing request, in their own words."""

    prompt: str = Field(..., max_length=4000)
    project_id: str = Field(..., min_length=1, max_length=64)


class PromptAnalysisResponse(BaseModel):
    project_id: str
    intent: IntentAnalysis
    needs_clarification: bool
