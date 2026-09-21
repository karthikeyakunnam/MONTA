"""
MONTA API — Project Endpoints
===============================
Manage video editing projects.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/")
async def create_project():
    """Create a new video editing project."""
    # TODO: Create project in DB
    return {"project_id": "proj_placeholder", "status": "created"}


@router.get("/{project_id}")
async def get_project(project_id: str):
    """Retrieve project details."""
    # TODO: Fetch from DB
    return {"project_id": project_id}


@router.get("/")
async def list_projects():
    """List all projects for the current user."""
    # TODO: Query user's projects
    return {"projects": []}


@router.put("/{project_id}")
async def update_project(project_id: str):
    """Update project settings."""
    # TODO: Update in DB
    return {"project_id": project_id, "status": "updated"}


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """Delete a project."""
    # TODO: Soft delete
    return {"project_id": project_id, "status": "deleted"}
