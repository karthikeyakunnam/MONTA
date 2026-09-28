"""
MONTA API — Prompt Endpoints
==============================
Layer 3: Prompt Intelligence Engine — interpret free-form editing requests.

Requires the repository root on PYTHONPATH (``services`` and ``shared`` are
top-level packages; the Docker images copy them alongside ``backend``).
"""

from functools import lru_cache

from fastapi import APIRouter, Depends

from app.schemas.prompt import PromptAnalysisRequest, PromptAnalysisResponse
from services.prompt_engine import IntentEngine
from shared.providers.registry import build_providers

router = APIRouter()


@lru_cache(maxsize=1)
def get_intent_engine() -> IntentEngine:
    """Process-wide engine; providers hold pooled HTTP clients and must be shared."""
    return IntentEngine(build_providers().text)


@router.post("/analyze", response_model=PromptAnalysisResponse)
async def analyze_prompt(request: PromptAnalysisRequest, engine: IntentEngine = Depends(get_intent_engine)):
    """
    Interpret a natural-language editing request. Any phrasing is accepted —
    slang, typos and fragments included.

    Example input:
        "make this feel like nike ad slow start then huge motivation ending
         use dark colors and aggressive cuts"

    The response carries every field with value, confidence, reasoning and
    evidence, plus detected ambiguities, conflicts and missing information
    (with clarifying questions the UI may surface).
    """
    intent = await engine.analyze(request.prompt)
    return PromptAnalysisResponse(project_id=request.project_id, intent=intent,
                                  needs_clarification=intent.needs_clarification)
