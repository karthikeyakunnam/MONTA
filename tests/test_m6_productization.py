"""
MONTA — M6 Productization Test Suite
====================================
Tests:
- ModelManager (list, capabilities, vision vs text, disk usage, in-use protection, delete)
- Storage & Temp Cleanup (stale upload fragments, expired renders, path safety)
- Persistent Configuration (atomic persistence, defaults, schema validation, reset)
- Models API routes & hardware recommendation
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response
from starlette.testclient import TestClient

from backend.app.main import app
from models.model_manager import (
    DiskUsageReport,
    DownloadProgress,
    ModelInUseError,
    ModelManager,
    ModelNotFoundError,
)
from shared.config_persistence import AppConfig, ConfigManager
from shared.hardware.detector import HardwareDetector
from shared.local_runtime.base import Capability, LocalModelInfo
from shared.storage_cleanup import StorageCleanupManager


# ===========================================================================
# 1. ModelManager Tests
# ===========================================================================

@pytest.fixture
def mock_runtime():
    runtime = MagicMock()
    runtime.is_available = AsyncMock(return_value=True)
    runtime.list_models = AsyncMock(
        return_value=[
            LocalModelInfo(
                name="llama3.2:latest",
                family="llama",
                param_size="3B",
                quantization="Q4_K_M",
                size_bytes=2_000_000_000,
                capabilities=[Capability.TEXT],
            ),
            LocalModelInfo(
                name="llava:latest",
                family="llava",
                param_size="7B",
                quantization="Q4_0",
                size_bytes=4_500_000_000,
                capabilities=[Capability.TEXT, Capability.VISION],
            ),
        ]
    )
    runtime.aclose = AsyncMock()
    return runtime


@pytest.mark.asyncio
async def test_model_manager_list_and_capabilities(mock_runtime):
    manager = ModelManager(runtime=mock_runtime)
    models = await manager.list_models()
    assert len(models) == 2

    # Capability separation
    text_models = await manager.list_text_models()
    assert "llama3.2:latest" in text_models
    assert "llava:latest" in text_models

    vision_models = await manager.list_vision_models()
    assert "llava:latest" in vision_models
    assert "llama3.2:latest" not in vision_models

    assert await manager.is_model_installed("llama3.2:latest")
    assert not await manager.is_model_installed("nonexistent-model")
    await manager.aclose()


@pytest.mark.asyncio
async def test_model_manager_in_use_protection(mock_runtime):
    manager = ModelManager(runtime=mock_runtime)
    manager.mark_active("llama3.2:latest")
    assert "llama3.2:latest" in manager.active_models()

    # Attempting to delete an active model must raise ModelInUseError
    with pytest.raises(ModelInUseError):
        await manager.delete_model("llama3.2:latest")

    manager.mark_inactive("llama3.2:latest")
    assert "llama3.2:latest" not in manager.active_models()
    await manager.aclose()


@pytest.mark.asyncio
async def test_model_manager_delete_nonexistent(mock_runtime):
    manager = ModelManager(runtime=mock_runtime)
    with pytest.raises(ModelNotFoundError):
        await manager.delete_model("nonexistent_model_123")
    await manager.aclose()


@pytest.mark.asyncio
async def test_model_manager_disk_usage(mock_runtime):
    manager = ModelManager(runtime=mock_runtime)
    report = await manager.disk_usage()
    assert report.total_bytes == 6_500_000_000
    assert report.total_gb > 6.0
    assert "llava:latest" in report.model_sizes
    await manager.aclose()


def test_download_progress_calculation():
    prog = DownloadProgress(model_name="test-model", total_bytes=1000, completed_bytes=500)
    assert prog.percent == 50.0

    empty_prog = DownloadProgress(model_name="test-model", total_bytes=0)
    assert empty_prog.percent == 0.0


# ===========================================================================
# 2. Storage & Temporary File Cleanup Tests
# ===========================================================================

def test_storage_cleanup_stale_uploads(tmp_path: Path):
    incoming = tmp_path / ".incoming"
    incoming.mkdir()

    # Create stale file
    stale_file = incoming / "chunk_0.part"
    stale_file.write_text("dummy")
    old_time = time.time() - 7200  # 2 hours old
    os.utime(stale_file, (old_time, old_time))

    # Create fresh file
    fresh_file = incoming / "chunk_1.part"
    fresh_file.write_text("fresh")

    manager = StorageCleanupManager(storage_root=str(tmp_path), stale_upload_ttl_seconds=3600)
    reclaimed = manager.cleanup_stale_uploads()

    assert reclaimed > 0
    assert not stale_file.exists()
    assert fresh_file.exists()


def test_storage_cleanup_expired_renders(tmp_path: Path):
    renders = tmp_path / "renders"
    renders.mkdir()

    # Create expired render
    old_video = renders / "project1_render.mp4"
    old_video.write_bytes(b"0" * 1024)
    old_time = time.time() - (86400 * 10)  # 10 days old
    os.utime(old_video, (old_time, old_time))

    # Create recent render
    recent_video = renders / "project2_render.mp4"
    recent_video.write_bytes(b"0" * 512)

    manager = StorageCleanupManager(storage_root=str(tmp_path), render_cache_ttl_seconds=86400 * 7)
    reclaimed = manager.cleanup_expired_renders()

    assert reclaimed == 1024
    assert not old_video.exists()
    assert recent_video.exists()


def test_storage_cleanup_path_confinement_safety(tmp_path: Path):
    manager = StorageCleanupManager(storage_root=str(tmp_path))
    outside = tmp_path.parent / "system_important_file.txt"
    assert not manager._is_safe_path(outside)


# ===========================================================================
# 3. Persistent Configuration Tests
# ===========================================================================

def test_config_persistence_lifecycle(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(config_path=str(cfg_file))

    # Initial load creates default config
    cfg = mgr.load()
    assert cfg.preferred_text_model == "llama3.2:latest"
    assert cfg_file.exists()

    # Update config
    updated = mgr.update(default_resolution="4k", default_fps=60)
    assert updated.default_resolution == "4k"
    assert updated.default_fps == 60

    # Reload from disk
    mgr2 = ConfigManager(config_path=str(cfg_file))
    reloaded = mgr2.load()
    assert reloaded.default_resolution == "4k"
    assert reloaded.default_fps == 60

    # Reset
    reset_cfg = mgr2.reset()
    assert reset_cfg.default_resolution == "1080p"


# ===========================================================================
# 4. Hardware Detection Integration
# ===========================================================================

def test_hardware_detector_profile_structure():
    detector = HardwareDetector()
    profile = detector.detect()
    assert profile.cpu_cores_physical >= 1
    assert profile.cpu_cores_logical >= 1
    assert profile.total_ram_gb > 0
    assert profile.recommended_model_size in ["3b", "7b", "14b", "32b", "70b"]
    assert profile.recommended_encoder is not None


# ===========================================================================
# 5. Models & Hardware API Tests
# ===========================================================================

def test_api_hardware_endpoint():
    client = TestClient(app)
    response = client.get("/api/v1/models/hardware")
    assert response.status_code == 200
    data = response.json()
    assert "hardware" in data
    assert "device_type" in data["hardware"]
    assert "recommended_encoder" in data["hardware"]


def test_api_models_list_endpoint():
    client = TestClient(app)
    response = client.get("/api/v1/models/")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert "available" in data

