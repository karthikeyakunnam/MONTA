"""
MONTA API — Prompt Endpoints
==============================
Layer 3: Prompt Intelligence Engine — Process natural language editing prompts.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PromptRequest(BaseModel):
    """User's natural language editing prompt."""
    prompt: str
    project_id: str
    target_platform: str = "instagram"


@router.post("/analyze")
async def analyze_prompt(request: PromptRequest):
    """
    Parse a natural language prompt into structured editing intent.
    
    Example input:
        "make this feel like a dark cinematic transformation story,
         slow beginning, emotional middle, aggressive ending,
         orange-teal grade, dramatic bass music"
    
    Example output:
        {
            "genre": "transformation",
            "pacing": "dynamic",
            "color": "orange_teal",
            "emotion": "motivational",
            "target": "instagram"
        }
    """
    # TODO: Send to prompt intelligence engine
    # TODO: Use LLM to parse intent
    return {
        "status": "analyzed",
        "intent": {
            "genre": "placeholder",
            "pacing": "dynamic",
            "color": "default",
            "emotion": "neutral",
            "target": request.target_platform,
        }
    }
