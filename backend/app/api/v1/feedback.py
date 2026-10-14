"""
MONTA API v1 — User Feedback & Learning Endpoints (M7)
======================================================
Endpoints for:
- Submitting edit ratings and feedback
- Querying learned preferences
- Retrieving liked examples for retrieval-augmented generation
- Resetting personalization data (GDPR Right to be Forgotten)
- Exporting recorded user data
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Header
from pydantic import BaseModel, Field

from memory.feedback_loop import FeedbackLoop

logger = logging.getLogger("monta.api.v1.feedback")

router = APIRouter()
_feedback_loop = FeedbackLoop()


class FeedbackSubmission(BaseModel):
    project_id: str
    edit_id: str
    liked: bool
    rating: float = Field(0.0, ge=0.0, le=10.0)
    tags: Optional[List[str]] = None
    comment: Optional[str] = ""
    model_used: Optional[str] = ""
    style: Optional[str] = ""
    prompt_summary: Optional[str] = ""
    project_type: Optional[str] = None
    story_pattern: Optional[str] = None
    pace: Optional[str] = None
    emotion: Optional[str] = None


@router.post("/")
async def submit_feedback(
    body: FeedbackSubmission,
    x_monta_user: str = Header("local", alias="X-MONTA-User"),
):
    """Submit user feedback for an edit."""
    try:
        record = await _feedback_loop.process_feedback(
            user_id=x_monta_user,
            project_id=body.project_id,
            edit_id=body.edit_id,
            liked=body.liked,
            rating=body.rating,
            tags=body.tags,
            comment=body.comment or "",
            model_used=body.model_used or "",
            style=body.style or "",
            prompt_summary=body.prompt_summary or "",
            project_type=body.project_type,
            story_pattern=body.story_pattern,
            pace=body.pace,
            emotion=body.emotion,
        )
        return {
            "status": "recorded",
            "dedup_key": record.dedup_key,
            "timestamp": record.timestamp,
        }
    except Exception as e:
        logger.error("Failed to process feedback: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preferences")
async def get_preferences(
    x_monta_user: str = Header("local", alias="X-MONTA-User"),
):
    """Get aggregated preferences learned for the requesting user."""
    try:
        prefs = await _feedback_loop.get_user_preferences(x_monta_user)
        return {"user_id": x_monta_user, "preferences": prefs}
    except Exception as e:
        logger.error("Failed to retrieve preferences: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/liked")
async def get_liked(
    limit: int = Query(10, ge=1, le=50),
    x_monta_user: str = Header("local", alias="X-MONTA-User"),
):
    """Get liked edit examples for the user."""
    try:
        examples = await _feedback_loop.get_liked_examples(x_monta_user, limit=limit)
        return {"user_id": x_monta_user, "liked_examples": examples}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
async def reset_preferences(
    x_monta_user: str = Header("local", alias="X-MONTA-User"),
):
    """Delete all recorded feedback for this user (privacy reset)."""
    try:
        deleted = await _feedback_loop.reset_user(x_monta_user)
        return {"user_id": x_monta_user, "reset": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_data(
    x_monta_user: str = Header("local", alias="X-MONTA-User"),
):
    """Export all user data (GDPR compliance)."""
    try:
        data = await _feedback_loop.export_user_data(x_monta_user)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
