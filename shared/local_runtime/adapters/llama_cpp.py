"""
MONTA — llama.cpp Runtime Adapter
===================================
Local runtime adapter for llama-server / llama-cpp daemon instances.
Provides native GGUF model execution.
"""

import logging
import time
from typing import Any

import httpx

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
    TextPart,
)
from shared.providers.errors import (
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger("monta.runtime.llama_cpp")

DEFAULT_LLAMA_CPP_URL = "http://localhost:8080"


class LlamaCppRuntime(LocalModelRuntime):
    """Local runtime adapter for llama.cpp server instances."""

    def __init__(
        self,
        base_url: str = DEFAULT_LLAMA_CPP_URL,
        client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=4),
            timeout=httpx.Timeout(120.0, connect=5.0),
        )

    @property
    def runtime_type(self) -> RuntimeType:
        return RuntimeType.LLAMA_CPP

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def is_available(self) -> bool:
        try:
            resp = await self._client.get(f"{self._base_url}/health", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def check_health(self) -> RuntimeHealth:
        try:
            resp = await self._client.get(f"{self._base_url}/health", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                return RuntimeHealth(
                    is_healthy=True,
                    runtime_name="llama_cpp",
                    extra=data,
                )
            return RuntimeHealth(
                is_healthy=False,
                runtime_name="llama_cpp",
                error_message=f"HTTP {resp.status_code}",
            )
        except Exception as e:
            return RuntimeHealth(
                is_healthy=False,
                runtime_name="llama_cpp",
                error_message=str(e),
            )

    async def list_models(self) -> list[LocalModelInfo]:
        try:
            resp = await self._client.get(f"{self._base_url}/v1/models", timeout=3.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = []
            for item in data.get("data", []):
                name = item.get("id", "default")
                spec = ModelRegistry.get_spec(name)
                models.append(
                    LocalModelInfo(
                        name=name,
                        family=spec.family,
                        capabilities=spec.capabilities,
                    )
                )
            return models
        except Exception:
            return []

    async def generate_chat(
        self,
        messages: list[Message],
        model: str,
        config: GenerationConfig | None = None,
    ) -> ModelResponse:
        cfg = config or GenerationConfig()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.text} for m in messages],
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_output_tokens,
            "stream": False,
        }
        if cfg.json_output:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=cfg.timeout_s,
            )
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(f"llama.cpp timed out after {cfg.timeout_s}s", provider="llama_cpp", model=model) from e
        except httpx.TransportError as e:
            raise ProviderUnavailableError(f"llama.cpp unreachable: {e}", provider="llama_cpp", model=model) from e

        if resp.status_code != 200:
            raise ProviderResponseError(f"llama.cpp error {resp.status_code}: {resp.text[:300]}", provider="llama_cpp", model=model)

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = data.get("usage", {})
        latency_ms = (time.perf_counter() - started) * 1000

        return ModelResponse(
            text=content,
            provider="llama_cpp",
            model=model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )
