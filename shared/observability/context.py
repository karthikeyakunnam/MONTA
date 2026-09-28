"""
MONTA — Correlation Context
=============================
Request-scoped identifiers in ``contextvars``. Values set with ``bind`` are
visible to everything awaited inside the block, including asyncio tasks
created there (asyncio copies the context at task creation).
"""

import contextvars
import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

_FIELDS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("monta_correlation", default={})

CORRELATION_KEYS = ("trace_id", "request_id", "project_id", "user_ref", "plan_id", "task_id", "agent", "stage")


def new_id() -> str:
    return uuid.uuid4().hex


def hash_user(user_id: str) -> str:
    """Stable, non-reversible user reference for telemetry."""
    return hashlib.sha256(f"monta:{user_id}".encode()).hexdigest()[:16]


def correlation() -> dict[str, str]:
    """Current correlation fields (copy)."""
    return dict(_FIELDS.get())


@contextmanager
def bind(**fields: str | None) -> Iterator[dict[str, str]]:
    """Add correlation fields for the duration of the block. ``user_id`` is hashed into ``user_ref``."""
    current = dict(_FIELDS.get())
    if "user_id" in fields:
        uid = fields.pop("user_id")
        if uid:
            fields["user_ref"] = hash_user(uid)
    current.update({k: str(v) for k, v in fields.items() if v is not None})
    token = _FIELDS.set(current)
    try:
        yield current
    finally:
        _FIELDS.reset(token)


@contextmanager
def new_trace(**fields: str | None) -> Iterator[dict[str, str]]:
    """Start a new trace (fresh trace_id + request_id) unless one is already active."""
    active = _FIELDS.get()
    extra = {} if "trace_id" in active else {"trace_id": new_id(), "request_id": fields.pop("request_id", None) or new_id()}
    with bind(**extra, **fields) as ctx:
        yield ctx
