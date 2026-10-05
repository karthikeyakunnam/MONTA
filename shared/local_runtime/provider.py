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

    async def generate(
        self,
        messages: list[Message],
        config: GenerationConfig | None = None,
    ) -> ModelResponse:
        """Executes turn against local runtime engine."""
        self.check_request(messages)
        return await self.runtime.generate_chat(messages, self.model, config)

    async def aclose(self) -> None:
        await self.runtime.aclose()
