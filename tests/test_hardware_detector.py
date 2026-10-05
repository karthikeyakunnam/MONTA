"""
Tests for HardwareDetector and HardwareProfile.
"""

from unittest.mock import patch

from shared.hardware.detector import HardwareDetector
from shared.hardware.profile import DeviceType, VideoEncoder


def test_hardware_detector_runs_without_crashing():
    profile = HardwareDetector.detect()
    assert profile.cpu_cores_logical >= 1
    assert profile.total_ram_gb > 0
    assert profile.device_type in (
        DeviceType.APPLE_SILICON,
        DeviceType.NVIDIA_CUDA,
        DeviceType.AMD_ROCM,
        DeviceType.CPU_ONLY,
    )
    assert profile.recommended_encoder in (
        VideoEncoder.VIDEOTOOLBOX_H264,
        VideoEncoder.NVENC_H264,
        VideoEncoder.VAAPI_H264,
        VideoEncoder.LIBX264,
    )
    assert profile.recommended_model_size in ("3b", "7b", "14b", "32b", "70b")


def test_model_budget_computation():
    # Low RAM machine -> 3b
    size, quant, conc = HardwareDetector._compute_model_budget(
        device_type=DeviceType.CPU_ONLY,
        total_ram_gb=8.0,
        available_ram_gb=4.0,
        vram_gb=0.0,
        is_unified=False,
    )
    assert size == "3b"
    assert conc == 1

    # Medium RAM / GPU -> 7b
    size, quant, conc = HardwareDetector._compute_model_budget(
        device_type=DeviceType.NVIDIA_CUDA,
        total_ram_gb=16.0,
        available_ram_gb=12.0,
        vram_gb=8.0,
        is_unified=False,
    )
    assert size == "7b"

    # High RAM Apple Silicon unified -> 14b or 32b
    size, quant, conc = HardwareDetector._compute_model_budget(
        device_type=DeviceType.APPLE_SILICON,
        total_ram_gb=36.0,
        available_ram_gb=28.0,
        vram_gb=27.0,
        is_unified=True,
    )
    assert size in ("14b", "32b")


def test_encoder_selection_by_platform():
    assert HardwareDetector._probe_best_encoder("darwin", DeviceType.APPLE_SILICON) == VideoEncoder.VIDEOTOOLBOX_H264
    assert HardwareDetector._probe_best_encoder("linux", DeviceType.NVIDIA_CUDA) == VideoEncoder.NVENC_H264
    assert HardwareDetector._probe_best_encoder("linux", DeviceType.AMD_ROCM) == VideoEncoder.VAAPI_H264
    assert HardwareDetector._probe_best_encoder("linux", DeviceType.CPU_ONLY) == VideoEncoder.LIBX264
