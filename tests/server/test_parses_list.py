"""Tests for GET /api/parses and GET /api/parses/{id} — list + detail endpoints.

Extracted from test_parses.py:197-534 per TEST-004 / Phase 2b.3.
Also includes TestUploaderDiscordId (test_parses.py:542-562) which tests a
helper from web.routes.parses.list, and the SIZE_BUCKETS sanity check.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_require_user, make_fake_user
from tests.server._parses_fixtures import (
    _FAKE_COMBATANTS,
    _FAKE_DAMAGE_TYPES,
    _FAKE_ENCOUNTER,
    _FAKE_TOP_ATTACKS,
    _FAKE_TOP_CURES,
    _FAKE_TOP_HEALS,
    _FAKE_TOP_THREATS,
)

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


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
    # Solo fight, single uploader — no mirror grouping needed.
    fake_list_sync = MagicMock(return_value=[dict(_FAKE_ENCOUNTER, combatant_count=2, player_count=1)])

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")

    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1  # one fight
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
    # Even a solo fight has a single-element uploads list (it's the canonical
    # itself) so the frontend can render the per-uploader expansion uniformly.
    assert len(enc["uploads"]) == 1
    assert enc["uploads"][0]["id"] == enc["id"]
    assert enc["uploads"][0]["uploaded_by"] == "Menludiir"


@pytest.mark.asyncio
async def test_list_parses_groups_mirror_uploads(app):
    """Two raiders uploading the same fight (same guild + title + ±60 s)
    collapse into one row with both in the `uploads` list."""
    base_started = 1716561116
    raider_a = dict(_FAKE_ENCOUNTER, id=1, uploaded_by="Menludiir", duration_s=46, started_at=base_started)
    raider_b = dict(
        _FAKE_ENCOUNTER,
        id=2,
        uploaded_by="Sihtric",
        started_at=base_started + 5,
        duration_s=50,
        combatant_count=2,
        player_count=1,
    )
    raider_a["combatant_count"] = 2
    raider_a["player_count"] = 1
    fake_list_sync = MagicMock(return_value=[raider_b, raider_a])  # arbitrary order

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1, "two mirror uploads should collapse to one fight"
    assert len(data["results"]) == 1
    fight = data["results"][0]
    # Canonical = longer-duration upload (Sihtric's at 50s).
    assert fight["id"] == 2
    assert fight["uploaded_by"] == "Sihtric"
    assert {u["uploaded_by"] for u in fight["uploads"]} == {"Menludiir", "Sihtric"}


@pytest.mark.asyncio
async def test_list_parses_does_not_group_same_uploader(app):
    """One uploader's two uploads of the same title within the mirror window
    are distinct fights (e.g. the same boss pulled twice in quick succession)
    — only different uploaders mirror a single fight."""
    base_started = 1716561116
    first = dict(_FAKE_ENCOUNTER, id=1, uploaded_by="Menludiir", started_at=base_started)
    second = dict(_FAKE_ENCOUNTER, id=2, uploaded_by="Menludiir", started_at=base_started + 5)
    fake_list_sync = MagicMock(return_value=[first, second])
    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    data = r.json()
    assert data["total"] == 2, "same uploader's two uploads must not collapse"
    assert {f["id"] for f in data["results"]} == {1, 2}


@pytest.mark.asyncio
async def test_list_parses_does_not_group_different_titles(app):
    """Same guild + close start times but different titles → different
    fights (distinct mirror groups)."""
    a = dict(_FAKE_ENCOUNTER, id=1, title="Boss A", started_at=1716561116)
    b = dict(_FAKE_ENCOUNTER, id=2, title="Boss B", started_at=1716561120)
    fake_list_sync = MagicMock(return_value=[a, b])
    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    data = r.json()
    assert data["total"] == 2
    assert {f["title"] for f in data["results"]} == {"Boss A", "Boss B"}


@pytest.mark.asyncio
async def test_list_parses_does_not_group_outside_window(app):
    """Same title but start times > MIRROR_WINDOW_S apart → separate
    fights (e.g. same boss killed twice an hour apart)."""
    a = dict(_FAKE_ENCOUNTER, id=1, started_at=1716561116)
    b = dict(_FAKE_ENCOUNTER, id=2, started_at=1716561116 + 600)  # 10 minutes later
    fake_list_sync = MagicMock(return_value=[a, b])
    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    data = r.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_list_parses_does_not_group_across_guilds(app):
    """Same title, same start time, different guilds → different fights."""
    a = dict(_FAKE_ENCOUNTER, id=1, guild_name="Exordium")
    b = dict(_FAKE_ENCOUNTER, id=2, guild_name="OtherGuild")
    fake_list_sync = MagicMock(return_value=[a, b])
    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_parses_clamps_fight_limit(app):
    """`limit` clamps the number of FIGHTS returned (not raw uploads).
    The inner SQL cap is generous (limit*30) so grouping has headroom."""
    captured = {}

    def fake_list_sync(inner_cap, zone, size, world="Varsoon"):
        captured["inner_cap"] = inner_cap
        captured["zone"] = zone
        captured["size"] = size
        return []

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Asking for 9999 should clamp the fight cap to 500 → inner=15000.
            await client.get("/api/parses?limit=9999")
            assert captured["inner_cap"] == 500 * 30
            # Asking for 0 should floor the fight cap to 1 → inner=max(30, 2000) = 2000.
            await client.get("/api/parses?limit=0")
            assert captured["inner_cap"] == 2000


@pytest.mark.asyncio
async def test_list_parses_passes_zone_filter(app):
    captured = {}

    def fake_list_sync(inner_cap, zone, size, world="Varsoon"):
        captured["zone"] = zone
        return []

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", side_effect=fake_list_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses?zone=Great+Divide")
            assert captured["zone"] == "Great Divide"


@pytest.mark.asyncio
async def test_list_parses_passes_size_filter(app):
    captured = {}

    def fake_list_sync(inner_cap, zone, size, world="Varsoon"):
        captured["size"] = size
        return []

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", side_effect=fake_list_sync),
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
    from backend.server.api.parses.list import SIZE_BUCKETS

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
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._encounter_detail_sync", fake_detail_sync),
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
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._encounter_detail_sync", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses/9999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_parse_clamps_top_attacks(app):
    captured = {}

    def fake_detail_sync(encounter_id, top_attacks_per_combatant, world="Varsoon"):
        captured["top"] = top_attacks_per_combatant
        return None  # 404 — we just want to inspect the captured arg

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._encounter_detail_sync", side_effect=fake_detail_sync),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses/1?top_attacks=999")
            assert captured["top"] == 50
            await client.get("/api/parses/1?top_attacks=0")
            assert captured["top"] == 1
