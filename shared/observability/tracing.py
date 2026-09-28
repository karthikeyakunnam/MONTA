"""
MONTA — Tracing
=================
``span(name, **attrs)`` records a timed span tied to the active trace. Spans
nest via a contextvar, so a provider call made inside a Director task inside
a pipeline run carries the full ancestry.

Sinks (all optional, configured once at startup):
* ``SPANS``                — bounded in-memory ring buffer (debug endpoints, tests)
* ``LogSink``              — one structured log line per finished span
* ``OpenTelemetrySink``    — bridges to an OpenTelemetry tracer when the SDK is installed
"""

import contextvars
import logging
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from shared.observability.context import bind, correlation, new_id

logger = logging.getLogger("monta.trace")

_CURRENT_SPAN: contextvars.ContextVar[str | None] = contextvars.ContextVar("monta_span", default=None)


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_ns: int
    end_ns: int = 0
    status: str = "ok"
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    correlation: dict[str, str] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1e6

    def set(self, **attrs: Any) -> None:
        self.attributes.update(attrs)


class SpanSink(Protocol):
    def export(self, span: Span) -> None: ...


class RingBufferSink:
    def __init__(self, maxlen: int = 10_000):
        self._spans: deque[Span] = deque(maxlen=maxlen)

    def export(self, span: Span) -> None:
        self._spans.append(span)

    def all(self) -> list[Span]:
        return list(self._spans)

    def for_trace(self, trace_id: str) -> list[Span]:
        return [s for s in self._spans if s.trace_id == trace_id]

    def clear(self) -> None:
        self._spans.clear()


class LogSink:
    def export(self, span: Span) -> None:
        logger.info("span %s", span.name, extra={
            "span_name": span.name, "span_id": span.span_id, "parent_id": span.parent_id,
            "duration_ms": round(span.duration_ms, 3), "status": span.status, "error": span.error,
            "attributes": span.attributes,
        })


class OpenTelemetrySink:
    """Re-emits finished spans to OpenTelemetry (requires ``opentelemetry-sdk``)."""

    def __init__(self, tracer_name: str = "monta"):
        from opentelemetry import trace  # noqa: PLC0415 — optional dependency

        self._tracer = trace.get_tracer(tracer_name)

    def export(self, span: Span) -> None:
        otel = self._tracer.start_span(span.name, start_time=span.start_ns,
                                       attributes={**span.correlation, **{k: _otel_value(v) for k, v in span.attributes.items()}})
        if span.error:
            otel.set_attribute("error.message", span.error)
        otel.end(end_time=span.end_ns)


def _otel_value(v: Any):
    return v if isinstance(v, (str, bool, int, float)) else str(v)


SPANS = RingBufferSink()
_SINKS: list[SpanSink] = [SPANS]


def add_sink(sink: SpanSink) -> None:
    _SINKS.append(sink)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    ctx = correlation()
    trace_id = ctx.get("trace_id") or new_id()
    s = Span(trace_id=trace_id, span_id=new_id()[:16], parent_id=_CURRENT_SPAN.get(), name=name,
             start_ns=time.time_ns(), attributes=dict(attrs))
    token = _CURRENT_SPAN.set(s.span_id)
    try:
        with bind(trace_id=trace_id):
            s.correlation = correlation()
            yield s
    except BaseException as e:
        s.status = "error"
        s.error = f"{type(e).__name__}: {str(e)[:300]}"
        raise
    finally:
        _CURRENT_SPAN.reset(token)
        s.end_ns = time.time_ns()
        for sink in _SINKS:
            try:
                sink.export(s)
            except Exception:  # telemetry must never break the request
                logger.debug("span sink failed", exc_info=True)
