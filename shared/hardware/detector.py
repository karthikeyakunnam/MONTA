"""
MONTA — Hardware Detector
==========================
Probes the user's local operating system, CPU, memory, GPU (Apple Silicon / Metal,
NVIDIA CUDA, AMD ROCm), and media encoder hardware. Computes safe local model size,
quantization, and concurrency recommendations.
"""

import logging
import os
import platform
import shutil
import subprocess
from typing import Optional

from shared.hardware.profile import DeviceType, HardwareProfile, VideoEncoder

logger = logging.getLogger("monta.hardware")


class HardwareDetector:
    """Detects local hardware capabilities and computes adaptive resource budgets."""

    @classmethod
    def detect(cls) -> HardwareProfile:
        os_name = platform.system().lower()
        cpu_arch = platform.machine().lower()
        logical_cores = os.cpu_count() or 4
        physical_cores = max(1, logical_cores // 2)

        total_ram_gb, available_ram_gb = cls._probe_ram()
        device_type, vram_gb, is_unified, gpu_name, extra = cls._probe_gpu(os_name, cpu_arch, total_ram_gb)
        encoder = cls._probe_best_encoder(os_name, device_type)

        model_size, quant, max_concurrency = cls._compute_model_budget(
            device_type=device_type,
            total_ram_gb=total_ram_gb,
            available_ram_gb=available_ram_gb,
            vram_gb=vram_gb,
            is_unified=is_unified,
        )

        ffmpeg_threads = min(8, max(2, logical_cores - 1))

        return HardwareProfile(
            device_type=device_type,
            os_name=os_name,
            cpu_arch=cpu_arch,
            cpu_cores_physical=physical_cores,
            cpu_cores_logical=logical_cores,
            total_ram_gb=round(total_ram_gb, 2),
            available_ram_gb=round(available_ram_gb, 2),
            vram_gb=round(vram_gb, 2),
            is_unified_memory=is_unified,
            gpu_name=gpu_name,
            cuda_version=extra.get("cuda_version", ""),
            rocm_version=extra.get("rocm_version", ""),
            recommended_encoder=encoder,
            recommended_model_size=model_size,
            recommended_quantization=quant,
            max_local_concurrency=max_concurrency,
            ffmpeg_threads=ffmpeg_threads,
            metadata=extra,
        )

    @classmethod
    def _probe_ram(cls) -> tuple[float, float]:
        """Returns (total_ram_gb, available_ram_gb). Safe on Linux/macOS/Windows."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            return mem.total / (1024**3), mem.available / (1024**3)
        except Exception:
            pass

        # Fallback for macOS via sysctl
        if platform.system().lower() == "darwin":
            try:
                out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=2).strip()
                total = int(out) / (1024**3)
                return total, total * 0.7
            except Exception:
                pass

        # Fallback for Linux via /proc/meminfo
        if os.path.exists("/proc/meminfo"):
            try:
                total_kb, avail_kb = 0, 0
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            total_kb = int(line.split()[1])
                        elif line.startswith("MemAvailable:"):
                            avail_kb = int(line.split()[1])
                if total_kb > 0:
                    total_gb = total_kb / (1024**2)
                    avail_gb = (avail_kb if avail_kb > 0 else total_kb * 0.7) / (1024**2)
                    return total_gb, avail_gb
            except Exception:
                pass

        return 16.0, 10.0  # Conservative default

    @classmethod
    def _probe_gpu(cls, os_name: str, cpu_arch: str, total_ram_gb: float) -> tuple[DeviceType, float, bool, str, dict]:
        """Probes Apple Silicon MPS, NVIDIA CUDA, or AMD ROCm."""
        extra: dict = {}

        # 1. Apple Silicon (macOS arm64)
        if os_name == "darwin" and ("arm" in cpu_arch or "aarch64" in cpu_arch):
            gpu_name = "Apple Silicon GPU (Unified Memory)"
            try:
                brand = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True, timeout=2).strip()
                if brand:
                    gpu_name = f"Apple {brand}"
            except Exception:
                pass
            # On Apple Silicon, unified memory is shared between CPU and GPU
            vram_gb = total_ram_gb * 0.75
            return DeviceType.APPLE_SILICON, vram_gb, True, gpu_name, extra

        # 2. NVIDIA CUDA (via nvidia-smi if installed)
        if shutil.which("nvidia-smi"):
            try:
                cmd = ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]
                out = subprocess.check_output(cmd, text=True, timeout=3).strip()
                if out:
                    lines = out.splitlines()
                    first_line = lines[0].split(",")
                    name = first_line[0].strip() if len(first_line) > 0 else "NVIDIA GPU"
                    mem_mb = float(first_line[1].strip()) if len(first_line) > 1 else 8192.0
                    driver = first_line[2].strip() if len(first_line) > 2 else ""
                    extra["driver_version"] = driver
                    extra["gpu_count"] = len(lines)
                    vram_gb = mem_mb / 1024.0
                    return DeviceType.NVIDIA_CUDA, vram_gb, False, name, extra
            except Exception as e:
                logger.debug("nvidia-smi probe failed: %s", e)

        # 3. AMD ROCm (via rocm-smi if installed)
        if shutil.which("rocm-smi"):
            try:
                out = subprocess.check_output(["rocm-smi", "--showmeminfo", "vram"], text=True, timeout=3).strip()
                return DeviceType.AMD_ROCM, 8.0, False, "AMD Radeon GPU (ROCm)", extra
            except Exception as e:
                logger.debug("rocm-smi probe failed: %s", e)

        # 4. CPU Fallback
        return DeviceType.CPU_ONLY, 0.0, False, "CPU Only (No discrete GPU acceleration)", extra

    @classmethod
    def _probe_best_encoder(cls, os_name: str, device_type: DeviceType) -> VideoEncoder:
        """Determines best FFmpeg hardware-accelerated video encoder."""
        if device_type == DeviceType.APPLE_SILICON or os_name == "darwin":
            return VideoEncoder.VIDEOTOOLBOX_H264
        if device_type == DeviceType.NVIDIA_CUDA:
            return VideoEncoder.NVENC_H264
        if device_type == DeviceType.AMD_ROCM and os_name == "linux":
            return VideoEncoder.VAAPI_H264
        return VideoEncoder.LIBX264

    @classmethod
    def _compute_model_budget(
        cls,
        device_type: DeviceType,
        total_ram_gb: float,
        available_ram_gb: float,
        vram_gb: float,
        is_unified: bool,
    ) -> tuple[str, str, int]:
        """Calculates recommended model size ("3b"|"7b"|"14b"|"32b"), quantization, and concurrency."""
        effective_memory = (available_ram_gb * 0.8) if is_unified else max(vram_gb, available_ram_gb * 0.7)

        if effective_memory >= 32.0:
            return "32b", "q4_k_m", 2
        elif effective_memory >= 14.0:
            return "14b", "q4_k_m", 2 if device_type != DeviceType.CPU_ONLY else 1
        elif effective_memory >= 6.0:
            return "7b", "q4_k_m", 1
        elif effective_memory >= 3.5:
            return "3b", "q4_k_m", 1
        else:
            return "3b", "q4_k_s", 1
