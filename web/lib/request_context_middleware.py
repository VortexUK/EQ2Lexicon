"""Starlette middleware: mint request_id, set contextvars, echo X-Request-ID.

Runs as early as possible in the middleware chain so every downstream
handler (auth, server context, route handler) sees the context populated.

Reads:
  - Inbound `X-Request-ID` if the client sent one — useful for client-side
    correlation (a frontend can stamp its own UUID and we honour it). Falls
    back to minting a fresh UUID4.
  - `request.session["user"]["id"]` if SessionMiddleware already populated
    it. Falls back to None.
  - `web.server_context.current_world()` (read AFTER ServerContextMiddleware
    has run — see Phase 2b for the install ordering).

Writes:
  - `X-Request-ID` on the response.
  - Stamps a token onto each contextvar on entry; resets on exit so
    background tasks spawned after the response don't inherit stale values
    on the next request.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from web.lib.request_context import request_id_var, user_id_var

_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Mint + propagate per-request context.

    Install BEFORE ServerContextMiddleware so the request_id is set on the
    contextvar before ServerContextMiddleware reads it (so its own log lines
    carry the request_id). Install AFTER SessionMiddleware so we can read
    request.session["user"] for the user_id stamp.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        inbound = request.headers.get(_HEADER)
        rid = inbound if inbound and len(inbound) <= 64 else uuid.uuid4().hex[:16]

        # Best-effort user_id pickup. SessionMiddleware may not have run on
        # an unauthenticated request — that's fine; user_id falls back to "-".
        uid: str | None = None
        try:
            session_user = request.session.get("user")  # type: ignore[union-attr]
            if isinstance(session_user, dict):
                uid = session_user.get("id")
        except Exception:
            uid = None  # SessionMiddleware not installed for this scope

        # World pickup runs AFTER ServerContextMiddleware. We can't rely on
        # it here because middleware execution is bottom-up — the dispatch
        # of THIS middleware runs first, but the request_id needs to be set
        # before the next middleware (ServerContextMiddleware) reads it.
        # So we set world to None initially; the route handler's logs will
        # see the world via current_world() through the filter (which reads
        # the contextvar from web/server_context.py, not from here).
        rid_token = request_id_var.set(rid)
        uid_token = user_id_var.set(uid)
        # world_var stays at its outer value (None outside a request).
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(rid_token)
            user_id_var.reset(uid_token)

        response.headers[_HEADER] = rid
        return response
