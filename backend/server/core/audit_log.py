"""Single API for audit-trail log lines: ``audit_log(action, actor, **fields)``.

Audit recommendation B + every Phase 1 LOG-001..LOG-013 finding's destination.

Why a dedicated helper instead of hand-rolling each route's `_log.info(...)`:
  - Forces a stable schema: every audit row has `action`, `actor`,
    `request_id`, `world`, plus the action's own fields. A dashboard rule
    like `action=claim_approved` works the same across the codebase.
  - Centralises the CR/LF scrub so a hostile name argument can't inject
    fake audit lines.
  - One grep target (`audit_log("...`) maps every privileged event in the
    codebase. P0 forensics become a `git grep` away.

Logger name `eq2.audit` is filterable by aggregators independently of the
application loggers — set up a separate sink/index for it in production if
you want a tamper-evident audit trail.
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
    """Emit a stable-shape audit-trail INFO record.

    Args:
        action: snake_case event name. Examples:
            ``claim_approved`` ``role_granted`` ``user_kicked``
            ``server_settings_updated`` ``parse_purged``.
        actor: discord_id of the actor (admin who did it). None for system /
            background events (rare — most audit lines have a human actor).
        **fields: arbitrary key/value pairs — every value is ``str()``'d
            and CR/LF-scrubbed before logging.

    Shape:
        Emitted message: ``audit: <action> actor=<actor>`` plus a flat
        ``extra=`` dict carrying every field. The format string in
        logging_config.py picks ``request_id`` and ``world`` up from the
        contextvar via the filter.

    Example:
        audit_log(
            "claim_approved",
            actor=admin["id"],
            claim_id=claim_id,
            character=result["character_name"],
            discord_id=result["discord_id"],
        )
    """
    safe_fields: dict[str, Any] = {(f"{k}_" if k in _LOGRECORD_RESERVED else k): scrub(v) for k, v in fields.items()}
    safe_fields["action"] = action
    safe_fields["actor"] = actor or "-"
    safe_fields["request_id"] = request_id_var.get() or "-"
    safe_fields["world"] = world_var.get() or "-"
    # The message string is intentionally short — dashboards filter on
    # extra= fields, not on message body. Keep the human-readable part
    # action + actor so text logs are still grep-friendly.
    _log.info("audit: %s actor=%s", action, actor or "-", extra=safe_fields)
