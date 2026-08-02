"""
Shared rate-limiter instance.

Import `limiter` in route modules and apply @limiter.limit("N/minute")
decorators to endpoints that trigger expensive downstream calls (Census API,
SQLite searches).  The limiter is keyed by client IP address.

The instance must also be registered on the FastAPI app in web/app.py:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
"""

from __future__ import annotations

import hashlib

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

limiter = Limiter(key_func=get_remote_address, default_limits=[])


def upload_rate_key(request: Request) -> str:
    """Rate-limit key for authenticated write endpoints (parse ingest/tamper).

    Keys on the AUTHENTICATED IDENTITY, not the client IP. Behind the Railway
    edge proxy every request's ``client.host`` collapses to one proxy IP, so an
    IP-keyed limit is effectively global (one abuser 429s everyone). Keying on
    the bearer token (hashed) or the session user id makes the bucket per-user
    regardless of source IP. Falls back to IP only for the unauthenticated case
    (which these endpoints reject anyway)."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[len("bearer ") :].strip()
        if token:
            return "tok:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    try:
        user = request.session.get("user")
    except (AssertionError, KeyError):
        user = None  # SessionMiddleware not in play (shouldn't happen on these routes)
    if user and user.get("id"):
        return "usr:" + str(user["id"])
    return "ip:" + get_remote_address(request)
