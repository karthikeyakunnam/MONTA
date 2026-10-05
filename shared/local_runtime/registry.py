"""
MONTA — Local Model Registry & Selector
=========================================
Catalog of supported open-weight foundation models and hardware-aware selection.
"""

from dataclasses import dataclass, field
from typing import Literal

from shared.hardware.profile import DeviceType, HardwareProfile
from shared.providers.base import Capability


@dataclass(frozen=True)
class ModelSpec:
    name: str
    family: str
    param_size: str
    capabilities: frozenset[Capability]
    min_vram_gb: float
    recommended_vram_gb: float
    context_window: int = 32768
    is_vision: bool = False
    description: str = ""


KNOWN_MODELS: dict[str, ModelSpec] = {
    # Qwen 2.5 Text/Reasoning Family (Primary recommended)
    "qwen2.5:3b": ModelSpec(
        name="qwen2.5:3b",
        family="qwen",
        param_size="3b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=2.5,
        recommended_vram_gb=4.0,
        description="Fast, ultra-lightweight Qwen model for low-spec or CPU-only hardware.",
    ),
    "qwen2.5:7b": ModelSpec(
        name="qwen2.5:7b",
        family="qwen",
        param_size="7b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=5.0,
        recommended_vram_gb=8.0,
        description="Balanced high-reasoning Qwen model for intent, story architecture, and refiner.",
    ),
    "qwen2.5:14b": ModelSpec(
        name="qwen2.5:14b",
        family="qwen",
        param_size="14b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=10.0,
        recommended_vram_gb=16.0,
        description="Advanced creative reasoning model for complex multi-act story generation.",
    ),
    "qwen2.5:32b": ModelSpec(
        name="qwen2.5:32b",
        family="qwen",
        param_size="32b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=20.0,
        recommended_vram_gb=32.0,
        description="High-tier open-weight model for workstation / high unified memory Mac.",
    ),
    # Vision Models (Local Video/Frame understanding)
    "qwen2-vl:7b": ModelSpec(
        name="qwen2-vl:7b",
        family="qwen_vl",
        param_size="7b",
        capabilities=frozenset({Capability.TEXT, Capability.VISION, Capability.JSON_MODE}),
        min_vram_gb=6.0,
        recommended_vram_gb=10.0,
        is_vision=True,
        description="State-of-the-art local multimodal vision model for video keyframe analysis.",
    ),
    "llava:7b": ModelSpec(
        name="llava:7b",
        family="llava",
        param_size="7b",
        capabilities=frozenset({Capability.TEXT, Capability.VISION, Capability.JSON_MODE}),
        min_vram_gb=5.5,
        recommended_vram_gb=8.0,
        is_vision=True,
        description="Standard local vision-language model.",
    ),
    "minicpm-v:8b": ModelSpec(
        name="minicpm-v:8b",
        family="minicpm",
        param_size="8b",
        capabilities=frozenset({Capability.TEXT, Capability.VISION, Capability.JSON_MODE}),
        min_vram_gb=6.0,
        recommended_vram_gb=9.0,
        is_vision=True,
        description="High-efficiency visual perception model.",
    ),
    # Fallback Lightweight Models
    "llama3.2:3b": ModelSpec(
        name="llama3.2:3b",
        family="llama",
        param_size="3b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=2.5,
        recommended_vram_gb=4.0,
        description="Compact lightweight LLaMA model.",
    ),
}


class ModelRegistry:
    """Manages model capabilities, cataloging, and automated selection."""

    @staticmethod
    def get_spec(model_name: str) -> ModelSpec:
        """Retrieves spec for a known model or returns a safe dynamic fallback spec."""
        cleaned = model_name.lower().split(":")[0] + (":" + model_name.split(":")[1] if ":" in model_name else "")
        if model_name in KNOWN_MODELS:
            return KNOWN_MODELS[model_name]
        for k, v in KNOWN_MODELS.items():
            if k.startswith(cleaned):
                return v

        is_vis = any(v in model_name.lower() for v in ("vl", "vision", "llava", "minicpm", "cogvlm", "moondream"))
        caps = {Capability.TEXT, Capability.JSON_MODE}
        if is_vis:
            caps.add(Capability.VISION)

        return ModelSpec(
            name=model_name,
            family="generic",
            param_size="7b",
            capabilities=frozenset(caps),
            min_vram_gb=5.0,
            recommended_vram_gb=8.0,
            is_vision=is_vis,
            description="Custom local model",
        )

    @classmethod
    def select_best_model(
        cls,
        role: Literal["text", "vision", "judge"],
        hardware: HardwareProfile,
        installed_models: list[str],
    ) -> str:
        """
        Picks the best available model for a given task role based on detected hardware
        and locally installed models.
        """
        if not installed_models:
            # Recommend defaults when nothing installed
            if role == "vision":
                return "qwen2-vl:7b"
            elif role == "judge":
                return "qwen2.5:7b"
            else:
                return f"qwen2.5:{hardware.recommended_model_size}"

        installed_set = {m.lower() for m in installed_models}

        if role == "vision":
            candidates = ["qwen2-vl:7b", "llava:7b", "minicpm-v:8b", "moondream:latest"]
            for c in candidates:
                for inst in installed_set:
                    if inst == c or inst.startswith(c.split(":")[0]):
                        return inst
            # Find any installed model with vision capabilities
            for inst in installed_models:
                if cls.get_spec(inst).is_vision:
                    return inst
            return installed_models[0]

        # Text / Story role
        size_pref = hardware.recommended_model_size
        priorities = [
            f"qwen2.5:{size_pref}",
            "qwen2.5:14b" if size_pref == "32b" else "qwen2.5:7b",
            "qwen2.5:7b",
            "qwen2.5:3b",
            "llama3.2:3b",
            "mistral:7b",
        ]

        for p in priorities:
            for inst in installed_set:
                if inst == p or inst.startswith(p.split(":")[0]):
                    return inst

        return installed_models[0]
