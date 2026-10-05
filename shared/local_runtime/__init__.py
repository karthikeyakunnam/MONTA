"""
MONTA — Local AI Runtime Subsystem
===================================
Local-first, offline-capable open-weight model execution infrastructure.
"""

from shared.local_runtime.adapters.llama_cpp import LlamaCppRuntime
from shared.local_runtime.adapters.mlx import MLXRuntime
from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.local_runtime.base import (
    LocalModelInfo,
    LocalModelRuntime,
    RuntimeHealth,
    RuntimeType,
)
from shared.local_runtime.provider import LocalAIProvider
from shared.local_runtime.registry import KNOWN_MODELS, ModelRegistry, ModelSpec

__all__ = [
    "LocalModelRuntime",
    "LocalModelInfo",
    "RuntimeHealth",
    "RuntimeType",
    "LocalAIProvider",
    "ModelRegistry",
    "ModelSpec",
    "KNOWN_MODELS",
    "OllamaRuntime",
    "LlamaCppRuntime",
    "MLXRuntime",
]
