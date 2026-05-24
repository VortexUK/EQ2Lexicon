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
        "deaths": 0,
        "kills": 4,
        "crit_hits": 123,
        "crit_dam_perc": 93.0,
        "damage_taken": 27557,
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
        "deaths": 1,
        "kills": 0,
        "crit_hits": 0,
        "crit_dam_perc": 0.0,
        "damage_taken": 145877,
    },
]

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
    fake_list_sync = MagicMock(return_value=([dict(_FAKE_ENCOUNTER, combatant_count=2)], 1))

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
    assert enc["encdps"] == 10928.65


@pytest.mark.asyncio
async def test_list_parses_clamps_limit(app):
    captured = {}

    def fake_list_sync(limit, zone):
        captured["limit"] = limit
        captured["zone"] = zone
        return ([], 0)

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Asking for 999 should be clamped to the max (100).
            await client.get("/api/parses?limit=999")
            assert captured["limit"] == 100
            # Asking for 0 should be floored to the min (1).
            await client.get("/api/parses?limit=0")
            assert captured["limit"] == 1


@pytest.mark.asyncio
async def test_list_parses_passes_zone_filter(app):
    captured = {}

    def fake_list_sync(limit, zone):
        captured["zone"] = zone
        return ([], 0)

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses?zone=Great+Divide")
            assert captured["zone"] == "Great Divide"


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_parse_returns_detail(app):
    fake_enc = dict(_FAKE_ENCOUNTER)
    fake_enc["combatants"] = [
        dict(c, ally=bool(c["ally"]), top_attacks=_FAKE_TOP_ATTACKS[c["id"]]) for c in _FAKE_COMBATANTS
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
    assert len(menludiir["top_attacks"]) == 1
    assert menludiir["top_attacks"][0]["attack_name"] == "Smite"

    mob = next(c for c in data["combatants"] if c["name"] == "a krait patriarch")
    assert mob["ally"] is False
    assert mob["damage_taken"] == 145877


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
