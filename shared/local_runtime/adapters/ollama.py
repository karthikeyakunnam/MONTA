"""
MONTA — Ollama Runtime Adapter
================================
Native adapter for the local Ollama daemon (default: http://localhost:11434).
Supports text reasoning, multimodal vision (Qwen-VL, LLaVA), and structured JSON output.
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
from shared.observability import catalog as m
from shared.observability.tracing import span
from shared.providers.base import (
    Capability,
    GenerationConfig,
    ImagePart,
    Message,
    ModelResponse,
    TextPart,
)
from shared.providers.errors import (
    CapabilityError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger("monta.runtime.ollama")

DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaRuntime(LocalModelRuntime):
    """Local runtime adapter communicating with the Ollama daemon."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
            timeout=httpx.Timeout(120.0, connect=5.0),
        )

    @property
    def runtime_type(self) -> RuntimeType:
        return RuntimeType.OLLAMA

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def is_available(self) -> bool:
        """Checks if Ollama daemon is reachable."""
        try:
            resp = await self._client.get(f"{self._base_url}/api/version", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def check_health(self) -> RuntimeHealth:
        """Probes Ollama daemon status and lists installed models."""
        try:
            resp = await self._client.get(f"{self._base_url}/api/version", timeout=3.0)
            if resp.status_code != 200:
                return RuntimeHealth(
                    is_healthy=False,
                    runtime_name="ollama",
                    error_message=f"HTTP {resp.status_code} from {self._base_url}",
                )
            version_data = resp.json()
            models = await self.list_models()
            model_names = tuple(m.name for m in models)
            return RuntimeHealth(
                is_healthy=True,
                runtime_name="ollama",
                version=version_data.get("version", "unknown"),
                installed_models=model_names,
                extra={"endpoint": self._base_url},
            )
        except Exception as e:
            return RuntimeHealth(
                is_healthy=False,
                runtime_name="ollama",
                error_message=f"Connection failed: {e}",
            )

    async def list_models(self) -> list[LocalModelInfo]:
        """Retrieves locally installed models via /api/tags."""
        try:
            resp = await self._client.get(f"{self._base_url}/api/tags", timeout=5.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            models: list[LocalModelInfo] = []
            for item in data.get("models", []):
                name = item.get("name", "")
                spec = ModelRegistry.get_spec(name)
                details = item.get("details", {})
                info = LocalModelInfo(
                    name=name,
                    family=details.get("family", spec.family),
                    size_bytes=item.get("size", 0),
                    param_size=details.get("parameter_size", spec.param_size),
                    quantization=details.get("quantization_level", "q4_k_m"),
                    capabilities=spec.capabilities,
                    modified_at=item.get("modified_at", ""),
                )
                models.append(info)
            return models
        except Exception as e:
            logger.warning("failed to list Ollama models: %s", e)
            return []

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Converts MONTA Message list to Ollama /api/chat message format."""
        out: list[dict[str, Any]] = []
        for msg in messages:
            text_chunks = [p.text for p in msg.parts if isinstance(p, TextPart)]
            image_b64s = [p.b64() for p in msg.parts if isinstance(p, ImagePart)]
            item: dict[str, Any] = {
                "role": msg.role,
                "content": "\n".join(text_chunks),
            }
            if image_b64s:
                item["images"] = image_b64s
            out.append(item)
        return out

    async def generate_chat(
        self,
        messages: list[Message],
        model: str,
        config: GenerationConfig | None = None,
    ) -> ModelResponse:
        """Executes generation against Ollama /api/chat endpoint."""
        cfg = config or GenerationConfig()
        spec = ModelRegistry.get_spec(model)

        # Validate capabilities
        has_images = any(m.has_images for m in messages)
        if has_images and not spec.is_vision:
            raise CapabilityError(
                f"Local model {model} does not have vision capabilities",
                provider="ollama",
                model=model,
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages),
            "stream": False,
            "options": {
                "temperature": cfg.temperature,
                "num_predict": cfg.max_output_tokens,
            },
        }

        if cfg.json_output:
            payload["format"] = "json"

        with span("ollama.chat", model=model, json_output=cfg.json_output) as sp:
            started = time.perf_counter()
            outcome = "ok"
            try:
                resp = await self._client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=cfg.timeout_s,
                )
            except httpx.TimeoutException as e:
                raise ProviderTimeoutError(
                    f"Ollama model {model} timed out after {cfg.timeout_s}s",
                    provider="ollama",
                    model=model,
                ) from e
            except httpx.TransportError as e:
                raise ProviderUnavailableError(
                    f"Ollama daemon unreachable at {self._base_url}: {e}",
                    provider="ollama",
                    model=model,
                ) from e
            except Exception as e:
                outcome = type(e).__name__
                raise

            elapsed = time.perf_counter() - started
            latency_ms = elapsed * 1000

            if resp.status_code != 200:
                outcome = f"http_{resp.status_code}"
                raise ProviderResponseError(
                    f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}",
                    provider="ollama",
                    model=model,
                )

            try:
                data = resp.json()
            except Exception as e:
                raise ProviderResponseError(
                    f"Ollama returned invalid JSON response: {e}",
                    provider="ollama",
                    model=model,
                ) from e

            content = data.get("message", {}).get("content", "")
            prompt_tokens = data.get("prompt_eval_count", 0)
            eval_tokens = data.get("eval_count", 0)

            sp.set(
                outcome=outcome,
                latency_ms=round(latency_ms, 2),
                tokens=f"{prompt_tokens}+{eval_tokens}",
            )
            m.PROVIDER_CALLS.inc(provider="ollama", model=model, outcome=outcome)
            m.PROVIDER_LATENCY.observe(elapsed, provider="ollama", model=model)
            m.PROVIDER_TOKENS.inc(prompt_tokens, provider="ollama", model=model, direction="input")
            m.PROVIDER_TOKENS.inc(eval_tokens, provider="ollama", model=model, direction="output")

            return ModelResponse(
                text=content,
                provider="ollama",
                model=model,
                input_tokens=prompt_tokens,
                output_tokens=eval_tokens,
                latency_ms=latency_ms,
                raw_finish_reason=data.get("done_reason", "stop"),
                extra={"total_duration_ns": data.get("total_duration", 0)},
            )
