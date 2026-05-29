"""Regression: HMAC validation must survive a body-rewriting middleware
between SessionMiddleware and the ingest route.

The strict-mode HMAC check (web/routes/parses._validate_payload_signature)
reads ``request.body()`` after FastAPI has already injected the body into the
handler signature. Starlette caches the wire bytes so the second read is
free — but if any future middleware reads the body via the ASGI receive()
loop without preserving the cache, the bytes the HMAC hashes diverge from
the bytes the body model parsed, and every upload 401s.

This test inserts a no-op BaseHTTPMiddleware that calls ``await
request.body()`` and re-emits a response, then exercises the happy-path
ingest. If the test breaks, the middleware-ordering assumption documented
at parses.py:1324-1326 needs revisiting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware


class _NoOpBodyReadingMiddleware(BaseHTTPMiddleware):
    """Reads body, then forwards unchanged — proves the cache survives."""

    async def dispatch(self, request: Request, call_next):
        _ = await request.body()  # The exact pattern a debugging middleware would use.
        return await call_next(request)


def _minimal_payload() -> dict:
    return {
        "logger_name": "Menludiir",
        "logger_server": "Varsoon",
        "encounter": {
            "encid": "HMAC1234",
            "title": "a krait patriarch",
            "zone": "Great Divide",
            "starttime": "2026-05-24 13:51:56",
            "endtime": "2026-05-24 13:52:42",
            "duration": 46,
            "damage": 502718,
            "encdps": 10928.65,
            "kills": 4,
            "deaths": 0,
        },
        "combatants": [
            {
                "name": "Menludiir",
                "ally": "T",
                "starttime": "2026-05-24 13:51:56",
                "endtime": "2026-05-24 13:52:43",
                "duration": 47,
                "damage": 502718,
                "damageperc": "100%",
                "kills": 4,
                "healed": 11637,
                "healedperc": "100%",
                "critheals": 1,
                "heals": 40,
                "curedispels": 0,
                "powerdrain": 0,
                "powerreplenish": 0,
                "dps": 10696.13,
                "encdps": 10928.65,
                "enchps": 252.98,
                "hits": 132,
                "crithits": 123,
                "blocked": 0,
                "misses": 0,
                "swings": 132,
                "healstaken": 11637,
                "damagetaken": 27557,
                "deaths": 0,
                "tohit": 100.0,
                "critdamperc": "93%",
                "crithealperc": "3%",
                "crittypes": "0.8%L - 0.0%F - 0.0%M",
                "threatstr": "+(0)20000/-(0)0",
                "threatdelta": 20000,
            },
        ],
        "damage_types": [],
        "attack_types": [],
    }


def _sign(body_bytes: bytes, token: str) -> str:
    return hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


async def _fake_require_user(request):
    return {"id": "discord-123", "username": "alice", "auth_source": "token"}


@pytest.mark.asyncio
async def test_hmac_validation_survives_body_reading_middleware(app) -> None:
    """Inject a no-op body-reading middleware and verify the happy-path
    ingest still returns 201. If Starlette's body cache is broken by the
    middleware chain, the HMAC will mismatch and ingest will 401 instead.

    This test pins the assumption documented in parses.py's
    _validate_payload_signature comment — see the ASSUMPTION block there.
    """
    # Inject the middleware AFTER the app is created. Starlette adds
    # middleware in reverse order (last-added = outermost), so this
    # middleware will run before the route but after SessionMiddleware.
    app.add_middleware(_NoOpBodyReadingMiddleware)

    token = "eq2c_middleware_test"
    payload = _minimal_payload()
    body_bytes = json.dumps(payload).encode("utf-8")
    signature = _sign(body_bytes, token)

    sync_result = ("inserted", 42, 2, 0, 0)
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/parses/ingest",
                content=body_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Lexicon-Signature": signature,
                    "Content-Type": "application/json",
                },
            )

    assert res.status_code == 201, res.text
