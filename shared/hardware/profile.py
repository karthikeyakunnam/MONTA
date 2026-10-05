"""
MONTA — Hardware Profile
=========================
Hardware capability descriptors and execution configuration.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class DeviceType(StrEnum):
    APPLE_SILICON = "apple_silicon"
    NVIDIA_CUDA = "nvidia_cuda"
    AMD_ROCM = "amd_rocm"
    CPU_ONLY = "cpu_only"


class VideoEncoder(StrEnum):
    VIDEOTOOLBOX_H264 = "h264_videotoolbox"
    VIDEOTOOLBOX_HEVC = "hevc_videotoolbox"
    NVENC_H264 = "h264_nvenc"
    NVENC_HEVC = "hevc_nvenc"
    VAAPI_H264 = "h264_vaapi"
    LIBX264 = "libx264"
    LIBX265 = "libx265"


@dataclass(frozen=True)
class HardwareProfile:
    """Detected user hardware capabilities and adaptive execution parameters."""

    device_type: DeviceType
    os_name: str
    cpu_arch: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    total_ram_gb: float
    available_ram_gb: float
    vram_gb: float
    is_unified_memory: bool = False
    gpu_name: str = ""
    cuda_version: str = ""
    rocm_version: str = ""
    recommended_encoder: VideoEncoder = VideoEncoder.LIBX264
    recommended_model_size: Literal["3b", "7b", "14b", "32b", "70b"] = "7b"
    recommended_quantization: str = "q4_k_m"
    max_local_concurrency: int = 1
    ffmpeg_threads: int = 4
    metadata: dict = field(default_factory=dict)

    @property
    def is_gpu_accelerated(self) -> bool:
        return self.device_type != DeviceType.CPU_ONLY

    @property
    def is_apple_silicon(self) -> bool:
        return self.device_type == DeviceType.APPLE_SILICON

    @property
    def is_nvidia(self) -> bool:
        return self.device_type == DeviceType.NVIDIA_CUDA
