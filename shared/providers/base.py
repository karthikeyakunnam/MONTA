"""
MONTA — Provider Interface
============================
Vendor-neutral request/response types and the ``ModelProvider`` ABC.
"""

import abc
import base64
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from shared.providers.errors import CapabilityError


class Capability(StrEnum):
    TEXT = "text"
    VISION = "vision"
    JSON_MODE = "json_mode"


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    mime_type: str = "image/jpeg"

    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


Part = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    parts: tuple[Part, ...]

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls("system", (TextPart(text),))

    @classmethod
    def user(cls, text: str, images: tuple[ImagePart, ...] = ()) -> "Message":
        return cls("user", (*images, TextPart(text)))

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls("assistant", (TextPart(text),))

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def has_images(self) -> bool:
        return any(isinstance(p, ImagePart) for p in self.parts)


@dataclass(frozen=True)
class GenerationConfig:
    temperature: float = 0.2
    max_output_tokens: int = 2048
    json_output: bool = False
    timeout_s: float = 60.0


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    raw_finish_reason: str = ""
    extra: dict = field(default_factory=dict)


class ModelProvider(abc.ABC):
    """Anything that turns messages into text. Implementations must be safe for concurrent use."""

    name: str
    model: str
    capabilities: frozenset[Capability]

    @abc.abstractmethod
    async def generate(self, messages: list[Message], config: GenerationConfig | None = None) -> ModelResponse:
        """Run one generation. Raises a ``ProviderError`` subclass on failure."""

    async def aclose(self) -> None:
        """Release network resources. Idempotent."""

    @property
    def model_id(self) -> str:
        return f"{self.name}:{self.model}"

    def check_request(self, messages: list[Message]) -> None:
        """Fail fast when a request needs capabilities this provider lacks."""
        if any(m.has_images for m in messages) and Capability.VISION not in self.capabilities:
            raise CapabilityError(f"{self.model_id} does not support images", provider=self.name, model=self.model)
