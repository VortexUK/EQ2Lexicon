"""Tests for POST /api/parses/ingest — the bearer-token upload endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_payload(encid: str = "ABCD1234", logger_server: str | None = "Varsoon") -> dict:
    """Smallest payload that should pass validation + ingest.

    Defaults logger_server to a value on the ALLOWED_SERVERS allowlist
    so the strict server gate (active since the introduction of the
    allowlist) doesn't trip on tests that don't care about the field.
    Pass logger_server=None to build the pre-v0.1.10 shape for tests
    that explicitly exercise the strict gate."""
    payload = {
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
    # Only stamp logger_server when the caller asked for one — passing
    # None lets a test build the pre-v0.1.10 shape to drive the strict
    # gate.
    if logger_server is not None:
        payload["logger_server"] = logger_server
    return payload


async def _fake_require_user(request):
    return {"id": "discord-123", "username": "alice", "auth_source": "token"}


def _sign(body_bytes: bytes, token: str) -> str:
    """Match what PayloadSigner.Sign does on the plugin side — lowercase
    hex HMAC-SHA256, key = utf-8 bytes of the bearer token."""
    return hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def _signed_post_kwargs(payload: dict, token: str = "eq2c_test_token") -> dict:
    """Build the AsyncClient.post(**kwargs) dict that a real v0.1.8+
    plugin upload would produce — raw `content` bytes so we control the
    exact bytes we hash, matching headers (Authorization + Content-Type
    + X-Lexicon-Signature). Use this for any test where the signature
    SHOULD validate; tests that probe the absent/wrong cases build the
    headers by hand instead."""
    body_bytes = json.dumps(payload).encode("utf-8")
    return {
        "content": body_bytes,
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Lexicon-Signature": _sign(body_bytes, token),
        },
    }


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
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "web.routes.parses._ingest_payload_sync",
            new=MagicMock(return_value=sync_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))

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
async def test_ingest_does_not_block_on_census_and_schedules_background(app):
    # Sync path uses cache-only snapshots (no Census); full resolution is
    # scheduled as a background task that runs after the response.
    sync_result = ("inserted", 42, 2, 1, 2)
    resolve_mock = AsyncMock(return_value={})  # background resolver
    cached_mock = MagicMock(return_value={})  # cache-only sync path
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("web.routes.parses._cached_snapshots", cached_mock),
        patch("web.routes.parses._resolve_combatant_snapshots", resolve_mock),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
        patch("web.routes.parses._update_snapshots_sync", new=MagicMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))
    assert r.status_code == 201
    cached_mock.assert_called_once()  # sync path used cache-only resolver
    resolve_mock.assert_awaited()  # background full resolution ran (after response)


@pytest.mark.asyncio
async def test_ingest_returns_skipped_on_duplicate(app):
    sync_result = ("skipped", 7, 0, 0, 0)

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "web.routes.parses._ingest_payload_sync",
            new=MagicMock(return_value=sync_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))

    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "skipped"
    assert data["encounter_id"] == 7
    assert data["combatants"] == 0


@pytest.mark.asyncio
async def test_ingest_revived_status_schedules_background(app):
    # A 'revived' status (re-upload of a soft-deleted parse) must also
    # schedule the background snapshot resolution, same as 'inserted'.
    sync_result = ("revived", 7, 0, 0, 0)
    resolve_mock = AsyncMock(return_value={})  # background resolver
    cached_mock = MagicMock(return_value={})  # cache-only sync path
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("web.routes.parses._cached_snapshots", cached_mock),
        patch("web.routes.parses._resolve_combatant_snapshots", resolve_mock),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
        patch("web.routes.parses._update_snapshots_sync", new=MagicMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))
    assert r.status_code == 201
    assert r.json()["status"] == "revived"
    resolve_mock.assert_awaited()  # background full resolution ran on revive too


@pytest.mark.asyncio
async def test_reupload_of_soft_deleted_parse_revives_it(tmp_path, monkeypatch):
    from parses import db as pdb
    from web.routes.parses import IngestRequest, _ingest_payload_sync

    db_file = tmp_path / "parses.db"
    monkeypatch.setattr(pdb, "DB_PATH", db_file)
    # init the schema
    pdb.init_db(db_file).close()

    payload = IngestRequest(**_minimal_payload())

    # First ingest → inserted.
    status, eid, *_ = _ingest_payload_sync(payload, "Menludiir", "Exordium", "plugin:123", {})
    assert status == "inserted" and eid is not None

    # Soft-delete it (as the delete route does for a boss kill).
    conn = pdb.init_db(db_file)
    try:
        pdb.soft_delete_encounter(conn, eid, hidden_at=1700000000)
        assert pdb.find_encounter_by_act_encid(conn, payload.encounter.encid)["hidden_at"] is not None
    finally:
        conn.close()

    # Re-upload the same encounter → revived + un-hidden.
    status2, eid2, *_ = _ingest_payload_sync(payload, "Menludiir", "Exordium", "plugin:123", {})
    assert status2 == "revived" and eid2 == eid
    conn = pdb.init_db(db_file)
    try:
        assert pdb.find_encounter_by_act_encid(conn, payload.encounter.encid)["hidden_at"] is None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_ingest_rejects_empty_logger_name(app):
    payload = _minimal_payload()
    payload["logger_name"] = "   "

    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    # Pydantic min_length=1 may catch this (non-empty before strip), but our
    # explicit empty-after-strip check should give 400.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_ingest_validates_payload_shape(app):
    """Missing required encounter field → 422 from Pydantic."""
    payload = {"logger_name": "Menludiir"}  # no `encounter`

    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
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
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "web.routes.parses._ingest_payload_sync",
            new=MagicMock(return_value=sync_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Real auth + signature gate both run here — sign with the
            # same token that's in the Authorization header.
            r = await client.post(
                "/api/parses/ingest",
                **_signed_post_kwargs(_minimal_payload(), token="eq2c_anything"),
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
# X-Lexicon-Signature (HMAC) — strict validation
# ---------------------------------------------------------------------------
# Plugin v0.1.8+ ships X-Lexicon-Signature = HMAC-SHA256(body, api_token).
# Server-side validation is STRICT (flipped from opportunistic 2026-05-25):
#   * token-auth + header missing  → 401 (force plugin update)
#   * token-auth + header present  → must verify; mismatch is 401
#   * session-auth + header present → 400 (confused client)
#   * session-auth + header absent → allowed (no key to validate against)
#
# Most other tests in this file use _signed_post_kwargs() which already
# signs correctly. The tests below build the request by hand because
# they probe the EDGE cases (wrong signature, wrong key, missing header,
# session-auth-with-header) where the helper would obscure the intent.


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
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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
async def test_signature_required_on_token_auth(app):
    """v0.1.7 and earlier plugins don't send X-Lexicon-Signature. In
    STRICT mode they must be rejected with a 401 that names the update
    path, so the user knows what to do."""
    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                headers={"Authorization": "Bearer eq2c_v017_plugin"},
                # NB: no X-Lexicon-Signature header
            )
    assert r.status_code == 401
    # The 401 detail must point at the update — otherwise a v0.1.7 user
    # sees a generic auth error and thinks their token is broken.
    detail = r.json().get("detail", "")
    assert "v0.1.8" in detail
    assert "releases" in detail


# ---------------------------------------------------------------------------
# Defensive validation (v0.1.13 audit follow-ups)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_name",
    [
        "alice:varsoon",  # ":" was the cache-collision injection vector
        "alice/etc",  # path char
        "alice bob",  # space (EQ2 names are single words)
        "alice123",  # digits not allowed in EQ2 names
        "alice'sworld",  # punctuation
        "aliceiswaytoolong16",  # > 15 chars
    ],
)
async def test_ingest_rejects_malformed_logger_name(app, bad_name):
    """logger_name must match the EQ2 character-name shape (1-15
    letters). Defence-in-depth against character_cache key collisions
    and against weird payloads in Census URLs / parses-DB rows."""
    payload = _minimal_payload()
    payload["logger_name"] = bad_name
    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    # Either Pydantic-level (422) or our explicit shape check (400).
    assert r.status_code in (400, 422), r.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "good_name",
    [
        "Alice",
        "Menludiir",
        "Kayleigh",
        "A",  # 1-char edge
        "Aaaaaaaaaaaaaaa",  # 15-char edge
        "menludiir",  # all lowercase
        "MENLUDIIR",  # all uppercase
    ],
)
async def test_ingest_accepts_valid_logger_name_shape(app, good_name):
    """The shape regex must not exclude legitimate EQ2 names."""
    payload = _minimal_payload()
    payload["logger_name"] = good_name
    sync_result = ("inserted", 1, 1, 0, 0)
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201


def test_sanitize_world_predicate():
    """Unit test for _sanitize_world. Plugin-supplied world strings
    must be passed through unchanged when they look like a real EQ2
    server name, or collapsed to None for the caller to fall back to
    EQ2_WORLD. Covers the v0.1.13 audit L2 defence."""
    from web.routes.parses import _sanitize_world

    # Legit EQ2 server names — all pass through unchanged.
    for ok in ("Varsoon", "Kaladim", "Antonia Bayle", "Lucan D'Lere", "Maj'Dul"):
        assert _sanitize_world(ok) == ok, ok

    # Garbage shapes — all collapsed to None.
    for bad in (
        "varsoon:other",  # ":" was the cache-collision vector
        "../etc/passwd",  # path traversal
        "varsoon?c=1",  # URL meta
        "9Varsoon",  # leading digit
        "Varsoon" + "x" * 30,  # > 30 chars
        "",
        None,
        "   ",
        "Varsoon\nKaladim",  # embedded control char (strip is end-only)
    ):
        assert _sanitize_world(bad) is None, repr(bad)

    # Leading/trailing whitespace IS stripped before the regex check —
    # tolerates a typo without breaking the upload. " Varsoon " → "Varsoon".
    assert _sanitize_world(" Varsoon ") == "Varsoon"


# ---------------------------------------------------------------------------
# logger_server (plugin v0.1.10+)
# ---------------------------------------------------------------------------
# Plugin reads the EQ2 server name from its log file's parent directory
# and stamps it on every upload. Server uses it to override EQ2_WORLD
# for the Census guild lookup. Backward compat is preserved by treating
# missing/empty as "fall back to configured default".


@pytest.mark.asyncio
async def test_logger_server_overrides_world_for_census(app):
    """When the plugin stamps logger_server, the Census call must use
    THAT world, not the EQ2_WORLD env-var default. Caught by inspecting
    what _resolve_uploader_guild_async was called with. Uses Wuoshi
    here — it's on the default ALLOWED_SERVERS allowlist and is a
    different world from EQ2_WORLD's default (Varsoon), so this test
    still proves that logger_server propagates rather than being
    silently substituted."""
    captured_worlds: list[str | None] = []

    async def _spy(uploader, world=None):
        captured_worlds.append(world)
        return "Exordium"

    payload = _minimal_payload(logger_server="Wuoshi")

    sync_result = ("inserted", 1, 1, 0, 0)
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=_spy),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))

    assert r.status_code == 201
    assert captured_worlds == ["Wuoshi"]


# Historical "logger_server missing → fall back to EQ2_WORLD" tests
# were removed when the ingest gate flipped to STRICT mode — pre-v0.1.10
# plugin clients are now rejected outright with a 400. The replacement
# rejection-path tests live in the "Strict server-allowlist gate"
# section at the end of this file.


@pytest.mark.asyncio
async def test_signature_absent_with_session_auth_is_allowed(app):
    """Browsers (session-cookie auth) don't have a token-style HMAC key,
    so the strict gate doesn't apply to them. This isn't a realistic
    code path — there's no browser flow that POSTs to /parses/ingest
    today — but pinning the contract prevents an accidental future
    session-cookie regression."""

    async def _fake_session_user(request):
        return {"id": "discord-123", "username": "alice", "auth_source": "session"}

    sync_result = ("inserted", 42, 2, 1, 2)
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_session_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/parses/ingest",
                json=_minimal_payload(),
                # No Authorization header → session-cookie path
                # No X-Lexicon-Signature → permitted on session auth
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


# ---------------------------------------------------------------------------
# _resolve_combatant_snapshots — cache-first per-combatant level/guild/class
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_snapshots_cache_hit_skips_census():
    """A character already in character_cache is snapshotted with zero Census
    traffic — no CensusClient is even constructed."""
    from web.routes import parses as parses_mod

    # A COMPLETE cache hit (ilvl present) skips Census entirely.
    cached = SimpleNamespace(level=90, guild_name="Exordium", cls="Templar", ilvl=372.2)
    fake_cache = MagicMock()
    fake_cache.get_stale.return_value = (cached, 0)

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch.object(parses_mod, "CensusClient", side_effect=AssertionError("Census must not be hit on a cache hit")),
    ):
        out = await parses_mod._resolve_combatant_snapshots(["Menludiir"], "Varsoon")

    assert out["Menludiir"].level == 90
    assert out["Menludiir"].guild_name == "Exordium"
    assert out["Menludiir"].cls == "Templar"
    assert out["Menludiir"].ilvl == 372.2


@pytest.mark.asyncio
async def test_resolve_snapshots_backfills_missing_ilvl():
    """A cache hit with a class but no ilvl (guild resolve omitted equipment)
    triggers a direct get_character to fill the ilvl."""
    from web.routes import parses as parses_mod

    cached = SimpleNamespace(level=92, guild_name="Exordium", cls="Wizard", ilvl=None)
    fake_cache = MagicMock()
    fake_cache.get_stale.return_value = (cached, 0)

    client = MagicMock()
    client.get_character = AsyncMock(return_value=object())  # opaque; _build_char_response is patched
    client.close = AsyncMock()

    filled = SimpleNamespace(level=92, guild_name="Exordium", cls="Wizard", ilvl=355.0)

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch.object(parses_mod, "CensusClient", return_value=client),
        patch("web.routes.character._build_char_response", return_value=filled),
    ):
        out = await parses_mod._resolve_combatant_snapshots(["Wiz"], "Varsoon")

    client.get_character.assert_awaited_once_with("Wiz", "Varsoon")
    assert out["Wiz"].ilvl == 355.0


@pytest.mark.asyncio
async def test_resolve_snapshots_miss_warms_roster_then_hits():
    """On a cache miss: one Census guild lookup, an awaited roster prewarm,
    then a re-check that now hits. This is the path that lets the first
    raider's lookup cover the rest of the (same-guild) raid."""
    from web.routes import parses as parses_mod

    cached = SimpleNamespace(level=88, guild_name="Exordium", cls="Fury", ilvl=410.0)
    fake_cache = MagicMock()
    # 1st check → miss; after prewarm → hit.
    fake_cache.get_stale.side_effect = [(None, 0), (cached, 0)]

    client = MagicMock()
    client.get_character_guild_name = AsyncMock(return_value="Exordium")
    client.close = AsyncMock()

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch.object(parses_mod, "CensusClient", return_value=client),
        patch.object(parses_mod, "_prewarm_guild_silently", new=AsyncMock()) as prewarm,
    ):
        out = await parses_mod._resolve_combatant_snapshots(["Sihtric"], "Varsoon")

    prewarm.assert_awaited_once_with("Exordium")
    client.get_character_guild_name.assert_awaited_once_with("Sihtric", "Varsoon")
    assert out["Sihtric"].level == 88


@pytest.mark.asyncio
async def test_resolve_snapshots_unguilded_miss_is_absent():
    """A character with no resolvable guild (pug / Census miss) is simply
    omitted from the result — its combatant row stores NULLs."""
    from web.routes import parses as parses_mod

    fake_cache = MagicMock()
    fake_cache.get_stale.return_value = (None, 0)  # always a miss

    client = MagicMock()
    client.get_character_guild_name = AsyncMock(return_value=None)
    client.close = AsyncMock()

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch.object(parses_mod, "CensusClient", return_value=client),
    ):
        out = await parses_mod._resolve_combatant_snapshots(["Randompug"], "Varsoon")

    assert out == {}


# ---------------------------------------------------------------------------
# Strict server-allowlist gate
#
# The ingest endpoint refuses any upload whose logger_server is missing,
# malformed, or not on the configured ALLOWED_SERVERS list. Pre-v0.1.10
# plugins (no logger_server) and v0.1.14+ plugins targeting a server the
# site doesn't accept both land here.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_rejects_missing_logger_server(app):
    """Pre-v0.1.10 plugin shape (no logger_server) → 400 with an
    upgrade prompt. The strict mode is intentional — older plugins
    couldn't pass the X-Lexicon-Signature gate anyway, so this is
    just adding a clearer error message."""
    payload = _minimal_payload(logger_server=None)
    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 400
    assert "logger_server is required" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_rejects_empty_logger_server(app):
    """Whitespace-only string → same outcome as null."""
    payload = _minimal_payload(logger_server="   ")
    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ingest_rejects_malformed_logger_server(app):
    """Bad shape (path traversal, control chars, etc.) → 400 malformed,
    not the allowlist-rejection 403 — distinguishes 'your plugin sent
    garbage' from 'your server isn't allowed'."""
    payload = _minimal_payload(logger_server="../etc/passwd")
    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 400
    assert "malformed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_rejects_disallowed_server(app):
    """Well-formed but not on the allowlist → 403, error detail lists
    the configured allowed set so the user knows what they CAN upload
    from."""
    # "Halls of Fate" passes _VALID_WORLD_RE (letters + space) but
    # isn't in the default ALLOWED_SERVERS={Varsoon,Wuoshi}.
    payload = _minimal_payload(logger_server="Halls of Fate")
    with patch("web.routes.parses.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "Halls of Fate" in detail
    assert "Allowed:" in detail


@pytest.mark.asyncio
async def test_ingest_accepts_allowed_server_case_insensitive(app):
    """`varsoon` (lower-case) → accepted. Casing in the log path varies
    by EQ2 install (the directory is sometimes capitalised, sometimes
    not) and we don't want a cosmetic mismatch to reject real uploads."""
    sync_result = ("inserted", 99, 2, 1, 2)
    payload = _minimal_payload(logger_server="varsoon")  # lowercase
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_ingest_accepts_wuoshi(app):
    """Second server in the default allowlist also works. Pin both so a
    future refactor can't accidentally narrow the set to just Varsoon."""
    sync_result = ("inserted", 100, 2, 1, 2)
    payload = _minimal_payload(logger_server="Wuoshi")
    with (
        patch("web.routes.parses.require_user_session_or_token", _fake_require_user),
        patch("web.routes.parses._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("web.routes.parses._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("web.routes.parses._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201
