"""
MONTA — Upload Tasks
======================
Background tasks for video upload processing.
"""

from workers.celery_app import app


@app.task(bind=True, name="tasks.process_upload")
def process_upload(self, clip_id: str, file_path: str):
    """Process an uploaded video: validate, extract metadata, store."""
    self.update_state(state="PROCESSING", meta={"step": "validating"})
    # TODO: Validate video format and constraints
    # TODO: Extract metadata with ffprobe
    # TODO: Generate thumbnail
    # TODO: Update DB with metadata
    return {"clip_id": clip_id, "status": "processed"}


@app.task(bind=True, name="tasks.extract_metadata")
def extract_metadata(self, clip_id: str, file_path: str):
    """Extract video metadata using FFprobe."""
    # TODO: Run ffprobe and parse output
    return {
        "fps": 30,
        "resolution": "1920x1080",
        "duration": 0,
        "clip_id": clip_id,
    }
