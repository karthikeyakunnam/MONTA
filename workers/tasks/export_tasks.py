"""
MONTA — Export Tasks
======================
Background tasks for platform-specific video export.
"""

from workers.celery_app import app


@app.task(bind=True, name="tasks.export_for_platform")
def export_for_platform(self, render_id: str, platform: str):
    """Re-encode rendered video for a specific platform."""
    # TODO: Apply platform-specific settings
    # TODO: Resize/crop for aspect ratio
    # TODO: Enforce duration limits
    return {"render_id": render_id, "platform": platform, "export_path": ""}
