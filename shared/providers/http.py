"""
MONTA — HTTP Provider Base
============================
Shared transport concerns for REST-based providers: one pooled ``httpx``
client per provider instance, uniform status → error mapping, latency capture.
"""

import time
from typing import Any

import httpx

from shared.observability import catalog as m
from shared.observability.tracing import span
from shared.providers.base import ModelProvider, ModelResponse
from shared.providers.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

DEFAULT_LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=16)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class HttpModelProvider(ModelProvider):
    """Base for providers that speak JSON over HTTPS."""

    def __init__(self, *, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(limits=DEFAULT_LIMITS)

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    def _observe(self, response: ModelResponse) -> ModelResponse:
        """Record token usage for a successful generation."""
        m.PROVIDER_TOKENS.inc(response.input_tokens, provider=self.name, model=self.model, direction="input")
        m.PROVIDER_TOKENS.inc(response.output_tokens, provider=self.name, model=self.model, direction="output")
        return response

    async def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout_s: float) -> tuple[dict, float]:
        """POST and return (json_body, latency_ms), mapping every failure to a ProviderError. Instrumented."""
        with span("provider.call", provider=self.name, model=self.model) as sp:
            started = time.perf_counter()
            outcome = "ok"
            try:
                return await self._post_json_inner(url, payload, headers, timeout_s)
            except Exception as e:
                outcome = type(e).__name__
                raise
            finally:
                elapsed = time.perf_counter() - started
                sp.set(outcome=outcome, latency_ms=round(elapsed * 1000, 2))
                m.PROVIDER_CALLS.inc(provider=self.name, model=self.model, outcome=outcome)
                m.PROVIDER_LATENCY.observe(elapsed, provider=self.name, model=self.model)

    async def _post_json_inner(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout_s: float) -> tuple[dict, float]:
        kw = {"provider": self.name, "model": self.model}
        started = time.perf_counter()
        try:
            response = await self._client.post(url, json=payload, headers=headers, timeout=timeout_s)
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(f"{self.model_id} timed out after {timeout_s}s", **kw) from e
        except httpx.TransportError as e:
            raise ProviderUnavailableError(f"{self.model_id} transport error: {e}", **kw) from e
        latency_ms = (time.perf_counter() - started) * 1000

        status = response.status_code
        if status in (401, 403):
            raise ProviderAuthError(f"{self.model_id} rejected credentials ({status})", **kw)
        if status == 429:
            raise ProviderRateLimitError(f"{self.model_id} rate limited", retry_after_s=_retry_after(response), **kw)
        if status == 408 or status >= 500:
            raise ProviderUnavailableError(f"{self.model_id} returned {status}", **kw)
        if status >= 400:
            # Never echo the body: vendors reflect prompts/images back in error messages.
            raise ProviderResponseError(f"{self.model_id} returned {status} ({len(response.content)} byte body)", **kw)
        try:
            return response.json(), latency_ms
        except ValueError as e:
            raise ProviderResponseError(f"{self.model_id} returned non-JSON body", **kw) from e
