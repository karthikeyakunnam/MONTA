"""
MONTA — Local AI Runtime Base Interface
=========================================
Vendor-independent abstractions for local on-device model execution runtimes
(Ollama, llama.cpp, MLX, etc.).
"""

import abc
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

from shared.providers.base import Capability, GenerationConfig, Message, ModelResponse


class RuntimeType(StrEnum):
    OLLAMA = "ollama"
    LLAMA_CPP = "llama_cpp"
    MLX = "mlx"
    CUSTOM = "custom"


@dataclass(frozen=True)
class LocalModelInfo:
    """Metadata for a model installed and ready in the local runtime."""

    name: str
    family: str = "qwen"
    size_bytes: int = 0
    param_size: str = "7b"
    quantization: str = "q4_k_m"
    capabilities: frozenset[Capability] = field(default_factory=lambda: frozenset({Capability.TEXT, Capability.JSON_MODE}))
    context_length: int = 32768
    modified_at: str = ""

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 2)

    @property
    def has_vision(self) -> bool:
        return Capability.VISION in self.capabilities


@dataclass(frozen=True)
class RuntimeHealth:
    is_healthy: bool
    runtime_name: str
    version: str = ""
    error_message: str = ""
    installed_models: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


class LocalModelRuntime(abc.ABC):
    """Abstract interface for local execution engines running open-weight models."""

    @property
    @abc.abstractmethod
    def runtime_type(self) -> RuntimeType:
        """The runtime identifier."""

    @property
    @abc.abstractmethod
    def base_url(self) -> str:
        """Endpoint URL for the local daemon/server."""

    @abc.abstractmethod
    async def is_available(self) -> bool:
        """Returns True if the local runtime daemon is running and reachable."""

    @abc.abstractmethod
    async def list_models(self) -> list[LocalModelInfo]:
        """Lists all locally installed models."""

    @abc.abstractmethod
    async def generate_chat(
        self,
        messages: list[Message],
        model: str,
        config: GenerationConfig | None = None,
    ) -> ModelResponse:
        """Executes a chat generation turn against the specified local model."""

    @abc.abstractmethod
    async def check_health(self) -> RuntimeHealth:
        """Performs a self-diagnostic check on the local runtime."""

    async def aclose(self) -> None:
        """Releases any local network or process handles."""
        pass
