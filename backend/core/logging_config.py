"""Centralised logging configuration.

Audit recommendations C + LOG-048, LOG-049, LOG-051, LOG-070:
  - Reads LOG_LEVEL from env (default INFO) — no more hardcoded basicConfig.
  - Reads LOG_FORMAT from env: "text" (default, human-readable) or "json"
    (one record per line, for Railway / structured aggregators).
  - Pins third-party logger levels so discord.py / aiohttp / uvicorn don't
    flood logs on a future library upgrade default change.
  - Installs RequestContextFilter on the root handler so every record gets
    request_id / user_id / world fields (the format string references them).
  - Emits one INFO at the end so the operator can confirm the level/format
    that's active without grepping env vars.

Called twice in the deployment:
  - web/app.py:lifespan startup — once per web process.
  - bot/bot.py:setup_hook — once per bot process.
Both use force=True semantics — re-applies even if uvicorn or another
library already touched the root logger.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from backend.server.core.request_context import RequestContextFilter

# Public so tests can assert which value is in use.
DEFAULT_LEVEL = "INFO"
DEFAULT_FORMAT = "text"

# Format string for text mode — adds the contextvar fields at the front of
# every line so a typical record reads:
#   2026-05-30 14:23:01.123  INFO     [req=abc12345 user=287… world=Varsoon]  [admin] Claim approved: …
_TEXT_FORMAT = (
    "%(asctime)s.%(msecs)03d  %(levelname)-8s  [req=%(request_id)s user=%(user_id)s world=%(world)s]  %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _JsonFormatter(logging.Formatter):
    """One-line-per-record JSON formatter for Railway / log-aggregator pickup.

    Keeps the keys flat (no nested objects in the standard fields) so a
    cheap line-shape parser at the aggregator doesn't have to descend.
    Anything passed via `extra=` ends up at the top level too — including
    the contextvar fields injected by RequestContextFilter.
    """

    _STANDARD = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, _DATE_FORMAT),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Stamp every non-standard attribute (LoggerAdapter extras, contextvars,
        # explicit extra= kwargs) onto the top level.
        for k, v in record.__dict__.items():
            if k not in self._STANDARD and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Apply project-wide logging config. Idempotent + safe to call twice.

    Reads:
      - LOG_LEVEL (default INFO). Bad values fall back to INFO + warn once.
      - LOG_FORMAT (default text). Bad values fall back to text.
    """
    level_name = os.getenv("LOG_LEVEL", DEFAULT_LEVEL).upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
        level_name = DEFAULT_LEVEL

    fmt_name = os.getenv("LOG_FORMAT", DEFAULT_FORMAT).lower()
    if fmt_name not in {"text", "json"}:
        fmt_name = DEFAULT_FORMAT

    handler = logging.StreamHandler()
    handler.addFilter(RequestContextFilter())
    if fmt_name == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    # Wipe any handlers a framework already installed (force=True semantics).
    root.handlers[:] = [handler]

    # Third-party logger levels — LOG-070 + LOG-048. Pin to WARNING so a
    # future library default change doesn't silently flood logs.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # LOG-051: announce the config so operators don't have to grep env.
    logging.getLogger("eq2.startup").info(
        "Logging configured: level=%s format=%s",
        level_name,
        fmt_name,
    )
