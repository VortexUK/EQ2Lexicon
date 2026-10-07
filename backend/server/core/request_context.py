"""Per-request context (request_id, user_id, world) propagated via contextvars
and stamped onto every log record. Process-local: it does not cross workers.
Outside a request every field reads as "-".
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

# Default None so background tasks + tests don't 500 on unset reads;
# RequestContextMiddleware sets them at request start.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
world_var: ContextVar[str | None] = ContextVar("world", default=None)


class _RequestContextAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """LoggerAdapter that injects contextvar values into every record's extra.

    `_log.info("foo", extra={"x": 1})` from inside a request becomes
    `_log.info("foo", extra={"x": 1, "request_id": "...", "user_id": "...",
    "world": "..."})`. Caller-supplied extras win on collision (defensive —
    a route can override a contextvar value for a specific log line if needed).
    """

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        extra = dict(kwargs.get("extra") or {})
        # Caller wins on collision — only fill in fields they didn't set.
        extra.setdefault("request_id", request_id_var.get() or "-")
        extra.setdefault("user_id", user_id_var.get() or "-")
        extra.setdefault("world", world_var.get() or "-")
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: str) -> _RequestContextAdapter:
    """Return a LoggerAdapter that auto-injects request_id/user_id/world.

    Optional: plain ``logging.getLogger(__name__)`` gets the same fields via
    ``RequestContextFilter`` on the root handler.
    """
    return _RequestContextAdapter(logging.getLogger(name), extra={})


class RequestContextFilter(logging.Filter):
    """Logging filter that injects request_id/user_id/world onto every record.

    Used by `configure_logging()` (backend/core/logging_config.py) on the root
    handler so even plain `logging.getLogger(__name__)` consumers (most of
    the codebase) get the contextvar values stamped onto their LogRecord.
    The format string can then reference `%(request_id)s`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # setattr only — don't overwrite a value the caller set via extra=.
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get() or "-"  # type: ignore[attr-defined]
        if not hasattr(record, "user_id"):
            record.user_id = user_id_var.get() or "-"  # type: ignore[attr-defined]
        if not hasattr(record, "world"):
            record.world = world_var.get() or "-"  # type: ignore[attr-defined]
        return True
