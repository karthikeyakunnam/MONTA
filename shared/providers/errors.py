"""
MONTA — Provider Errors
=========================
A closed error taxonomy. Callers branch on ``retryable`` rather than on vendor
status codes, which keeps retry policy independent of any one API.
"""

from shared.exceptions import ModelError


class ProviderError(ModelError):
    """Base class for all model provider failures."""

    retryable: bool = False

    def __init__(self, message: str, *, provider: str = "", model: str = ""):
        super().__init__(message)
        self.provider = provider
        self.model = model


class ProviderTimeoutError(ProviderError):
    retryable = True


class ProviderUnavailableError(ProviderError):
    """Network failure or 5xx."""

    retryable = True


class ProviderRateLimitError(ProviderError):
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after_s = retry_after_s


class ProviderAuthError(ProviderError):
    """Bad or missing credentials. Never retried — retrying cannot fix it."""


class ProviderResponseError(ProviderError):
    """The request was rejected (4xx) or the response is unusable (blocked, empty, malformed)."""


class CapabilityError(ProviderError):
    """The request needs a capability (e.g. vision) the provider lacks."""


class CircuitOpenError(ProviderUnavailableError):
    """The circuit breaker is open; the call was not attempted."""

    retryable = False


class StructuredOutputError(ProviderError):
    """The model never produced output that passed schema + semantic validation."""

    def __init__(self, message: str, *, errors: list[str], **kw):
        super().__init__(message, **kw)
        self.errors = errors
