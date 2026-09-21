"""
MONTA API — Timeline Endpoints
================================
Layer 9: Timeline Generator — Generate and manage edit timelines.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/generate")
async def generate_timeline(project_id: str):
    """
    Generate an edit timeline for a project.
    
    This triggers the full pipeline:
    Layer 5 (Director) → Layer 6 (Intelligence) → Layer 7 (Story) →
    Layer 8 (Style) → Layer 9 (Timeline)
    """
    # TODO: Trigger LangGraph orchestration
    return {"project_id": project_id, "status": "generating"}


@router.get("/{project_id}")
async def get_timeline(project_id: str):
    """Retrieve the generated timeline for a project."""
    # TODO: Fetch timeline from DB
    return {"project_id": project_id, "timeline": []}


@router.put("/{project_id}")
async def update_timeline(project_id: str):
    """User adjustments to the generated timeline."""
    # TODO: Apply user edits
    return {"project_id": project_id, "status": "updated"}
