"""
MONTA — Observability
=======================
One trace per request, correlated everywhere:

* ``context``  — contextvars carrying trace_id, request/project/plan/task ids
                 (copied into every asyncio task the Director spawns).
* ``tracing``  — ``span()`` context manager; spans go to pluggable sinks
                 (in-memory ring buffer, structured log, OpenTelemetry bridge).
* ``metrics``  — counters/histograms/gauges with Prometheus text exposition;
                 the full catalog is declared in ``catalog.py``.
* ``logging``  — JSON log formatter that stamps correlation ids on every line.

Privacy: user ids are hashed before entering the context; prompts and model
responses are never attached to spans or logs (lengths and hashes only).
"""

from shared.observability import catalog
from shared.observability.context import bind, correlation, new_trace
from shared.observability.tracing import SPANS, span

__all__ = ["SPANS", "bind", "catalog", "correlation", "new_trace", "span"]
