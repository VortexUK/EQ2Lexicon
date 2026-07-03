"""Tests for POST /api/parses/ingest — the bearer-token upload endpoint."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.server._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _sign,
    _signed_post_kwargs,
)

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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "backend.server.api.parses.ingest._ingest_payload_sync",
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._cached_snapshots", cached_mock),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", resolve_mock),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
        patch("backend.server.api.parses.ingest._update_snapshots_sync", new=MagicMock()),
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "backend.server.api.parses.ingest._ingest_payload_sync",
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._cached_snapshots", cached_mock),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", resolve_mock),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
        patch("backend.server.api.parses.ingest._update_snapshots_sync", new=MagicMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))
    assert r.status_code == 201
    assert r.json()["status"] == "revived"
    resolve_mock.assert_awaited()  # background full resolution ran on revive too


@pytest.mark.asyncio
async def test_reupload_of_soft_deleted_parse_revives_it(tmp_path, monkeypatch):
    from backend.server.api.parses import IngestRequest
    from backend.server.api.parses.ingest import _ingest_payload_sync
    from backend.server.parses import db as pdb

    db_file = tmp_path / "backend.server.parses.db"
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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

    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 422


def test_ingest_accepts_numeric_percent_fields():
    """Regression: some plugin builds emit the *perc fields as bare numbers
    instead of strings. Pydantic v2 won't coerce number → str on its own, which
    422'd the whole upload (combatants/damage_types/attack_types .crit*perc).
    coerce_numbers_to_str on the ingest row models accepts them; the values are
    stored as strings and parse back to the right percentage."""
    from backend.server.api.parses import IngestRequest
    from backend.server.parses.models import _to_perc

    payload = _minimal_payload()
    payload["combatants"][0]["critdamperc"] = 93.0
    payload["combatants"][0]["crithealperc"] = 3
    payload["damage_types"][0]["critperc"] = 90
    payload["attack_types"][0]["critperc"] = 90.5

    req = IngestRequest(**payload)  # must not raise

    for v in (
        req.combatants[0].critdamperc,
        req.combatants[0].crithealperc,
        req.damage_types[0].critperc,
        req.attack_types[0].critperc,
    ):
        assert isinstance(v, str), f"expected str after coercion, got {type(v)}"
    assert _to_perc(req.combatants[0].critdamperc) == 93.0
    assert _to_perc(req.damage_types[0].critperc) == 90.0
    assert _to_perc(req.attack_types[0].critperc) == 90.5


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
            "backend.server.auth_deps.users_db.lookup_api_token",
            new=AsyncMock(return_value=fake_lookup_row),
        ),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "backend.server.api.parses.ingest._ingest_payload_sync",
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
        "backend.server.auth_deps.users_db.lookup_api_token",
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
        "backend.server.auth_deps.users_db.lookup_api_token",
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
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
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201


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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=_spy),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_session_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
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
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_session_user):
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
    API calls — the shared client is obtained but its Census methods are not
    called when every combatant is already in the cache with a non-None ilvl."""
    from backend.server.api.parses import ingest as parses_mod

    # A COMPLETE cache hit (ilvl present) skips Census API calls entirely.
    cached = SimpleNamespace(level=90, guild_name="Exordium", cls="Templar", ilvl=372.2)
    fake_cache = MagicMock()
    fake_cache.get_stale.return_value = (cached, 0)

    mock_client = MagicMock()
    mock_client.get_character_guild_name = AsyncMock(
        side_effect=AssertionError("get_character_guild_name must not be called on cache hit")
    )
    mock_client.get_character = AsyncMock(
        side_effect=AssertionError("get_character must not be called on cache hit with ilvl")
    )

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_client),
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
    from backend.server.api.parses import ingest as parses_mod

    cached = SimpleNamespace(level=92, guild_name="Exordium", cls="Wizard", ilvl=None)
    fake_cache = MagicMock()
    fake_cache.get_stale.return_value = (cached, 0)

    client = MagicMock()
    client.get_character = AsyncMock(return_value=object())  # opaque; _build_char_response is patched
    client.close = AsyncMock()

    # Real CharacterResponse is pydantic (has model_dump); the ilvl-backfill now
    # writes it through to census_store, so the fake needs model_dump too.
    class _Filled(SimpleNamespace):
        def model_dump(self):
            return dict(self.__dict__)

    filled = _Filled(level=92, guild_name="Exordium", cls="Wizard", ilvl=355.0)

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=client),
        patch("backend.server.api.character._build_char_response", return_value=filled),
    ):
        out = await parses_mod._resolve_combatant_snapshots(["Wiz"], "Varsoon")

    client.get_character.assert_awaited_once_with("Wiz", "Varsoon")
    assert out["Wiz"].ilvl == 355.0


@pytest.mark.asyncio
async def test_resolve_snapshots_miss_warms_roster_then_hits():
    """On a cache miss: one Census guild lookup, an awaited roster prewarm,
    then a re-check that now hits. This is the path that lets the first
    raider's lookup cover the rest of the (same-guild) raid."""
    from backend.server.api.parses import ingest as parses_mod

    cached = SimpleNamespace(level=88, guild_name="Exordium", cls="Fury", ilvl=410.0)
    fake_cache = MagicMock()
    # 1st check → miss; after prewarm → hit.
    fake_cache.get_stale.side_effect = [(None, 0), (cached, 0)]

    client = MagicMock()
    client.get_character_guild_name = AsyncMock(return_value="Exordium")
    client.close = AsyncMock()

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=client),
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
    from backend.server.api.parses import ingest as parses_mod

    fake_cache = MagicMock()
    fake_cache.get_stale.return_value = (None, 0)  # always a miss

    client = MagicMock()
    client.get_character_guild_name = AsyncMock(return_value=None)
    client.close = AsyncMock()

    with (
        patch.object(parses_mod, "character_cache", fake_cache),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=client),
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
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 400
    assert "logger_server is required" in r.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_rejects_empty_logger_server(app):
    """Whitespace-only string → same outcome as null."""
    payload = _minimal_payload(logger_server="   ")
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_ingest_rejects_malformed_logger_server(app):
    """Bad shape (path traversal, control chars, etc.) → 400 malformed,
    not the allowlist-rejection 403 — distinguishes 'your plugin sent
    garbage' from 'your server isn't allowed'."""
    payload = _minimal_payload(logger_server="../etc/passwd")
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
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
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
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
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201
