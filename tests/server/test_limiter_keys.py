"""Rate-limit buckets must be per client, not per proxy.

Behind Railway (and Cloudflare) every request's TCP peer is the proxy, so the
old IP-keyed default collapsed ~60 limits into one site-wide bucket — one
scraper 429'd everyone. The key now prefers the session user, then the real
client IP from the proxy headers."""

from __future__ import annotations

from starlette.requests import Request

from backend.server.limiter import client_ip, request_rate_key, upload_rate_key


def _request(headers: dict[str, str] | None = None, *, peer: str = "10.0.0.1", session: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/x",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 1234),
    }
    if session is not None:
        scope["session"] = session
    return Request(scope)


def test_client_ip_prefers_cloudflare_then_xff_then_peer():
    assert client_ip(_request({"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "198.51.100.2"})) == "203.0.113.9"
    assert client_ip(_request({"X-Forwarded-For": "198.51.100.2, 10.0.0.1"})) == "198.51.100.2"
    assert client_ip(_request()) == "10.0.0.1"


def test_two_clients_behind_one_proxy_get_separate_buckets():
    a = request_rate_key(_request({"X-Forwarded-For": "198.51.100.2"}))
    b = request_rate_key(_request({"X-Forwarded-For": "198.51.100.3"}))
    assert a != b and a.startswith("ip:")


def test_logged_in_user_is_keyed_by_identity_not_ip():
    r1 = _request({"X-Forwarded-For": "198.51.100.2"}, session={"user": {"id": "u1"}})
    r2 = _request({"X-Forwarded-For": "198.51.100.3"}, session={"user": {"id": "u1"}})
    assert request_rate_key(r1) == request_rate_key(r2) == "usr:u1"


def test_upload_key_order_token_then_session_then_ip():
    tok = _request({"Authorization": "Bearer eq2c_abc"}, session={"user": {"id": "u1"}})
    assert upload_rate_key(tok).startswith("tok:")
    assert upload_rate_key(_request(session={"user": {"id": "u1"}})) == "usr:u1"
    assert upload_rate_key(_request({"X-Forwarded-For": "198.51.100.2"})) == "ip:198.51.100.2"
