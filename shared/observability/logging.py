"""
MONTA — Structured Logging
============================
``configure_logging()`` installs a JSON formatter on the root logger. Every
record carries the active correlation ids (trace_id, request_id, project_id,
user_ref, plan_id, task_id, agent, stage) so any log line can be joined to
its trace.
"""

import json
import logging
import sys
from datetime import datetime, timezone

from shared.observability.context import correlation

_STANDARD = set(vars(logging.makeLogRecord({})))

#: Attribute names ``LogRecord`` owns. Passing any of these in ``extra=`` makes
#: ``Logger.makeRecord`` raise ``KeyError``, so a log line can abort the request that was
#: only trying to report on itself.
RESERVED_LOG_KEYS = frozenset(_STANDARD | {"message", "asctime"})
_MAKE_RECORD_PATCHED = False


def _install_reserved_key_guard() -> None:
    """Make a colliding ``extra=`` key rename itself instead of raising.

    A field named ``filename`` or ``name`` is a natural thing to log, and the collision only
    surfaces once logging is configured at INFO — which is to say in production and not in a
    quiet unit test. Losing a request because its log line used the wrong word is not a
    trade worth making, so the key is suffixed and the line is still emitted.
    """
    global _MAKE_RECORD_PATCHED
    if _MAKE_RECORD_PATCHED:
        return
    original = logging.Logger.makeRecord

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):
        if extra:
            collisions = RESERVED_LOG_KEYS & extra.keys()
            if collisions:
                extra = {(f"{k}_" if k in collisions else k): v for k, v in extra.items()}
        return original(self, name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)

    logging.Logger.makeRecord = makeRecord       # type: ignore[method-assign]
    _MAKE_RECORD_PATCHED = True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **correlation(),
        }
        for k, v in vars(record).items():
            if k not in _STANDARD and k not in payload:
                payload[k] = v
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", stream=None) -> logging.Handler:
    _install_reserved_key_guard()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    return handler
