"""Timeline artifact endpoints.

Timeline construction is deliberately owned by the queued project pipeline. These
endpoints expose its persisted, validated artifact; they never claim to generate a
timeline without running the rest of M3.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_user_id, get_db
from app.models import Project, Timeline

router = APIRouter()


@router.post("/generate", status_code=status.HTTP_409_CONFLICT)
async def generate_timeline() -> None:
    """Reject the retired stub endpoint instead of reporting fake generation."""
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "Timeline generation runs as part of POST /projects/{project_id}/pipeline.",
    )


@router.get("/{project_id}")
async def get_timeline(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(current_user_id),
) -> dict:
    """Return the actual Story-to-Timeline artifact and render trace for its owner."""
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    timeline = (await db.execute(
        select(Timeline).where(Timeline.project_id == project_id)
    )).scalar_one_or_none()
    if timeline is None or timeline.entries is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No generated timeline for this project")
    return {
        "project_id": project_id,
        "timeline": timeline.entries,
        "story_acts": timeline.story_acts,
        "trace": timeline.style_config,
        "total_duration_s": timeline.total_duration,
        "created_at": timeline.created_at,
        "updated_at": timeline.updated_at,
    }
