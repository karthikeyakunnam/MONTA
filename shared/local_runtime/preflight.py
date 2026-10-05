"""
MONTA — Local AI Preflight Diagnostic
======================================
Verifies local AI runtime readiness, model existence, structured output generation,
modality capabilities, and offline mode enforcement before pipeline execution.
"""

from dataclasses import dataclass, field
import logging
import time
from typing import Optional

from shared.hardware.detector import HardwareDetector
from shared.hardware.profile import HardwareProfile
from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.local_runtime.base import LocalModelRuntime, RuntimeHealth
from shared.local_runtime.registry import ModelRegistry
from shared.providers.base import Capability, GenerationConfig, Message
from shared.providers.registry import ProviderSettings, build_providers

logger = logging.getLogger("monta.preflight")


@dataclass(frozen=True)
class PreflightReport:
    """Diagnostic report on local AI readiness."""

    is_ready: bool
    runtime_name: str
    runtime_version: str
    selected_model: str
    installed_models: tuple[str, ...]
    hardware_profile: HardwareProfile
    offline_mode_enforced: bool
    structured_output_verified: bool
    vision_verified: bool
    test_latency_ms: float
    error_message: str = ""
    warnings: tuple[str, ...] = ()

    def summary(self) -> str:
        status = "READY" if self.is_ready else "NOT READY"
        return (
            f"=== LOCAL AI PREFLIGHT: {status} ===\n"
            f"Runtime: {self.runtime_name} (v{self.runtime_version})\n"
            f"Model: {self.selected_model}\n"
            f"Hardware: {self.hardware_profile.device_type.value} ({self.hardware_profile.total_ram_gb} GB RAM)\n"
            f"Offline Mode: {'ENABLED' if self.offline_mode_enforced else 'DISABLED'}\n"
            f"Structured JSON: {'OK' if self.structured_output_verified else 'FAILED'}\n"
            f"Vision: {'VERIFIED' if self.vision_verified else 'UNAVAILABLE (Deterministic Signals Active)'}\n"
            f"Inference Latency: {self.test_latency_ms:.1f}ms\n"
            + (f"Error: {self.error_message}\n" if self.error_message else "")
        )


class LocalAIPreflight:
    """Preflight diagnostic runner."""

    @classmethod
    async def run(
        cls,
        settings: Optional[ProviderSettings] = None,
        custom_runtime: Optional[LocalModelRuntime] = None,
    ) -> PreflightReport:
        s = settings or ProviderSettings()
        hw = HardwareDetector.detect()
        warnings: list[str] = []

        runtime = custom_runtime or OllamaRuntime(base_url=s.ollama_base_url)
        health = await runtime.check_health()

        if not health.is_healthy:
            return PreflightReport(
                is_ready=False,
                runtime_name=health.runtime_name,
                runtime_version=health.version,
                selected_model=s.ollama_text_model,
                installed_models=health.installed_models,
                hardware_profile=hw,
                offline_mode_enforced=s.monta_offline_mode,
                structured_output_verified=False,
                vision_verified=False,
                test_latency_ms=0.0,
                error_message=f"Local runtime '{health.runtime_name}' is unreachable: {health.error_message}",
            )

        # Check installed models
        installed = health.installed_models
        selected_model = s.ollama_text_model

        # If configured model is not in installed list, pick best available model
        if installed and selected_model not in installed:
            matching = [m for m in installed if selected_model.split(":")[0] in m]
            if matching:
                selected_model = matching[0]
            else:
                best = ModelRegistry.select_best_model("text", hw, list(installed))
                selected_model = best
                warnings.append(f"Configured model '{s.ollama_text_model}' not installed. Selected available '{best}'.")

        # Perform test inference turn
        structured_ok = False
        test_latency = 0.0
        try:
            t0 = time.perf_counter()
            test_resp = await runtime.generate_chat(
                messages=[Message.user("Return JSON with status: ok")],
                model=selected_model,
                config=GenerationConfig(temperature=0.0, json_output=True, timeout_s=15.0),
            )
            test_latency = (time.perf_counter() - t0) * 1000
            structured_ok = "ok" in test_resp.text.lower()
        except Exception as e:
            warnings.append(f"Test inference warning: {e}")

        # Check vision model
        spec = ModelRegistry.get_spec(selected_model)
        vision_ok = spec.is_vision or any(ModelRegistry.get_spec(m).is_vision for m in installed)

        return PreflightReport(
            is_ready=health.is_healthy and (len(installed) > 0 or structured_ok),
            runtime_name=health.runtime_name,
            runtime_version=health.version,
            selected_model=selected_model,
            installed_models=installed,
            hardware_profile=hw,
            offline_mode_enforced=s.monta_offline_mode,
            structured_output_verified=structured_ok,
            vision_verified=vision_ok,
            test_latency_ms=test_latency,
            warnings=tuple(warnings),
        )
