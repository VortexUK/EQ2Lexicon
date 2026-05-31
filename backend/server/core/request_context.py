"""Per-request context (request_id, user_id, world) propagated via contextvars.

Why contextvars instead of an `extra=` kwarg at every log call site:
  - Existing code is hundreds of `_log.info(...)` lines deep; passing context
    explicitly to each one is a forever-job.
  - asyncio task-local context is exactly what `contextvars` is designed for
    (PEP 567). Inside a request handler, every awaitable spawned from it
    inherits the contextvar values automatically.
  - A `logging.LoggerAdapter` reads the contextvar inside its `process()`
    method, so the addition is invisible to call sites — `_log.info("foo")`
    inside a request gets `[req=abc12345 user=… world=…]` in the formatted
    output without any change to the log site.

Single-process assumption: contextvars don't cross workers. Today the app
runs with WEB_CONCURRENCY=1 (asserted at startup — see web/app.py:_startup).
If that ever loosens, request_id propagation between workers needs an
HTTP-header pickup at the receiving worker too — at which point the middleware
in request_context_middleware.py needs to honour an inbound X-Request-ID
header rather than minting one unconditionally. See LOG-047 + the
single-process note in CLAUDE.md.

The contextvars are intentionally Optional — outside a request (background
task started before a request, CLI script, test harness) the LoggerAdapter
falls back to "-" so log lines stay parseable.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

# Public contextvars. Default None so background tasks + tests don't 500 on
# unset reads. Phase 2b's middleware sets them at request start.
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

    The convention is to assign this at module level just like a plain
    logger::

        from backend.server.core.request_context import get_logger
        _log = get_logger(__name__)

    Plain `logging.getLogger(__name__)` still works — the contextvars are
    also read by the logging-config filter (web/lib/logging_config.py), so
    every log record from any logger gets the contextvar values attached
    via the filter. The adapter exists for routes that want a typed
    handle + auto-context.
    """
    return _RequestContextAdapter(logging.getLogger(name), extra={})


class RequestContextFilter(logging.Filter):
    """Logging filter that injects request_id/user_id/world onto every record.

    Used by `configure_logging()` (web/lib/logging_config.py) on the root
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
