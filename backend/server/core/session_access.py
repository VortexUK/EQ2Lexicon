"""Session-cookie access gate.

The session cookie is a signed, stateless blob, so on its own a login lasts
until the cookie expires regardless of what an admin does to the account.
This middleware runs right inside ``SessionMiddleware`` and, for any request
carrying a session user, checks the account row:

* ``session_epoch`` must match the epoch stamped into the cookie at login —
  kick / deny bump the row's epoch, which kills every live cookie for that
  user at once. A missing row (erased account) is dead too.
* ``access_status`` must be ``approved`` for anything outside ``/api/auth/``,
  so a pending or denied account can still see who it is (``/auth/me``), log
  out or erase itself, but cannot use the site. Admins (``ADMIN_DISCORD_IDS``)
  pass the status check — never the epoch check.

A failed check pops ``user`` from the session; SessionMiddleware then sends
the clearing Set-Cookie and the route sees an anonymous request (401).
Results are cached per user for ``CACHE_TTL`` seconds; the admin routes that
change status or epoch call :func:`invalidate` so the change is immediate.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import psycopg

from backend.server import db as users_db
from backend.server.auth_deps import ADMIN_IDS
from backend.server.core.request_context import user_id_var

_log = logging.getLogger(__name__)

CACHE_TTL = 60.0

#: Tests inject fake session users that have no ``users`` row; the conftest
#: turns enforcement off except where a test opts in.
ENFORCE = True

# Paths a pending/denied account may still reach with its session intact.
_STATUS_EXEMPT_PREFIX = "/api/auth/"

# discord_id -> (expires_at, (access_status, session_epoch) | None); None = no row.
_cache: dict[str, tuple[float, tuple[str, int] | None]] = {}


def invalidate(discord_id: str) -> None:
    _cache.pop(discord_id, None)


def clear_cache() -> None:
    _cache.clear()


async def _lookup(discord_id: str) -> tuple[str, int] | None:
    now = time.monotonic()
    hit = _cache.get(discord_id)
    if hit is not None and hit[0] > now:
        return hit[1]
    row = await users_db.get_session_access(discord_id)
    _cache[discord_id] = (now + CACHE_TTL, row)
    return row


async def check_session(user: dict[str, Any], path: str) -> str:
    """Return ``"ok"``, ``"revoked"`` (epoch mismatch / no row) or
    ``"not_approved"`` (row exists, status not approved, path not exempt)."""
    discord_id = str(user.get("id") or "")
    if not discord_id:
        return "revoked"
    row = await _lookup(discord_id)
    if row is None:
        return "revoked"
    access_status, epoch = row
    if int(user.get("epoch") or 0) != int(epoch):
        return "revoked"
    if access_status == "approved" or discord_id in ADMIN_IDS:
        return "ok"
    if path.startswith(_STATUS_EXEMPT_PREFIX):
        return "ok"
    return "not_approved"


class SessionAccessMiddleware:
    """Pure-ASGI; must sit INSIDE SessionMiddleware (added to the app before it)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        session = scope.get("session")
        if not isinstance(session, dict):
            await self.app(scope, receive, send)
            return
        user = session.get("user")
        if user and ENFORCE:
            try:
                verdict = await check_session(user, scope.get("path", ""))
            except psycopg.Error as exc:
                # The request will almost certainly fail downstream anyway;
                # don't turn a DB blip into a site-wide logout.
                _log.warning("[session-access] lookup failed, allowing request: %s", exc)
                verdict = "ok"
            if verdict != "ok":
                _log.info(
                    "[session-access] %s session dropped: user_id=%s path=%s",
                    verdict,
                    user.get("id"),
                    scope.get("path"),
                )
                session.pop("user", None)
                user = None
        # This is the first layer that sees a decoded, validated session, so
        # it is where the per-request log context learns who the user is
        # (RequestContextMiddleware runs outside SessionMiddleware).
        uid_token = user_id_var.set(str(user["id"])) if user and user.get("id") else None
        try:
            await self.app(scope, receive, send)
        finally:
            if uid_token is not None:
                user_id_var.reset(uid_token)
