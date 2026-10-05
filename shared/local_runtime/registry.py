"""
MONTA — Local Model Registry & Selector
=========================================
Catalog of supported open-weight foundation models and hardware-aware selection.
"""

from dataclasses import dataclass
import re
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
    "qwen2.5:0.5b": ModelSpec(
        name="qwen2.5:0.5b",
        family="qwen",
        param_size="0.5b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=0.5,
        recommended_vram_gb=1.0,
        description="Ultra-tiny Qwen model for dev/testing and extremely constrained hardware.",
    ),
    "qwen2.5:1.5b": ModelSpec(
        name="qwen2.5:1.5b",
        family="qwen",
        param_size="1.5b",
        capabilities=frozenset({Capability.TEXT, Capability.JSON_MODE}),
        min_vram_gb=1.5,
        recommended_vram_gb=2.5,
        description="Compact Qwen model for low-spec hardware.",
    ),
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
        """Pick one installed model using a deterministic, capability-safe policy.

        Ranking is deliberately explicit.  It never depends on the iteration order of
        ``installed_models`` (or a set derived from it): an exact recommended model is
        preferred first, then the same family at the nearest suitable size, followed by
        a documented compatible family fallback.  A model that does not fit the detected
        hardware, or does not support the requested modality, is never selected.
        """
        if not installed_models:
            # Recommend defaults when nothing installed
            if role == "vision":
                return "qwen2-vl:7b"
            elif role == "judge":
                return "qwen2.5:7b"
            else:
                return f"qwen2.5:{hardware.recommended_model_size}"

        # Normalize and sort candidates once.  The original spelling is retained for
        # the runtime call, while every tie-breaker uses the normalized name.
        candidates = sorted(
            ((model, model.strip().lower(), cls.get_spec(model)) for model in installed_models if model.strip()),
            key=lambda candidate: candidate[1],
        )
        compatible = [candidate for candidate in candidates if cls._supports_role(candidate[2], role)]
        fitting = [candidate for candidate in compatible if cls._fits_hardware(candidate[2], hardware)]

        if not compatible:
            required = "vision" if role == "vision" else "text"
            raise ValueError(f"No installed model supports the required {required} role")
        if not fitting:
            budget = cls._memory_budget_gb(hardware)
            raise ValueError(
                f"No installed {role} model fits the local memory budget "
                f"({budget:.1f} GB); compatible models: "
                f"{', '.join(candidate[0] for candidate in compatible)}"
            )

        if role == "vision":
            return cls._pick_vision(fitting).strip()
        return cls._pick_text(fitting, hardware.recommended_model_size).strip()

    @staticmethod
    def _memory_budget_gb(hardware: HardwareProfile) -> float:
        """Match the detector's conservative local-model memory policy."""
        if hardware.is_unified_memory:
            return hardware.available_ram_gb * 0.8
        if hardware.device_type == DeviceType.CPU_ONLY:
            return hardware.available_ram_gb * 0.8
        return max(hardware.vram_gb, hardware.available_ram_gb * 0.7)

    @classmethod
    def _fits_hardware(cls, spec: ModelSpec, hardware: HardwareProfile) -> bool:
        return spec.min_vram_gb <= cls._memory_budget_gb(hardware)

    @staticmethod
    def _supports_role(spec: ModelSpec, role: Literal["text", "vision", "judge"]) -> bool:
        if role == "vision":
            return Capability.VISION in spec.capabilities and spec.is_vision
        # VLMs can technically receive text, but reserving them for visual analysis
        # prevents a text/judge job from accidentally consuming the only vision model.
        return Capability.TEXT in spec.capabilities and not spec.is_vision

    @staticmethod
    def _size_value(param_size: str) -> float:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)b", param_size.strip().lower())
        return float(match.group(1)) if match else float("inf")

    @staticmethod
    def _name_family(model_name: str) -> str:
        return model_name.strip().lower().split(":", 1)[0]

    @classmethod
    def _pick_vision(cls, candidates: list[tuple[str, str, ModelSpec]]) -> str:
        """Use a stable quality preference before a stable size/name fallback."""
        preferred = ("qwen2-vl:7b", "llava:7b", "minicpm-v:8b", "moondream:latest")
        preference = {name: index for index, name in enumerate(preferred)}
        return min(
            candidates,
            key=lambda candidate: (
                preference.get(candidate[1], len(preferred)),
                cls._size_value(candidate[2].param_size),
                candidate[1],
            ),
        )[0]

    @classmethod
    def _pick_text(cls, candidates: list[tuple[str, str, ModelSpec]], preferred_size: str) -> str:
        """Prefer exact ``qwen2.5:<size>``, then nearest qwen family fallback."""
        desired = f"qwen2.5:{preferred_size}".lower()
        desired_size = cls._size_value(preferred_size)

        # 1. Exact requested installed model match.
        exact = [candidate for candidate in candidates if candidate[1] == desired]
        if exact:
            return exact[0][0]

        # 2. Exact model family with the closest available size.  This accepts a
        # local tag such as qwen2.5:7b-instruct as the same family if its catalogued
        # specification reports the same parameter size.
        same_family = [candidate for candidate in candidates if cls._name_family(candidate[1]) == "qwen2.5"]
        if same_family:
            return min(
                same_family,
                key=lambda candidate: (
                    abs(cls._size_value(candidate[2].param_size) - desired_size),
                    cls._size_value(candidate[2].param_size),
                    candidate[1],
                ),
            )[0]

        # 3. Explicit compatible family fallback.  Its order documents the policy;
        # size and name complete every tie-breaker deterministically.
        family_preference = {"qwen": 0, "llama": 1, "mistral": 2, "generic": 3}
        return min(
            candidates,
            key=lambda candidate: (
                family_preference.get(candidate[2].family, 4),
                abs(cls._size_value(candidate[2].param_size) - desired_size),
                cls._size_value(candidate[2].param_size),
                candidate[1],
            ),
        )[0]
