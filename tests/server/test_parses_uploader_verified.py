"""Uploads bind to the token owner's approved claims (ingest + attendance).

Before this, ``logger_name`` was trusted as-is: any approved account could
file fights or attendance snapshots under a rival guild's roster. Now an
unclaimed logger still uploads (kept for its uploader) but is never
attributed to a guild and never ranks; attendance refuses outright.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.parses import IngestRequest
from backend.server.api.parses.ingest import _ingest_payload_sync, _uploader_claimed
from backend.server.api.rankings import _SQL as _RANKINGS_SQL
from backend.server.db import review_claim, submit_claim, upsert_user
from tests.fixtures.pg import pg_conn
from tests.server._parses_ingest_fixtures import _minimal_payload, _signed_post_kwargs

pytestmark = pytest.mark.usefixtures("users_schema", "uploader_claims_enforced")


async def _seed_claim(user_id: str, character: str, world: str, *, approve: bool = True) -> None:
    await upsert_user(discord_id=user_id, discord_name=user_id, discord_username=user_id, avatar=None)
    claim = await submit_claim(user_id, character, world=world)
    if approve:
        await review_claim(claim["id"], "approved", "admin-1")


def _user(user_id: str) -> AsyncMock:
    return AsyncMock(return_value={"id": user_id, "username": user_id, "auth_source": "token"})


# -- _uploader_claimed ---------------------------------------------------------


@pytest.mark.asyncio
async def test_uploader_claimed_requires_an_approved_claim_on_that_world():
    await _seed_claim("uv-owner", "Menludiir", "Varsoon")
    await _seed_claim("uv-pending", "Sihtric", "Varsoon", approve=False)

    assert await _uploader_claimed("uv-owner", "Menludiir", "Varsoon") is True
    assert await _uploader_claimed("uv-owner", "menludiir", "Varsoon") is True  # case-insensitive
    assert await _uploader_claimed("uv-owner", "Menludiir", "Wuoshi") is False  # other world
    assert await _uploader_claimed("uv-owner", "Sihtric", "Varsoon") is False  # someone else's name
    assert await _uploader_claimed("uv-pending", "Sihtric", "Varsoon") is False  # pending only
    assert await _uploader_claimed("uv-nobody", "Menludiir", "Varsoon") is False


# -- ingest route --------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaimed_logger_is_stored_unverified_without_guild(app):
    sync = MagicMock(return_value=("inserted", 42, 2, 1, 2))
    resolve = AsyncMock(return_value="Exordium")
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _user("uv-stranger")),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=resolve),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))

    assert r.status_code == 201, r.text
    assert r.json()["guild_name"] is None
    resolve.assert_not_awaited()  # never attributed to a guild, no backfill either
    assert sync.call_args.args[-1] is False  # uploader_verified


@pytest.mark.asyncio
async def test_claimed_logger_is_verified_and_attributed(app):
    await _seed_claim("uv-raider", "Menludiir", "Varsoon")
    sync = MagicMock(return_value=("inserted", 43, 2, 1, 2))
    resolve = AsyncMock(return_value="Exordium")
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _user("uv-raider")),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=resolve),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_signed_post_kwargs(_minimal_payload()))

    assert r.status_code == 201, r.text
    assert r.json()["guild_name"] == "Exordium"
    resolve.assert_awaited_once()
    assert sync.call_args.args[-1] is True


# -- storage + rankings dataset ------------------------------------------------


def test_unverified_and_hidden_rows_never_rank(parses_db_path):
    verified = IngestRequest(**_minimal_payload("VERIF001"))
    unverified = IngestRequest(**_minimal_payload("UNVER001"))
    hidden = IngestRequest(**_minimal_payload("HIDDEN01"))
    _, eid_v, *_ = _ingest_payload_sync(verified, "Menludiir", "Exordium", "plugin:1", {}, "Varsoon", True)
    _, eid_u, *_ = _ingest_payload_sync(unverified, "Menludiir", None, "plugin:2", {}, "Varsoon", False)
    _, eid_h, *_ = _ingest_payload_sync(hidden, "Menludiir", "Exordium", "plugin:1", {}, "Varsoon", True)

    with pg_conn(parses_db_path) as conn:
        conn.execute("UPDATE encounters SET success_level = 1 WHERE id = ANY(%s)", ([eid_v, eid_u, eid_h],))
        conn.execute("UPDATE encounters SET hidden_at = 1 WHERE id = %s", (eid_h,))
        flags = {
            r["id"]: r["uploader_verified"]
            for r in conn.execute("SELECT id, uploader_verified FROM encounters").fetchall()
        }
        # The raw UPDATEs above bypass the store hooks that keep each fight's
        # primaries current, so refresh them the way the hooks would.
        from backend.server.parses import fights

        for f in conn.execute("SELECT id FROM fights").fetchall():
            fights.refresh_fight(conn, f["id"])
        ranked = conn.execute(
            _RANKINGS_SQL["list_primary_winning_kills"].format(player_count_sql="0"),
            ("Varsoon",),
        ).fetchall()

    assert flags == {eid_v: 1, eid_u: 0, eid_h: 1}
    assert [r["id"] for r in ranked] == [eid_v]


# -- attendance route ----------------------------------------------------------


@pytest.mark.asyncio
async def test_attendance_refuses_an_unclaimed_logger(app):
    body = {
        "logger_name": "Menludiir",
        "logger_server": "Varsoon",
        "sent_at": 1_750_000_000,
        "raid_members": [],
        "online_guildies": [],
        "zones": [],
    }
    with (
        patch("backend.server.api.attendance.require_user_session_or_token", _user("uv-stranger")),
        patch("backend.server.api.attendance._ensure_subscriber", new=AsyncMock(return_value=None)),
        patch("backend.server.api.attendance._validate_payload_signature", new=AsyncMock(return_value=None)),
        patch("backend.server.api.attendance._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/attendance/ingest", json=body)
    assert r.status_code == 403, r.text
    assert "not a character claimed by your account" in r.json()["detail"]
