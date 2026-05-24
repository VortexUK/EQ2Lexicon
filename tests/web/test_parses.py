"""Tests for the parses route — auth gate and DB-driven responses."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Fake DB data — shape matches what parses_db.recent_encounters /
# get_combatants_for_encounter / get_top_attacks_for_combatant return
# (dicts from sqlite3.Row).
# ---------------------------------------------------------------------------

_FAKE_ENCOUNTER = {
    "id": 1,
    "act_encid": "18cf3eb9",
    "title": "a krait patriarch",
    "zone": "Great Divide",
    "started_at": 1716561116,
    "ended_at": 1716561162,
    "duration_s": 46,
    "total_damage": 502718,
    "encdps": 10928.65,
    "kills": 4,
    "deaths": 0,
    "source_dsn": "eq2act",
    "uploaded_by": "Menludiir",
    "guild_name": "Exordium",
    "ingested_at": 1716561200,
}

_FAKE_COMBATANTS = [
    {
        "id": 10,
        "encounter_id": 1,
        "name": "Menludiir",
        "ally": 1,
        "duration_s": 47,
        "damage": 502718,
        "damage_perc": 100.0,
        "dps": 10696.13,
        "encdps": 10928.65,
        "healed": 11637,
        "enchps": 252.98,
        "heals": 40,
        "crit_heals": 1,
        "cure_dispels": 0,
        "power_drain": 0,
        "power_replenish": 0,
        "heals_taken": 11637,
        "damage_taken": 27557,
        "threat_delta": 20000,
        "deaths": 0,
        "kills": 4,
        "crit_hits": 123,
        "crit_dam_perc": 93.0,
    },
    {
        "id": 11,
        "encounter_id": 1,
        "name": "a krait patriarch",
        "ally": 0,
        "duration_s": 15,
        "damage": 5716,
        "damage_perc": 0.0,
        "dps": 381.07,
        "encdps": 124.26,
        "healed": 0,
        "enchps": 0.0,
        "heals": 0,
        "crit_heals": 0,
        "cure_dispels": 0,
        "power_drain": 0,
        "power_replenish": 0,
        "heals_taken": 0,
        "damage_taken": 145877,
        "threat_delta": 0,
        "deaths": 1,
        "kills": 0,
        "crit_hits": 0,
        "crit_dam_perc": 0.0,
    },
]

_FAKE_DAMAGE_TYPES = {
    10: [
        {
            "damage_type": "divine",
            "damage": 400000,
            "dps": 8500.0,
            "hits": 100,
            "swings": 100,
            "max_hit": 8000,
            "crit_perc": 90.0,
        },
        {
            "damage_type": "melee",
            "damage": 102718,
            "dps": 2185.0,
            "hits": 32,
            "swings": 32,
            "max_hit": 4500,
            "crit_perc": 100.0,
        },
    ],
    11: [
        {
            "damage_type": "physical",
            "damage": 5716,
            "dps": 381.07,
            "hits": 11,
            "swings": 12,
            "max_hit": 1297,
            "crit_perc": 0.0,
        },
    ],
}

_FAKE_TOP_ATTACKS = {
    10: [
        {
            "id": 100,
            "combatant_id": 10,
            "attack_name": "Smite",
            "damage": 400000,
            "hits": 100,
            "swings": 100,
            "crit_perc": 90.0,
            "max_hit": 8000,
        },
    ],
    11: [
        {
            "id": 200,
            "combatant_id": 11,
            "attack_name": "melee",
            "damage": 5716,
            "hits": 11,
            "swings": 12,
            "crit_perc": 0.0,
            "max_hit": 1297,
        },
    ],
}

_FAKE_TOP_HEALS = {
    10: [
        {
            "attack_name": "Reverence",
            "damage": 7818,  # amount healed
            "hits": 12,
            "swings": 12,
            "crit_perc": 0.0,
            "max_hit": 1297,
            "resist": "Hitpoints",
        },
        {
            "attack_name": "Stonewill",
            "damage": 3819,
            "hits": 12,
            "swings": 12,
            "crit_perc": 0.0,
            "max_hit": 1297,
            "resist": "Absorption",  # ward
        },
    ],
    11: [],
}

_FAKE_TOP_CURES = {
    10: [
        {"attack_name": "Cure", "damage": 4, "hits": 4, "max_hit": 1, "resist": "relieves"},
        {"attack_name": "Devoted Resolve", "damage": 2, "hits": 2, "max_hit": 1, "resist": "relieves"},
    ],
    11: [],
}

_FAKE_TOP_THREATS = {
    10: [
        {"attack_name": "Undeniable Malice", "damage": 27240, "hits": 10, "max_hit": 5000, "resist": "Increase"},
    ],
    11: [],
}


def _fake_user(request=None) -> dict:  # matches _require_user(request) signature
    return {"id": "123456789", "username": "testuser"}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_parses_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/parses")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_parse_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/parses/1")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_parses_returns_results(app):
    fake_list_sync = MagicMock(return_value=([dict(_FAKE_ENCOUNTER, combatant_count=2, player_count=1)], 1))

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert len(data["results"]) == 1
    enc = data["results"][0]
    assert enc["act_encid"] == "18cf3eb9"
    assert enc["title"] == "a krait patriarch"
    assert enc["zone"] == "Great Divide"
    assert enc["combatant_count"] == 2
    assert enc["player_count"] == 1
    assert enc["uploaded_by"] == "Menludiir"
    assert enc["guild_name"] == "Exordium"
    assert enc["encdps"] == 10928.65


@pytest.mark.asyncio
async def test_list_parses_clamps_limit(app):
    captured = {}

    def fake_list_sync(limit, zone, size):
        captured["limit"] = limit
        captured["zone"] = zone
        captured["size"] = size
        return ([], 0)

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Asking for 9999 should be clamped to the max (500).
            await client.get("/api/parses?limit=9999")
            assert captured["limit"] == 500
            # Asking for 0 should be floored to the min (1).
            await client.get("/api/parses?limit=0")
            assert captured["limit"] == 1


@pytest.mark.asyncio
async def test_list_parses_passes_zone_filter(app):
    captured = {}

    def fake_list_sync(limit, zone, size):
        captured["zone"] = zone
        return ([], 0)

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses?zone=Great+Divide")
            assert captured["zone"] == "Great Divide"


@pytest.mark.asyncio
async def test_list_parses_passes_size_filter(app):
    captured = {}

    def fake_list_sync(limit, zone, size):
        captured["size"] = size
        return ([], 0)

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses?size=raid24")
            assert captured["size"] == "raid24"

            # Unknown bucket values are silently dropped (no filter).
            await client.get("/api/parses?size=nonsense")
            assert captured["size"] is None


@pytest.mark.asyncio
async def test_size_buckets_defined():
    """Sanity-check the bucket ranges so the frontend can rely on them."""
    from web.routes.parses import SIZE_BUCKETS

    assert SIZE_BUCKETS["individual"] == (1, 1)
    assert SIZE_BUCKETS["group"] == (2, 6)
    assert SIZE_BUCKETS["raid12"] == (7, 12)
    assert SIZE_BUCKETS["raid24"] == (13, 24)


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_parse_returns_detail(app):
    fake_enc = dict(_FAKE_ENCOUNTER)
    fake_enc["combatants"] = [
        dict(
            c,
            ally=bool(c["ally"]),
            top_attacks=_FAKE_TOP_ATTACKS[c["id"]],
            top_heals=_FAKE_TOP_HEALS[c["id"]],
            top_cures=_FAKE_TOP_CURES[c["id"]],
            top_threats=_FAKE_TOP_THREATS[c["id"]],
            damage_types=_FAKE_DAMAGE_TYPES[c["id"]],
        )
        for c in _FAKE_COMBATANTS
    ]

    fake_detail_sync = MagicMock(return_value=fake_enc)

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._encounter_detail_sync", fake_detail_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses/1")

    assert r.status_code == 200
    data = r.json()
    assert data["id"] == 1
    assert data["title"] == "a krait patriarch"
    assert len(data["combatants"]) == 2

    menludiir = next(c for c in data["combatants"] if c["name"] == "Menludiir")
    assert menludiir["ally"] is True
    assert menludiir["damage"] == 502718
    assert menludiir["crit_dam_perc"] == 93.0
    assert menludiir["heals"] == 40
    assert menludiir["threat_delta"] == 20000
    assert len(menludiir["top_attacks"]) == 1
    assert menludiir["top_attacks"][0]["attack_name"] == "Smite"
    assert len(menludiir["damage_types"]) == 2
    assert menludiir["damage_types"][0]["damage_type"] == "divine"
    assert menludiir["damage_types"][0]["damage"] == 400000

    # Heal abilities surfaced separately, with heal_type carried through.
    assert len(menludiir["top_heals"]) == 2
    rev = next(h for h in menludiir["top_heals"] if h["heal_name"] == "Reverence")
    assert rev["healed"] == 7818
    assert rev["heal_type"] == "Hitpoints"
    sw = next(h for h in menludiir["top_heals"] if h["heal_name"] == "Stonewill")
    assert sw["heal_type"] == "Absorption"

    # Cures (swing_type=20)
    assert len(menludiir["top_cures"]) == 2
    cure = next(c for c in menludiir["top_cures"] if c["cure_name"] == "Cure")
    assert cure["effects_removed"] == 4
    assert cure["times_cast"] == 4

    # Threat procs (swing_type=100, type != 'All')
    assert len(menludiir["top_threats"]) == 1
    um = menludiir["top_threats"][0]
    assert um["ability_name"] == "Undeniable Malice"
    assert um["value"] == 27240
    assert um["procs"] == 10
    assert um["kind"] == "Increase"

    mob = next(c for c in data["combatants"] if c["name"] == "a krait patriarch")
    assert mob["ally"] is False
    assert mob["damage_taken"] == 145877
    assert len(mob["damage_types"]) == 1
    assert mob["top_heals"] == []
    assert mob["top_cures"] == []
    assert mob["top_threats"] == []


@pytest.mark.asyncio
async def test_get_parse_missing_returns_404(app):
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._encounter_detail_sync", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_parse_clamps_top_attacks(app):
    captured = {}

    def fake_detail_sync(encounter_id, top_attacks_per_combatant):
        captured["top"] = top_attacks_per_combatant
        return None  # 404 — we just want to inspect the captured arg

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._encounter_detail_sync", side_effect=fake_detail_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses/1?top_attacks=999")
            assert captured["top"] == 50
            await client.get("/api/parses/1?top_attacks=0")
            assert captured["top"] == 1


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


class TestUploaderDiscordId:
    def test_plugin_prefix_returns_id(self):
        from web.routes.parses import _uploader_discord_id

        assert _uploader_discord_id("plugin:12345") == "12345"

    def test_eq2act_returns_none(self):
        from web.routes.parses import _uploader_discord_id

        assert _uploader_discord_id("eq2act") is None

    def test_empty_returns_none(self):
        from web.routes.parses import _uploader_discord_id

        assert _uploader_discord_id("") is None
        assert _uploader_discord_id(None) is None

    def test_plugin_with_no_id_returns_none(self):
        from web.routes.parses import _uploader_discord_id

        assert _uploader_discord_id("plugin:") is None


# ---------------------------------------------------------------------------
# DELETE /api/parses/{id}
# ---------------------------------------------------------------------------


def _fake_conn_for_fetch(row: dict | None) -> MagicMock:
    """Build a MagicMock connection whose `execute().fetchone()` returns the
    given row. Used to fake the per-id encounter lookup inside delete_parse."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn.execute.return_value = cur
    return conn


@pytest.mark.asyncio
async def test_delete_parse_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/parses/1")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_parse_404_when_missing(app):
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_parse_admin_can_delete(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:99999"}
    delete_mock = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_parse_uploader_can_delete(app):
    # _fake_user returns id="123456789"; source_dsn matches → uploader-allowed
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:123456789"}
    delete_mock = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_parse_officer_can_delete(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER_USER"}
    delete_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    delete_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_parse_random_user_403(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER_USER"}

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/parses (bulk by filter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_bulk_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_bulk_requires_guild(app):
    with patch("web.routes.parses._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses")
    assert r.status_code == 422  # FastAPI validation: missing required query param


@pytest.mark.asyncio
async def test_delete_bulk_admin_passes_filters(app):
    captured = {}

    def fake_delete(conn, *, guild_name, zone=None, date=None, uploaded_by=None):
        captured.update(guild_name=guild_name, zone=zone, date=date, uploaded_by=uploaded_by)
        return 7

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.delete_encounters_by_filter", fake_delete),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&zone=Great+Divide&date=2026-05-24&uploader=Menludiir")
    assert r.status_code == 200
    assert r.json() == {"deleted": 7}
    assert captured == {
        "guild_name": "Exordium",
        "zone": "Great Divide",
        "date": "2026-05-24",
        "uploaded_by": "Menludiir",
    }


@pytest.mark.asyncio
async def test_delete_bulk_officer_allowed(app):
    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.delete_encounters_by_filter", MagicMock(return_value=3)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 200
    assert r.json() == {"deleted": 3}


@pytest.mark.asyncio
async def test_delete_bulk_random_user_403(app):
    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 403
