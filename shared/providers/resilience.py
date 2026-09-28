"""
MONTA — Provider Resilience
=============================
Decorators around any ``ModelProvider``:

* ``ResilientProvider`` — bounded concurrency, retries with full-jitter
  exponential backoff (honouring ``Retry-After``), and a circuit breaker so a
  failing vendor is shed quickly instead of piling up timeouts.
* ``FallbackProvider`` — ordered failover across vendors (e.g. Qwen-VL → Gemini).
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable

from shared.observability import catalog as m
from shared.providers.base import GenerationConfig, Message, ModelProvider, ModelResponse
from shared.providers.errors import CircuitOpenError, ProviderError, ProviderRateLimitError

logger = logging.getLogger("monta.providers")

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class CircuitBreaker:
    """Classic closed → open → half-open breaker. Not shared across processes by design."""

    def __init__(self, failure_threshold: int = 5, reset_timeout_s: float = 30.0, clock: Clock = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_timeout_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self.state == "half_open" or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()


class ResilientProvider(ModelProvider):
    def __init__(
        self,
        inner: ModelProvider,
        *,
        max_attempts: int = 3,
        base_delay_s: float = 0.5,
        max_delay_s: float = 8.0,
        max_concurrency: int = 16,
        breaker: CircuitBreaker | None = None,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
    ):
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.capabilities = inner.capabilities
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.breaker = breaker or CircuitBreaker()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._sleep = sleep
        self._rng = rng or random.Random()

    def _delay(self, attempt: int, error: ProviderError) -> float:
        if isinstance(error, ProviderRateLimitError) and error.retry_after_s is not None:
            return min(self.max_delay_s, error.retry_after_s)
        return self._rng.uniform(0, min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1)))

    async def generate(self, messages: list[Message], config: GenerationConfig | None = None) -> ModelResponse:
        self.inner.check_request(messages)
        last: ProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            m.CIRCUIT_OPEN.set(1.0 if self.breaker.state == "open" else 0.0, provider=self.name, model=self.model)
            if not self.breaker.allow():
                raise CircuitOpenError(f"circuit open for {self.model_id}", provider=self.name, model=self.model)
            try:
                async with self._semaphore:
                    response = await self.inner.generate(messages, config)
            except ProviderError as e:
                last = e
                if e.retryable:
                    self.breaker.record_failure()
                if not e.retryable or attempt == self.max_attempts:
                    raise
                m.PROVIDER_RETRIES.inc(provider=self.name, model=self.model, error=type(e).__name__)
                delay = self._delay(attempt, e)
                logger.warning("%s attempt %d failed (%s); retrying in %.2fs", self.model_id, attempt, e, delay)
                await self._sleep(delay)
            else:
                self.breaker.record_success()
                return response
        raise last if last else ProviderError("no attempts made", provider=self.name, model=self.model)

    async def aclose(self) -> None:
        await self.inner.aclose()


class FallbackProvider(ModelProvider):
    """Try providers in order. Auth and capability errors also fail over (another vendor may work)."""

    def __init__(self, providers: list[ModelProvider]):
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self.providers = providers
        self.name = "fallback"
        self.model = "+".join(p.model_id for p in providers)
        self.capabilities = frozenset().union(*(p.capabilities for p in providers))

    async def generate(self, messages: list[Message], config: GenerationConfig | None = None) -> ModelResponse:
        errors: list[str] = []
        for i, provider in enumerate(self.providers):
            try:
                provider.check_request(messages)
                response = await provider.generate(messages, config)
                if i > 0:
                    m.FALLBACK_USED.inc(from_model=self.providers[0].model_id, to_model=provider.model_id)
                return response
            except ProviderError as e:  # includes auth and capability errors: another vendor may still work
                errors.append(f"{provider.model_id}: {e}")
                logger.warning("fallback: %s failed: %s", provider.model_id, e)
        raise ProviderError("all providers failed: " + " | ".join(errors), provider=self.name, model=self.model)

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()
