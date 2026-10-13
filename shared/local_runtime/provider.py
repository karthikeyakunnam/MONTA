"""
MONTA — Local AI Provider
===========================
ModelProvider implementation that executes inferences through the Local AI Runtime
subsystem on the user's local hardware (GPU/CPU).
"""

import logging
from typing import Optional

from shared.local_runtime.base import LocalModelRuntime
from shared.local_runtime.registry import ModelRegistry
from shared.providers.base import (
    Capability,
    GenerationConfig,
    Message,
    ModelProvider,
    ModelResponse,
)

logger = logging.getLogger("monta.provider.local")


class LocalAIProvider(ModelProvider):
    """
    First-class local model provider.
    Bridges MONTA intelligence layers to local runtime engines (Ollama, llama.cpp, etc.).
    """

    def __init__(
        self,
        runtime: LocalModelRuntime,
        model: str,
        *,
        vision: bool | None = None,
    ):
        self.runtime = runtime
        self.name = f"local_{runtime.runtime_type.value}"
        self.model = model
        self._spec = ModelRegistry.get_spec(model)

        caps = set(self._spec.capabilities)
        if vision is True:
            caps.add(Capability.VISION)
        elif vision is False:
            caps.discard(Capability.VISION)

        self.capabilities = frozenset(caps)
        self._resolved_model: str | None = None

    @property
    def model_id(self) -> str:
        active = self._resolved_model or self.model
        return f"{self.name}:{active}"

    async def _get_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        if self.model and self.model != "auto":
            try:
                models = await self.runtime.list_models()
                installed_names = [m.name for m in models]
                if installed_names:
                    has_exact = any(
                        m == self.model or m.startswith(self.model + ":") or self.model.startswith(m + ":")
                        for m in installed_names
                    )
                    if not has_exact:
                        from shared.hardware.detector import HardwareDetector
                        hw = HardwareDetector.detect()
                        role = "vision" if Capability.VISION in self.capabilities else "text"
                        try:
                            fallback = ModelRegistry.select_best_model(role, hw, installed_names)
                            logger.info(
                                "Configured model '%s' not installed; resolved '%s' from local models",
                                self.model,
                                fallback,
                            )
                            self._resolved_model = fallback
                            return fallback
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("Failed to query runtime installed models: %s", e)
            self._resolved_model = self.model
            return self.model

        # When model == "auto" or empty
        models = await self.runtime.list_models()
        installed_names = [m.name for m in models]
        from shared.hardware.detector import HardwareDetector
        hw = HardwareDetector.detect()
        role = "vision" if Capability.VISION in self.capabilities else "text"
        fallback = ModelRegistry.select_best_model(role, hw, installed_names)
        self._resolved_model = fallback
        return fallback

    async def generate(
        self,
        messages: list[Message],
        config: GenerationConfig | None = None,
    ) -> ModelResponse:
        """Executes turn against local runtime engine."""
        self.check_request(messages)
        model = await self._get_model()
        return await self.runtime.generate_chat(messages, model, config)

    async def aclose(self) -> None:
        await self.runtime.aclose()
