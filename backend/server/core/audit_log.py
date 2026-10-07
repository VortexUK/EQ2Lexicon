"""Single API for audit-trail log lines: ``audit_log(action, actor, **fields)``
on the dedicated ``eq2.audit`` logger. Every value is CR/LF-scrubbed so a
hostile argument can't inject fake audit lines.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.core.log_safety import scrub
from backend.server.core.request_context import request_id_var, world_var

# Dedicated logger — set to INFO regardless of LOG_LEVEL so audit rows always
# emit, even if the operator cranked the app to WARNING for debug noise reduction.
_log = logging.getLogger("eq2.audit")
_log.setLevel(logging.INFO)


# Fields that Python's logging.LogRecord already defines — passing them in
# extra= raises KeyError("Attempt to overwrite %r in LogRecord").  We rename
# any clashing caller-supplied key to ``<key>_`` so the value is preserved
# without conflicting with the logging machinery.
_LOGRECORD_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "relativeCreated",
        "thread",
        "threadName",
        "process",
        "processName",
        "pathname",
        "filename",
        "module",
        "funcName",
        "lineno",
        "levelname",
        "levelno",
        "exc_info",
        "exc_text",
        "stack_info",
        "taskName",
        "message",
    }
)


def audit_log(action: str, actor: str | None, **fields: Any) -> None:
    """Emit a stable-shape audit-trail INFO record:
    ``audit: <action> actor=<actor>`` plus a flat ``extra=`` dict of every field.

    ``action`` is a snake_case event name (``claim_approved``); ``actor`` is
    the acting discord_id, None for system events. Field names that clash
    with LogRecord attributes are renamed ``<key>_``.
    """
    safe_fields: dict[str, Any] = {(f"{k}_" if k in _LOGRECORD_RESERVED else k): scrub(v) for k, v in fields.items()}
    safe_fields["action"] = action
    safe_fields["actor"] = scrub(actor) if actor else "-"
    safe_fields["request_id"] = request_id_var.get() or "-"
    safe_fields["world"] = world_var.get() or "-"
    # The message string is intentionally short — dashboards filter on
    # extra= fields, not on message body. Keep the human-readable part
    # action + actor so text logs are still grep-friendly.
    _log.info("audit: %s actor=%s", action, scrub(actor) if actor else "-", extra=safe_fields)
