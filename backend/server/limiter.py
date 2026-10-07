"""
Shared rate-limiter instance.

Import `limiter` in route modules and apply @limiter.limit("N/minute")
decorators to endpoints that trigger expensive downstream calls (Census API,
catalogue searches). Buckets are keyed per logged-in user, else per real
client IP (``request_rate_key``).

The instance must also be registered on the FastAPI app in server/app.py:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
"""

from __future__ import annotations

import hashlib

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def client_ip(request: Request) -> str:
    """The end client's IP as seen through the proxies in front of the app.

    Behind Railway's edge (and Cloudflare in front of it) ``client.host`` is
    the proxy, so an IP-keyed limit collapsed to ONE shared bucket for the
    whole site. Cloudflare's ``CF-Connecting-IP`` is authoritative for traffic
    via the public domain; the first ``X-Forwarded-For`` hop covers direct
    Railway-domain traffic; the TCP peer is the last resort (dev/tests).
    A spoofed header can only earn its sender a bucket of their own — it
    can never collapse other clients into one."""
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    return get_remote_address(request)


def _session_user_id(request: Request) -> str | None:
    try:
        user = request.session.get("user")
    except (AssertionError, KeyError):
        return None  # SessionMiddleware not in play
    return str(user["id"]) if user and user.get("id") else None


def request_rate_key(request: Request) -> str:
    """Default limiter key: the logged-in user, else the real client IP."""
    uid = _session_user_id(request)
    return f"usr:{uid}" if uid else f"ip:{client_ip(request)}"


limiter = Limiter(key_func=request_rate_key, default_limits=[])


def upload_rate_key(request: Request) -> str:
    """Rate-limit key for authenticated write endpoints (parse ingest/tamper).

    Keys on the AUTHENTICATED IDENTITY: the bearer token (hashed) or the
    session user id, so the bucket is per-user regardless of source IP.
    Falls back to the real client IP only for the unauthenticated case (which
    these endpoints reject anyway)."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[len("bearer ") :].strip()
        if token:
            return "tok:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    uid = _session_user_id(request)
    if uid:
        return "usr:" + uid
    return "ip:" + client_ip(request)


# Same keying for authenticated non-upload writes (officer/admin actions).
user_rate_key = upload_rate_key
