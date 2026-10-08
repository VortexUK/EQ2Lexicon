"""Guild settings: store, the leader helper, and the GET/PUT routes.

`officers_can_delete_parses` exists so a guild leader (Census rank 0) can stop
officers deleting the guild's parses. Enforcement in the delete routes and the
/parses permission pass is covered in test_parses_delete.py / test_parses_list.py.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.db.guild_settings import store as gs

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


@pytest.fixture(autouse=True)
def users_db(users_schema: str) -> str:
    """Isolated leased schema per test (conftest ``users_schema``), aliased
    so tests can keep naming it ``users_db``."""
    return users_schema


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_defaults_when_no_row(users_db):
    got = await gs.get_settings("Varsoon", "Exordium")
    assert got == {"officers_can_delete_parses": True, "officer_rank_ids": None, "updated_by": None, "updated_at": None}


async def test_upsert_round_trips_and_stamps_actor(users_db):
    stored = await gs.upsert_settings("Varsoon", "Exordium", officers_can_delete_parses=False, updated_by="lead-1")
    assert stored["officers_can_delete_parses"] is False
    assert stored["updated_by"] == "lead-1"
    assert isinstance(stored["updated_at"], int)
    again = await gs.upsert_settings("Varsoon", "Exordium", officers_can_delete_parses=True, updated_by="admin-1")
    assert again["officers_can_delete_parses"] is True
    assert again["updated_by"] == "admin-1"
    assert (await gs.get_settings("Varsoon", "Exordium"))["officers_can_delete_parses"] is True


async def test_flags_query_defaults_absent_guilds_and_scopes_by_world(users_db):
    assert await gs.officers_can_delete_parses("Varsoon", []) == {}
    await gs.upsert_settings("Varsoon", "Exordium", officers_can_delete_parses=False, updated_by="lead-1")
    await gs.upsert_settings("Wuoshi", "Remnant", officers_can_delete_parses=False, updated_by="lead-2")
    flags = await gs.officers_can_delete_parses("Varsoon", ["Exordium", "Remnant", "Nobody", ""])
    assert flags == {"Exordium": False, "Remnant": True, "Nobody": True}


async def test_officer_rank_ids_round_trip_and_bulk_defaults(users_db):
    from backend.server.constants import OFFICER_RANK_IDS

    assert await gs.officer_rank_ids_for("Varsoon", []) == {}
    stored = await gs.upsert_settings(
        "Varsoon", "Exordium", officers_can_delete_parses=True, updated_by="lead-1", officer_rank_ids=[0, 1, 2]
    )
    assert stored["officer_rank_ids"] == [0, 1, 2]
    ranks = await gs.officer_rank_ids_for("Varsoon", ["Exordium", "Nobody"])
    assert ranks == {"Exordium": frozenset({0, 1, 2}), "Nobody": OFFICER_RANK_IDS}
    # None stores the site default again.
    back = await gs.upsert_settings("Varsoon", "Exordium", officers_can_delete_parses=True, updated_by="lead-1")
    assert back["officer_rank_ids"] is None
    assert (await gs.officer_rank_ids_for("Varsoon", ["Exordium"]))["Exordium"] == OFFICER_RANK_IDS


async def test_officer_chars_honours_the_guilds_rank_setting(users_db):
    """A three-rank leadership (junior/senior/leader): rank 2 counts once
    the leader says so."""
    from backend.server.api import guild as guild_api

    guild_api.invalidate_officer_ranks("Exordium", "Varsoon")
    claims = AsyncMock(return_value={"approved": [{"character_name": "Junior"}]})
    with (
        patch("backend.server.api.guild.get_active_claims", claims),
        patch("backend.server.api.guild._roster_rank_map", AsyncMock(return_value={"junior": 2})),
    ):
        assert await guild_api._officer_chars("disc", "Exordium") == set()
        await gs.upsert_settings(
            "Varsoon", "Exordium", officers_can_delete_parses=True, updated_by="lead-1", officer_rank_ids=[0, 1, 2]
        )
        guild_api.invalidate_officer_ranks("Exordium", "Varsoon")  # what the PUT route does
        assert await guild_api._officer_chars("disc", "Exordium") == {"junior"}
    guild_api.invalidate_officer_ranks("Exordium", "Varsoon")


# ---------------------------------------------------------------------------
# _leader_chars — rank 0 only
# ---------------------------------------------------------------------------


async def test_leader_chars_requires_rank_zero():
    from backend.server.api import guild as guild_api

    claims = AsyncMock(return_value={"approved": [{"character_name": "Sihtric"}, {"character_name": "Alt"}]})
    with (
        patch("backend.server.api.guild.get_active_claims", claims),
        patch("backend.server.api.guild._roster_rank_map", AsyncMock(return_value={"sihtric": 0, "alt": 1})),
    ):
        assert await guild_api._leader_chars("disc", "Exordium") == {"sihtric"}
        assert await guild_api._officer_chars("disc", "Exordium") == {"sihtric", "alt"}
    with (
        patch("backend.server.api.guild.get_active_claims", claims),
        patch("backend.server.api.guild._roster_rank_map", AsyncMock(return_value={"sihtric": 1, "alt": 1})),
    ):
        assert await guild_api._leader_chars("disc", "Exordium") == set()


async def test_leader_chars_cached_is_none_on_cold_cache():
    from backend.server.api import guild as guild_api

    claims = AsyncMock(return_value={"approved": [{"character_name": "Sihtric"}]})
    with (
        patch("backend.server.api.guild.get_active_claims", claims),
        patch("backend.server.api.guild._roster_rank_map_cached", AsyncMock(return_value=None)),
    ):
        assert await guild_api._leader_chars_cached("disc", "Exordium") is None
    with (
        patch("backend.server.api.guild.get_active_claims", claims),
        patch("backend.server.api.guild._roster_rank_map_cached", AsyncMock(return_value={"sihtric": 0})),
    ):
        assert await guild_api._leader_chars_cached("disc", "Exordium") == {"sihtric"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    payload = base64.b64encode(json.dumps({"user": user}).encode()).decode()
    return {"session": itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()}


_LEADER = {"id": "lead-1", "username": "leader"}


async def _put(app, body, *, leader=True, admin=False, cookies=True):
    leader_ret = {"sihtric"} if leader else set()
    with (
        patch("backend.server.api.guild_settings._leader_chars", new=AsyncMock(return_value=leader_ret)),
        patch("backend.server.api.guild_settings.is_admin", return_value=admin),
        patch("backend.server.api.guild_settings.audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/guild/Exordium/settings",
                json=body,
                cookies=_cookies(_LEADER) if cookies else {},
            )
    return r, audit


async def test_get_is_public_and_returns_defaults(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/guild/Exordium/settings")
    assert r.status_code == 200
    assert r.json() == {
        "officers_can_delete_parses": True,
        "officer_rank_ids": [0, 1],
        "officer_rank_ids_custom": False,
        "updated_at": None,
        "updated_by_name": None,
    }


async def test_put_requires_auth(app):
    r, _ = await _put(app, {"officers_can_delete_parses": False}, cookies=False)
    assert r.status_code == 401


async def test_put_rejects_an_officer_who_is_not_the_leader(app):
    r, audit = await _put(app, {"officers_can_delete_parses": False}, leader=False)
    assert r.status_code == 403
    assert "leader" in r.json()["detail"].lower()
    audit.assert_not_called()
    assert (await gs.get_settings("Varsoon", "Exordium"))["officers_can_delete_parses"] is True


async def test_put_by_leader_persists_and_audits(app):
    r, audit = await _put(app, {"officers_can_delete_parses": False})
    assert r.status_code == 200
    body = r.json()
    assert body["officers_can_delete_parses"] is False
    assert isinstance(body["updated_at"], int)
    audit.assert_called_once()
    assert audit.call_args.args[0] == "guild_settings_updated"
    assert audit.call_args.kwargs["guild"] == "Exordium"
    assert audit.call_args.kwargs["officers_can_delete_parses"] is False
    stored = await gs.get_settings("Varsoon", "Exordium")
    assert stored["officers_can_delete_parses"] is False and stored["updated_by"] == "lead-1"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        again = await client.get("/api/guild/Exordium/settings")
    assert again.json()["officers_can_delete_parses"] is False


async def test_put_by_admin_who_is_not_the_leader(app):
    r, _ = await _put(app, {"officers_can_delete_parses": False}, leader=False, admin=True)
    assert r.status_code == 200
    assert r.json()["officers_can_delete_parses"] is False


async def test_put_rejects_a_bad_guild_name(app):
    r, _ = await _put(app, {"officers_can_delete_parses": False})
    assert r.status_code == 200  # sanity: the good name works
    with (
        patch("backend.server.api.guild_settings._leader_chars", new=AsyncMock(return_value={"sihtric"})),
        patch("backend.server.api.guild_settings.is_admin", return_value=False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.put(
                "/api/guild/" + "x" * 200 + "/settings",
                json={"officers_can_delete_parses": False},
                cookies=_cookies(_LEADER),
            )
    assert bad.status_code == 400


async def test_put_officer_rank_ids_always_includes_the_leader_and_validates(app):
    from backend.server.api import guild as guild_api

    r, audit = await _put(app, {"officers_can_delete_parses": True, "officer_rank_ids": [2, 1, 2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["officer_rank_ids"] == [0, 1, 2]  # rank 0 added, deduped, sorted
    assert body["officer_rank_ids_custom"] is True
    assert audit.call_args.kwargs["officer_rank_ids"] == "0,1,2"
    assert (await guild_api.officer_rank_ids("Exordium", "Varsoon")) == frozenset({0, 1, 2})

    bad, _ = await _put(app, {"officers_can_delete_parses": True, "officer_rank_ids": [0, 99]})
    assert bad.status_code == 400

    reset, _ = await _put(app, {"officers_can_delete_parses": True, "officer_rank_ids": None})
    assert reset.json()["officer_rank_ids"] == [0, 1]
    assert reset.json()["officer_rank_ids_custom"] is False
    guild_api.invalidate_officer_ranks("Exordium", "Varsoon")
