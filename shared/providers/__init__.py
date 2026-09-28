"""
MONTA — Model Provider Abstraction
====================================
Business logic depends only on ``ModelProvider`` (base.py). Vendor specifics
(Gemini REST, OpenAI-compatible chat APIs used by Qwen / Qwen-VL via DashScope
or vLLM) live in their own modules and never leak upward. Adding a future
multimodal model means adding one provider class and one registry entry.
"""

from shared.providers.base import (
    Capability,
    GenerationConfig,
    ImagePart,
    Message,
    ModelProvider,
    ModelResponse,
    TextPart,
)
from shared.providers.errors import (
    CapabilityError,
    CircuitOpenError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StructuredOutputError,
)

__all__ = [
    "Capability", "GenerationConfig", "ImagePart", "Message", "ModelProvider", "ModelResponse", "TextPart",
    "CapabilityError", "CircuitOpenError", "ProviderAuthError", "ProviderError", "ProviderRateLimitError",
    "ProviderResponseError", "ProviderTimeoutError", "ProviderUnavailableError", "StructuredOutputError",
]
