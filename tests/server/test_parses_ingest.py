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
    # A 'revived' status must also schedule the background snapshot
    # resolution, same as 'inserted'. Only this mock produces 'revived':
    # the real _ingest_payload_sync never returns it (a re-upload of a
    # soft-deleted parse is 'skipped', see the test below, and the comment
    # in ingest_parse where the route branches on the status).
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
async def test_reupload_of_soft_deleted_parse_does_not_revive_it(parses_db_path):
    """Security: re-uploading a moderator-hidden parse must NOT un-hide it.

    Soft-delete is the moderation action; auto-reviving on re-upload let a
    cheater undo an officer's hide just by re-sending the payload. Re-upload
    now returns 'skipped' (with no encounter_id echo — enumeration oracle
    closed) and leaves hidden_at set. Restoring a wrongly-hidden parse is a
    separate authenticated admin action."""
    from backend.server.api.parses import IngestRequest
    from backend.server.api.parses.ingest import _ingest_payload_sync
    from backend.server.parses import db as pdb

    payload = IngestRequest(**_minimal_payload())

    # First ingest → inserted (the fixture re-pointed the shared store at a
    # leased scratch schema).
    status, eid, *_ = _ingest_payload_sync(payload, "Menludiir", "Exordium", "plugin:123", {})
    assert status == "inserted" and eid is not None

    # Soft-delete it (as the delete route does for a boss kill).
    conn = pdb.store.init_db()
    try:
        pdb.store.soft_delete_encounter(conn, eid, hidden_at=1700000000)
        assert pdb.store.find_encounter_by_act_encid(conn, payload.encounter.encid)["hidden_at"] is not None
    finally:
        conn.close()

    # Re-upload the same encounter → skipped, NOT revived; id not echoed.
    status2, eid2, *_ = _ingest_payload_sync(payload, "Menludiir", "Exordium", "plugin:123", {})
    assert status2 == "skipped"
    assert eid2 is None
    # Still hidden — moderation preserved.
    conn = pdb.store.init_db()
    try:
        assert pdb.store.find_encounter_by_act_encid(conn, payload.encounter.encid)["hidden_at"] is not None
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
# `backend.server.auth_deps.require_user_session_or_token` is tested below by mocking
# `users_db.lookup_api_token` to exercise the wiring without needing to
# patch backend.server.db.DB_PATH (which is captured at import time).


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
# Server-side validation is STRICT:
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
# Error text by client: EQ2Parser shows `detail` verbatim in its status line,
# and its users do not run the ACT plugin. The client is recognised only by a
# User-Agent prefix (EQ2Parser/<version>); every other client keeps the
# original wording byte-for-byte.
# ---------------------------------------------------------------------------

_PLUGIN_MISSING_SIGNATURE_DETAIL = (
    "X-Lexicon-Signature is required for plugin uploads. "
    "Update the EQ2 Lexicon ACT plugin to v0.1.8 or later: "
    "https://github.com/VortexUK/EQ2LexiconACTPlugin/releases/latest"
)
_PLUGIN_MISSING_SERVER_DETAIL = (
    "logger_server is required. Please update the EQ2 Lexicon ACT plugin to v0.1.14 or later."
)

# Not EQ2Parser: the plugin, a browser-ish string that merely contains the
# name (prefix match, not substring), a lookalike product, and no header at
# all (httpx then sends its own python-httpx/<version>).
_NOT_PARSER_USER_AGENTS = [
    "EQ2LexiconACTPlugin/0.1.16",
    "Mozilla/5.0 EQ2Parser/0.5.7",
    "EQ2ParserPro/1.0",
    None,
]


def _with_user_agent(kwargs: dict, user_agent: str | None) -> dict:
    if user_agent is not None:
        kwargs["headers"]["User-Agent"] = user_agent
    return kwargs


async def _post_unsigned(app, user_agent: str | None):
    headers = {"Authorization": "Bearer eq2c_unsigned"}
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/parses/ingest", json=_minimal_payload(), headers=headers)


async def _post_without_server(app, user_agent: str | None):
    kwargs = _with_user_agent(_signed_post_kwargs(_minimal_payload(logger_server=None)), user_agent)
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/parses/ingest", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("user_agent", ["EQ2Parser/0.5.7", "eq2parser/0.6.0"])
async def test_missing_signature_text_for_eq2parser(app, user_agent):
    """The parser is told the upload was unsigned and to update EQ2Parser,
    never to update an ACT plugin it does not run. Status stays 401."""
    r = await _post_unsigned(app, user_agent)
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert "not signed" in detail
    assert "Update EQ2Parser" in detail
    assert "ACT plugin" not in detail
    assert "v0.1.8" not in detail
    assert "EQ2LexiconACTPlugin" not in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("user_agent", _NOT_PARSER_USER_AGENTS)
async def test_missing_signature_text_unchanged_for_other_clients(app, user_agent):
    r = await _post_unsigned(app, user_agent)
    assert r.status_code == 401
    assert r.json()["detail"] == _PLUGIN_MISSING_SIGNATURE_DETAIL


@pytest.mark.asyncio
@pytest.mark.parametrize("user_agent", ["EQ2Parser/0.5.7", "eq2parser/0.6.0"])
async def test_missing_logger_server_text_for_eq2parser(app, user_agent):
    """In the parser a missing logger_server means the log file sits directly
    in the game's logs folder, not that some plugin is out of date."""
    r = await _post_without_server(app, user_agent)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "logs/<server>/" in detail
    assert "not directly in logs" in detail
    assert "ACT plugin" not in detail
    assert "v0.1.14" not in detail


@pytest.mark.asyncio
@pytest.mark.parametrize("user_agent", _NOT_PARSER_USER_AGENTS)
async def test_missing_logger_server_text_unchanged_for_other_clients(app, user_agent):
    r = await _post_without_server(app, user_agent)
    assert r.status_code == 400
    assert r.json()["detail"] == _PLUGIN_MISSING_SERVER_DETAIL


@pytest.mark.asyncio
async def test_user_agent_is_never_echoed_in_the_error_or_the_logs(app, caplog):
    """The User-Agent only selects the wording; its value reaches neither the
    response nor a log line."""
    marker = "zz-echo-probe-9.9.9"
    with caplog.at_level("DEBUG"):
        unsigned = await _post_unsigned(app, f"EQ2Parser/{marker}")
        no_server = await _post_without_server(app, f"EQ2Parser/{marker}")
    assert unsigned.status_code == 401 and no_server.status_code == 400
    assert marker not in unsigned.text
    assert marker not in no_server.text
    assert marker not in caplog.text


# ---------------------------------------------------------------------------
# Defensive validation
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
        # The background snapshot resolver would otherwise run for real and,
        # on a cold character cache, call the live Census API.
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
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

    prewarm.assert_awaited_once_with("Exordium", "Varsoon")
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


# ---------------------------------------------------------------------------
# X-Lexicon-Reject: marks a refusal that is about THIS upload, not about the
# caller. EQ2Parser pauses its whole upload queue on a 401/403 unless the
# header is present, so it must be on the allowlist 403 (an alt logging on a
# server the site does not serve) and on nothing that is about the account.
# ---------------------------------------------------------------------------


def test_reject_header_name_is_the_wire_contract():
    """EQ2Parser's LexiconUploadClient hardcodes this name."""
    from backend.server.api.parses.ingest import REJECT_HEADER

    assert REJECT_HEADER == "X-Lexicon-Reject"


@pytest.mark.asyncio
async def test_disallowed_server_403_is_tagged_server_not_allowed(app):
    """Status and detail are exactly what they were; the header is added."""
    from backend.server.config import ALLOWED_SERVERS

    payload = _minimal_payload(logger_server="Halls of Fate")
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 403
    assert r.json()["detail"] == (
        f"Server 'Halls of Fate' is not on the allowed list. Allowed: {', '.join(sorted(ALLOWED_SERVERS))}."
    )
    assert r.headers.get("X-Lexicon-Reject") == "server-not-allowed"


@pytest.mark.asyncio
async def test_unapproved_account_403_is_not_tagged(app):
    """The auth layer's own 403 is about the account, so the parser must keep
    treating it as an account-level refusal: no header."""
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
    with patch("backend.server.auth_deps.users_db.lookup_api_token", new=AsyncMock(return_value=pending_row)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # A disallowed server in the body too: auth runs first, and the
            # account-level refusal must win without the upload-level tag.
            r = await client.post(
                "/api/parses/ingest",
                **_signed_post_kwargs(_minimal_payload(logger_server="Halls of Fate"), token="eq2c_pendingtoken"),
            )
    assert r.status_code == 403
    assert r.json()["detail"] == "Account not approved"
    assert "X-Lexicon-Reject" not in r.headers


@pytest.mark.asyncio
async def test_successful_upload_has_no_reject_header(app):
    sync_result = ("inserted", 101, 2, 1, 2)
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))
    assert r.status_code == 201
    assert "X-Lexicon-Reject" not in r.headers


@pytest.mark.asyncio
async def test_other_ingest_refusals_have_no_reject_header(app):
    """Only the allowlist 403 is tagged. A 400 (missing or malformed
    logger_server) and a 401 (no token, unsigned, bad signature) are not."""
    signed_bad_sig = _signed_post_kwargs(_minimal_payload())
    signed_bad_sig["headers"]["X-Lexicon-Signature"] = "0" * 64
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post(
                "/api/parses/ingest", **_signed_post_kwargs(_minimal_payload(logger_server=None))
            )
            malformed = await client.post(
                "/api/parses/ingest", **_signed_post_kwargs(_minimal_payload(logger_server="../etc/passwd"))
            )
            unsigned = await client.post(
                "/api/parses/ingest", json=_minimal_payload(), headers={"Authorization": "Bearer eq2c_unsigned"}
            )
            bad_sig = await client.post("/api/parses/ingest", **signed_bad_sig)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        no_token = await client.post("/api/parses/ingest", json=_minimal_payload())
    assert [r.status_code for r in (missing, malformed, unsigned, bad_sig, no_token)] == [400, 400, 401, 401, 401]
    for r in (missing, malformed, unsigned, bad_sig, no_token):
        assert "X-Lexicon-Reject" not in r.headers


# ---------------------------------------------------------------------------
# EQ2Parser (≤0.3.4) field-gap fallbacks: it ships encdps + crithits for
# attack/damage-type rows but not ACT's `dps` and pre-formatted `critperc` —
# without the fallbacks those rows stored dps=0 / crit_perc=0 and the parse
# page's per-attack DPS + CRIT% columns rendered 0 for parser uploads.
# ---------------------------------------------------------------------------


def test_attack_type_dps_falls_back_to_encdps():
    from backend.server.api.parses.ingest import _attack_types_from_payload
    from backend.server.api.parses.models import IngestAttackType

    row = IngestAttackType(
        attacker="Gigachad",
        type="Bear Claws",
        swingtype=2,
        damage=409217,
        encdps=1206.0,  # what EQ2Parser ships
        hits=207,
        swings=207,
        crithits=140,
        # no dps, no critperc (the parser's field gap)
    )
    (out,) = _attack_types_from_payload([row], "ENC1")
    assert out.dps == 1206.0  # falls back to encdps instead of 0
    assert out.crit_perc == round(100.0 * 140 / 207, 1)  # computed from counts


def test_attack_type_act_fields_win_over_fallbacks():
    """ACT-plugin rows carry real dps/critperc — the fallbacks must not
    override them."""
    from backend.server.api.parses.ingest import _attack_types_from_payload
    from backend.server.api.parses.models import IngestAttackType

    row = IngestAttackType(
        attacker="Menludiir",
        type="Smite",
        swingtype=2,
        damage=400000,
        dps=1500.0,
        encdps=1100.0,
        hits=100,
        crithits=90,
        critperc="93%",
    )
    (out,) = _attack_types_from_payload([row], "ENC1")
    assert out.dps == 1500.0
    assert out.crit_perc == 93.0


def test_damage_type_rows_get_the_same_fallbacks():
    from backend.server.api.parses.ingest import _damage_types_from_payload
    from backend.server.api.parses.models import IngestDamageType

    row = IngestDamageType(
        combatant="Gigachad",
        type="crushing",
        damage=100000,
        encdps=350.0,
        hits=50,
        crithits=10,
    )
    (out,) = _damage_types_from_payload([row], "ENC1")
    assert out.dps == 350.0
    assert out.crit_perc == 20.0


# ---------------------------------------------------------------------------
# One length limit for a server name and for a character name
#
# The model's max_length is the validator's own bound (server-name pattern 31,
# character-name shape 15). The model used to allow 64 for both, so a 32-64
# character server name passed it and was then refused by the route as
# "malformed" (400). Too long is now the model's 422, as it always was past
# 64; a value inside the bound with the wrong shape is still the route's 400.
# ---------------------------------------------------------------------------

_SERVER_31 = "V" * 31  # the longest name the server-name pattern accepts
_NAME_15 = "A" * 15  # the longest character name


def _too_long_fields(response) -> list[tuple[str, str]]:
    """(field, error type) for each entry of a 422's detail list."""
    return [(str(e["loc"][-1]), e["type"]) for e in response.json()["detail"]]


@pytest.mark.asyncio
async def test_ingest_logger_server_of_31_passes_the_model_and_the_shape(app):
    """31 characters is inside both bounds, so the request gets as far as the
    allowlist: a 403 about the server, not a length or shape refusal."""
    payload = _minimal_payload(logger_server=_SERVER_31)
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 403, r.text
    assert _SERVER_31 in r.json()["detail"]
    assert r.headers.get("X-Lexicon-Reject") == "server-not-allowed"


@pytest.mark.asyncio
async def test_ingest_logger_server_of_32_is_too_long(app):
    payload = _minimal_payload(logger_server="V" * 32)
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 422, r.text
    assert _too_long_fields(r) == [("logger_server", "string_too_long")]
    # Only the upload-scoped 403s carry the reject header; a 422 never does.
    assert "X-Lexicon-Reject" not in r.headers


@pytest.mark.asyncio
async def test_ingest_logger_name_of_15_is_accepted(app):
    payload = _minimal_payload()
    payload["logger_name"] = _NAME_15
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value=None)),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch(
            "backend.server.api.parses.ingest._ingest_payload_sync",
            new=MagicMock(return_value=("inserted", 1, 1, 0, 0)),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_ingest_logger_name_of_16_is_too_long(app):
    payload = _minimal_payload()
    payload["logger_name"] = "A" * 16
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert r.status_code == 422, r.text
    assert _too_long_fields(r) == [("logger_name", "string_too_long")]


@pytest.mark.asyncio
async def test_ingest_wrong_shape_inside_the_bound_is_still_a_400(app):
    """The model bounds length only. A short value of the wrong shape keeps
    the route's own 400 and its text, for the server and for the name."""
    bad_server = _minimal_payload(logger_server="../etc/passwd")
    bad_name = _minimal_payload()
    bad_name["logger_name"] = "alice bob"
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_server = await client.post("/api/parses/ingest", **_signed_post_kwargs(bad_server))
            r_name = await client.post("/api/parses/ingest", **_signed_post_kwargs(bad_name))
    assert r_server.status_code == 400
    assert r_server.json()["detail"] == "logger_server '../etc/passwd' is malformed."
    assert r_name.status_code == 400
    assert r_name.json()["detail"] == "logger_name must be 1-15 letters (the EQ2 character-name shape)."
