"""
MONTA API v1 — Local Model Management Endpoints (M6)
=====================================================
Exposes model lifecycle via ModelManager:
- List models & capabilities (text vs vision)
- Download model & poll progress
- Delete model (with in-use protection)
- Disk usage reporting
- Hardware detection & recommendations
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from models.model_manager import (
    ModelManager,
    ModelInUseError,
    ModelNotFoundError,
    ModelDownloadError,
)
from shared.hardware.detector import HardwareDetector

logger = logging.getLogger("monta.api.v1.models")

router = APIRouter()
_manager = ModelManager()


class DownloadRequest(BaseModel):
    model_name: str


@router.get("/")
async def list_models():
    """List all installed local models with capabilities."""
    try:
        models = await _manager.list_models()
        return {
            "models": [
                {
                    "name": m.name,
                    "family": m.family,
                    "param_size": m.param_size,
                    "quantization": m.quantization,
                    "size_bytes": m.size_bytes,
                    "capabilities": [c.value for c in m.capabilities],
                }
                for m in models
            ],
            "available": await _manager.is_available(),
        }
    except Exception as e:
        logger.error("Failed to list models: %s", e)
        return {"models": [], "available": False, "error": str(e)}


@router.get("/capabilities/{model_name}")
async def get_capabilities(model_name: str):
    """Get detailed capability metadata for a model."""
    try:
        return await _manager.get_model_capabilities(model_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/download")
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Start background download of a model."""
    model_name = request.model_name
    progress = _manager.get_download_progress(model_name)
    if progress and progress.status == "downloading":
        return {"status": "already_downloading", "model_name": model_name}

    async def _do_download():
        try:
            await _manager.download_model(model_name)
        except Exception as e:
            logger.error("Background download failed for %s: %s", model_name, e)

    background_tasks.add_task(_do_download)
    return {"status": "started", "model_name": model_name}


@router.get("/download/{model_name}")
async def get_download_status(model_name: str):
    """Get status of an active or recent download."""
    progress = _manager.get_download_progress(model_name)
    if not progress:
        return {"status": "not_found", "model_name": model_name}
    return {
        "model_name": progress.model_name,
        "status": progress.status,
        "total_bytes": progress.total_bytes,
        "completed_bytes": progress.completed_bytes,
        "percent": progress.percent,
        "error": progress.error,
    }


@router.delete("/{model_name}")
async def delete_model(model_name: str):
    """Delete an installed model (blocked if currently in use)."""
    try:
        await _manager.delete_model(model_name)
        return {"deleted": True, "model_name": model_name}
    except ModelInUseError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/disk-usage")
async def get_disk_usage():
    """Get disk usage for installed models."""
    try:
        report = await _manager.disk_usage()
        return {
            "total_bytes": report.total_bytes,
            "total_gb": round(report.total_gb, 2),
            "model_sizes": report.model_sizes,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hardware")
async def get_hardware():
    """Get detected hardware info, supported acceleration, and model recommendations."""
    detector = HardwareDetector()
    profile = detector.detect()
    return {
        "hardware": {
            "device_type": profile.device_type.value,
            "os_name": profile.os_name,
            "cpu_arch": profile.cpu_arch,
            "cpu_cores_physical": profile.cpu_cores_physical,
            "cpu_cores_logical": profile.cpu_cores_logical,
            "ram_total_gb": profile.total_ram_gb,
            "ram_available_gb": profile.available_ram_gb,
            "has_gpu": profile.is_gpu_accelerated,
            "gpu_name": profile.gpu_name,
            "vram_gb": profile.vram_gb,
            "is_unified_memory": profile.is_unified_memory,
            "recommended_encoder": profile.recommended_encoder.value,
            "recommended_model_size": profile.recommended_model_size,
            "recommended_quantization": profile.recommended_quantization,
            "max_local_concurrency": profile.max_local_concurrency,
            "ffmpeg_threads": profile.ffmpeg_threads,
        }
    }
