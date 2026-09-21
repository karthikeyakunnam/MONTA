"""
MONTA API — Upload Endpoints
==============================
Layer 2: Media Gateway — Upload and validate video files.
"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import List

router = APIRouter()


@router.post("/video")
async def upload_video(file: UploadFile = File(...)):
    """
    Upload a video file for processing.
    
    Validates:
    - Format: mp4, mov
    - Max clip length: 4 minutes
    - Resolution limits: 1080p → 20 clips, 4K → 4 clips
    """
    # TODO: Validate file format
    # TODO: Extract metadata (fps, resolution, duration)
    # TODO: Store file
    # TODO: Trigger async analysis task
    return {
        "status": "uploaded",
        "filename": file.filename,
        "clip_id": "clip_placeholder",
    }


@router.post("/batch")
async def upload_batch(files: List[UploadFile] = File(...)):
    """Upload multiple video files at once."""
    # TODO: Validate batch size against resolution limits
    # TODO: Process each file
    return {"status": "batch_uploaded", "count": len(files)}


@router.get("/status/{clip_id}")
async def upload_status(clip_id: str):
    """Check the processing status of an uploaded clip."""
    # TODO: Query processing status from Redis/DB
    return {"clip_id": clip_id, "status": "processing"}
