"""Starlette middleware: mint request_id, set contextvars, echo X-Request-ID.

An inbound ``X-Request-ID`` (<= 64 chars) is honoured, otherwise one is
minted. The contextvars are reset on exit so later background tasks don't
inherit stale values.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from backend.server.core.request_context import request_id_var, user_id_var

_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Mint + propagate the per-request ``request_id``.

    Runs OUTSIDE SessionMiddleware and ServerContextMiddleware (it is added
    after them, and Starlette runs add_middleware calls in reverse), so the
    request_id exists before either of them logs. The session is not decoded
    yet at this layer — ``user_id`` is stamped by SessionAccessMiddleware
    (inside the session layer) and ``world`` by ServerContextMiddleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        inbound = request.headers.get(_HEADER)
        rid = inbound if inbound and len(inbound) <= 64 else uuid.uuid4().hex[:16]

        rid_token = request_id_var.set(rid)
        uid_token = user_id_var.set(None)  # inner layers set it once the session is decoded
        # Also on the ASGI scope (request.state is scope["state"]) — it
        # outlives the contextvar reset below, so the outermost
        # UnhandledErrorMiddleware can still stamp the id on its one-liner.
        request.state.request_id = rid
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(rid_token)
            user_id_var.reset(uid_token)

        response.headers[_HEADER] = rid
        return response
