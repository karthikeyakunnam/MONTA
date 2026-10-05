"""
MONTA — Apple Silicon MLX Runtime Adapter
===========================================
Local runtime adapter for Apple Silicon hardware acceleration via MLX.
"""

import logging
import time
from typing import Any

from shared.local_runtime.base import (
    LocalModelInfo,
    LocalModelRuntime,
    RuntimeHealth,
    RuntimeType,
)
from shared.local_runtime.registry import ModelRegistry
from shared.providers.base import (
    Capability,
    GenerationConfig,
    Message,
    ModelResponse,
)
from shared.providers.errors import (
    ProviderUnavailableError,
)

logger = logging.getLogger("monta.runtime.mlx")


class MLXRuntime(LocalModelRuntime):
    """Local runtime adapter for Apple Silicon MLX models."""

    def __init__(self, model_path: str = ""):
        self._model_path = model_path

    @property
    def runtime_type(self) -> RuntimeType:
        return RuntimeType.MLX

    @property
    def base_url(self) -> str:
        return "local://mlx"

    async def is_available(self) -> bool:
        try:
            import mlx.core as mx  # type: ignore # noqa: F401
            import mlx_lm  # type: ignore # noqa: F401
            return True
        except ImportError:
            return False

    async def check_health(self) -> RuntimeHealth:
        avail = await self.is_available()
        if not avail:
            return RuntimeHealth(
                is_healthy=False,
                runtime_name="mlx",
                error_message="mlx or mlx_lm python packages not installed on Apple Silicon",
            )
        return RuntimeHealth(is_healthy=True, runtime_name="mlx")

    async def list_models(self) -> list[LocalModelInfo]:
        return []

    async def generate_chat(
        self,
        messages: list[Message],
        model: str,
        config: GenerationConfig | None = None,
    ) -> ModelResponse:
        raise ProviderUnavailableError("MLX execution requires direct python binding load", provider="mlx", model=model)
