"""Tests for POST /api/parses/ingest — the bearer-token upload endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_payload(encid: str = "ABCD1234") -> dict:
    """Smallest payload that should pass validation + ingest."""
    return {
        "logger_name": "Menludiir",
        "encounter": {
            "encid": encid,
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
            {
                "name": "a krait patriarch",
                "ally": "F",
                "duration": 15,
                "damage": 5716,
                "damageperc": "--",
                "kills": 0,
                "healed": 0,
                "dps": 381.07,
                "encdps": 124.26,
                "hits": 11,
                "swings": 12,
                "deaths": 1,
                "damagetaken": 145877,
                "tohit": 91.67,
            },
        ],
        "damage_types": [
            {
                "combatant": "Menludiir",
                "grouping": "Group 1",
                "type": "divine",
                "damage": 400000,
                "hits": 100,
                "swings": 100,
                "crithits": 90,
                "maxhit": 8000,
                "dps": 8500.0,
                "critperc": "90%",
            },
        ],
        "attack_types": [
            {
                "attacker": "Menludiir",
                "victim": "a krait patriarch",
                "swingtype": 2,
                "type": "Smite",
                "damage": 400000,
                "hits": 100,
                "swings": 100,
                "crithits": 90,
                "maxhit": 8000,
                "minhit": 100,
                "resist": "divine",
                "critperc": "90%",
            },
            # Heal row (swing_type=3) — must come through unchanged
            {
                "attacker": "Menludiir",
                "swingtype": 3,
                "type": "Reverence",
                "damage": 7818,
                "hits": 12,
                "swings": 12,
                "resist": "Hitpoints",
                "critperc": "0%",
            },
            # All rollup — must be filtered out by the ingest path
            {
                "attacker": "Menludiir",
                "swingtype": 100,
                "type": "All",
                "damage": 502718,
                "hits": 132,
                "swings": 132,
                "resist": "All",
                "critperc": "93%",
            },
        ],
    }


async def _fake_require_user(request):
    return {"id": "discord-123", "username": "alice", "auth_source": "token"}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/parses/ingest", json=_minimal_payload())
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_ingest_rejects_bad_bearer_token(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/parses/ingest",
            json=_minimal_payload(),
            headers={"Authorization": "Bearer eq2c_not_real"},
        )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_inserts_encounter(app):
    sync_result = ("inserted", 42, 2, 1, 2)  # (status, eid, n_c, n_dt, n_at)

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch(
            "web.routes.parses._ingest_payload_sync",
            new=MagicMock(return_value=sync_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_anything"},
            )

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "inserted"
    assert data["encounter_id"] == 42
    assert data["combatants"] == 2
    assert data["damage_types"] == 1
    assert data["attack_types"] == 2
    assert data["guild_name"] == "Exordium"
    assert data["act_encid"] == "ABCD1234"


@pytest.mark.asyncio
async def test_ingest_returns_skipped_on_duplicate(app):
    sync_result = ("skipped", 7, 0, 0, 0)

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch(
            "web.routes.parses._ingest_payload_sync",
            new=MagicMock(return_value=sync_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_anything"},
            )

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "skipped"
    assert data["encounter_id"] == 7
    assert data["combatants"] == 0


@pytest.mark.asyncio
async def test_ingest_rejects_empty_logger_name(app):
    payload = _minimal_payload()
    payload["logger_name"] = "   "

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=payload,
                headers={"Authorization": "Bearer eq2c_anything"},
            )
    # Pydantic min_length=1 may catch this (non-empty before strip), but our
    # explicit empty-after-strip check should give 400.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_ingest_validates_payload_shape(app):
    """Missing required encounter field → 422 from Pydantic."""
    payload = {"logger_name": "Menludiir"}  # no `encounter`

    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=payload,
                headers={"Authorization": "Bearer eq2c_anything"},
            )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Bearer auth helper-level integration
# ---------------------------------------------------------------------------
# Coverage at the DB layer lives in test_auth_tokens.py (mint/lookup/revoke
# against a real temp DB). Coverage at the HTTP layer above mocks
# `require_user_session_or_token`. The bearer-token path through
# `web.auth_deps.require_user_session_or_token` is tested below by mocking
# `users_db.lookup_api_token` to exercise the wiring without needing to
# patch web.db.DB_PATH (which is captured at import time).


@pytest.mark.asyncio
async def test_bearer_token_path_resolves_to_user(app):
    """Hitting the upload endpoint with a real-looking Bearer header
    routes through require_user_session_or_token → lookup_api_token."""
    fake_lookup_row = {
        "token_id": 1,
        "user_id": "discord-real",
        "discord_id": "discord-real",
        "discord_name": "RealAlice",
        "discord_username": "realalice",
        "token_name": "Plugin",
        "access_status": "approved",
        "avatar": None,
        "revoked_at": None,
    }
    sync_result = ("inserted", 99, 2, 1, 2)
    with (
        patch(
            "web.auth_deps.users_db.lookup_api_token",
            new=AsyncMock(return_value=fake_lookup_row),
        ),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch(
            "web.routes.parses._ingest_payload_sync",
            new=MagicMock(return_value=sync_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_anything"},
            )
    assert r.status_code == 201
    assert r.json()["status"] == "inserted"


@pytest.mark.asyncio
async def test_bearer_token_revoked_returns_401(app):
    """lookup_api_token returns None for revoked tokens — auth rejects."""
    with patch(
        "web.auth_deps.users_db.lookup_api_token",
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_revoked"},
            )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_unapproved_user_returns_403(app):
    """A token for a user whose access_status isn't 'approved' is rejected
    even though the token itself is valid."""
    pending_row = {
        "token_id": 2,
        "user_id": "discord-pending",
        "discord_id": "discord-pending",
        "discord_name": "PendingBob",
        "discord_username": "pendingbob",
        "token_name": "Plugin",
        "access_status": "pending",
        "avatar": None,
        "revoked_at": None,
    }
    with patch(
        "web.auth_deps.users_db.lookup_api_token",
        new=AsyncMock(return_value=pending_row),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_pendingtoken"},
            )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# X-Lexicon-Signature (HMAC) — opportunistic validation
# ---------------------------------------------------------------------------
# Plugin v0.1.8+ ships X-Lexicon-Signature = HMAC-SHA256(body, api_token).
# Server-side validation is currently OPPORTUNISTIC:
#   * header absent  → accepted (v0.1.7 and earlier kept working)
#   * header present → MUST verify; mismatch is 401
# Tests pin both branches so a future flip to strict mode is a single
# tweak in the route + matching test update, not a hunt across the suite.


def _sign(body_bytes: bytes, token: str) -> str:
    """Match what PayloadSigner.Sign does on the plugin side — lowercase
    hex HMAC-SHA256, key = utf-8 bytes of the bearer token."""
    return hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_signature_accepted_when_correct(app):
    """Happy path: plugin computes the signature, server validates, ingest
    proceeds. Body is serialised the same way httpx will serialise it so
    the bytes match what the server reads via request.body()."""
    payload = _minimal_payload()
    body_bytes = json.dumps(payload).encode("utf-8")
    token = "eq2c_realtoken"
    sig = _sign(body_bytes, token)

    sync_result = ("inserted", 42, 2, 1, 2)
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Send raw content (not json=) so we control the exact bytes
            # the server hashes. json= would let httpx serialise and
            # potentially differ from our local bytes.
            r = await client.post(
                "/api/parses/ingest",
                content=body_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Lexicon-Signature": sig,
                },
            )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_signature_rejected_when_body_tampered(app):
    """Sign one body, ship a different one. This is the attack the HMAC
    is designed to catch — must be a 401."""
    original = _minimal_payload()
    body_bytes = json.dumps(original).encode("utf-8")
    token = "eq2c_realtoken"
    sig = _sign(body_bytes, token)

    # Tamper: change DPS to something inflated.
    tampered = dict(original)
    tampered["encounter"] = {**original["encounter"], "encdps": 99999999.0}
    tampered_bytes = json.dumps(tampered).encode("utf-8")

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                content=tampered_bytes,  # different body
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Lexicon-Signature": sig,  # signature for ORIGINAL body
                },
            )
    assert r.status_code == 401
    assert "signature" in r.text.lower() or "X-Lexicon-Signature" in r.text


@pytest.mark.asyncio
async def test_signature_rejected_when_wrong_key(app):
    """Signature computed with a different token than the bearer. Catches
    the case where someone steals the token but doesn't know they need to
    sign with it too — or, more practically, an out-of-sync replay."""
    body_bytes = json.dumps(_minimal_payload()).encode("utf-8")
    sig_with_wrong_key = _sign(body_bytes, "wrong-token")

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                content=body_bytes,
                headers={
                    "Authorization": "Bearer eq2c_realtoken",
                    "Content-Type": "application/json",
                    "X-Lexicon-Signature": sig_with_wrong_key,
                },
            )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_signature_absent_is_accepted_in_opportunistic_mode(app):
    """v0.1.7 and earlier don't send the header. They MUST keep working
    during the rollout window. This test pins the opportunistic-mode
    contract — flip this expectation when we move to strict mode."""
    sync_result = ("inserted", 42, 2, 1, 2)
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_anything"},
                # NB: no X-Lexicon-Signature header
            )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_signature_with_session_auth_is_rejected(app):
    """A browser (session cookie auth) sending X-Lexicon-Signature would
    be confused — the header has no key to validate against in that
    auth path. Reject with 400 rather than silently accepting."""

    async def _fake_session_user(request):
        return {"id": "discord-123", "username": "alice", "auth_source": "session"}

    body_bytes = json.dumps(_minimal_payload()).encode("utf-8")
    with patch("web.routes.parses.require_user_session_or_token", _fake_session_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Lexicon-Signature": "0" * 64,  # shape-valid but unverifiable
                },
            )
    assert r.status_code == 400
