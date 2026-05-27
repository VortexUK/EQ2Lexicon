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
    # Solo fight, single uploader — no mirror grouping needed.
    fake_list_sync = MagicMock(return_value=[dict(_FAKE_ENCOUNTER, combatant_count=2, player_count=1)])

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
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
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._list_encounters_sync", side_effect=fake_list_sync),
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

    def fake_detail_sync(encounter_id, top_attacks_per_combatant, world="Varsoon"):
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
    """Build a MagicMock connection whose `execute()` returns the given row via
    both fetchone and fetchall (the delete auth path uses an `IN (...)` query
    → fetchall). Used to fake the per-id encounter lookup inside delete_parse."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = row
    cur.fetchall.return_value = [row] if row else []
    conn.execute.return_value = cur
    return conn


def _fake_conn_multi(rows: list[dict]) -> MagicMock:
    """Like _fake_conn_for_fetch but for the batch path — fetchall returns the
    full row list."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = rows[0] if rows else None
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
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:99999",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
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
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:123456789",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
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
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:OTHER_USER",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
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
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:OTHER_USER",
        "title": "a krait patriarch",
        "hidden_at": None,
    }

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
# DELETE /api/parses/batch (whole multi-uploader encounter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_batch_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_batch_officer_deletes_all_uploads(app):
    # Two uploads of one fight, both by *other* people; caller is an officer of
    # the guild → may delete the whole encounter.
    rows = [
        {
            "id": 1,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER1",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
        {
            "id": 2,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER2",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
    ]
    delete_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}
    assert sorted(c.args[1] for c in delete_mock.call_args_list) == [1, 2]


@pytest.mark.asyncio
async def test_delete_batch_admin_deletes_all_uploads(app):
    rows = [
        {"id": 5, "guild_name": None, "source_dsn": "plugin:OTHER1", "title": "a krait patriarch", "hidden_at": None},
        {
            "id": 6,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER2",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
    ]
    delete_mock = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=5,6")
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}


@pytest.mark.asyncio
async def test_delete_batch_skips_unauthorised_ids(app):
    # Caller (_fake_user id=123456789) uploaded id 1 only; not officer/admin.
    # The batch deletes the one they own and skips the other.
    rows = [
        {
            "id": 1,
            "guild_name": "Exordium",
            "source_dsn": "plugin:123456789",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
        {
            "id": 2,
            "guild_name": "Exordium",
            "source_dsn": "plugin:SOMEONE_ELSE",
            "title": "a krait patriarch",
            "hidden_at": None,
        },
    ]
    delete_mock = MagicMock(return_value=True)

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.parses_db.delete_encounter", delete_mock),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}
    assert [c.args[1] for c in delete_mock.call_args_list] == [1]


@pytest.mark.asyncio
async def test_delete_batch_none_allowed_403(app):
    rows = [
        {
            "id": 1,
            "guild_name": "Exordium",
            "source_dsn": "plugin:OTHER",
            "title": "a krait patriarch",
            "hidden_at": None,
        }
    ]

    async def fake_officer_chars(discord_id, guild):
        return set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi(rows)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_batch_404_when_no_rows(app):
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi([])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_batch_rejects_bad_ids(app):
    with patch("web.routes.parses._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            empty = await client.delete("/api/parses/batch?ids=")
            bad = await client.delete("/api/parses/batch?ids=abc")
    assert empty.status_code == 400
    assert bad.status_code == 400


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

    def fake_find(conn, *, guild_name, zone=None, date=None, uploaded_by=None, world=None):
        captured.update(guild_name=guild_name, zone=zone, date=date, uploaded_by=uploaded_by, world=world)
        return [
            {"id": 1, "title": "a krait patriarch", "guild_name": guild_name, "source_dsn": "plugin:X"},
            {"id": 2, "title": "a krait patriarch", "guild_name": guild_name, "source_dsn": "plugin:Y"},
        ]

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=MagicMock()),
        patch("web.routes.parses.parses_db.find_encounters_by_filter", fake_find),
        patch("web.routes.parses.parses_db.delete_encounter", MagicMock(return_value=True)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&zone=Great+Divide&date=2026-05-24&uploader=Menludiir")
    assert r.status_code == 200
    assert r.json() == {"deleted": 2}
    assert captured["guild_name"] == "Exordium"
    assert captured["zone"] == "Great Divide"
    assert captured["date"] == "2026-05-24"
    assert captured["uploaded_by"] == "Menludiir"
    # world must always be passed (per-server isolation)
    assert captured["world"] is not None


@pytest.mark.asyncio
async def test_delete_bulk_officer_allowed(app):
    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"} if guild == "Exordium" else set()

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
        patch("web.routes.parses.parses_db.init_db", return_value=MagicMock()),
        patch(
            "web.routes.parses.parses_db.find_encounters_by_filter",
            MagicMock(
                return_value=[
                    {"id": 1, "title": "a krait patriarch", "guild_name": "Exordium", "source_dsn": "plugin:X"},
                ]
            ),
        ),
        patch("web.routes.parses.parses_db.delete_encounter", MagicMock(return_value=True)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 200
    assert r.json() == {"deleted": 1}


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


# ---------------------------------------------------------------------------
# Soft-delete visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_excludes_hidden_rows(app, tmp_path, monkeypatch):
    # Real temp DB: one visible boss kill, one soft-deleted.
    import time as _t

    from parses import db as pdb
    from parses.models import Encounter

    db_file = tmp_path / "parses.db"
    monkeypatch.setattr(pdb, "DB_PATH", db_file)
    conn = pdb.init_db(db_file)
    for encid, title in [("AAA", "Tarinax"), ("BBB", "Venekor")]:
        enc = Encounter(
            encid=encid,
            title=title,
            zone="Zone",
            started_at=None,
            ended_at=None,
            duration_s=60,
            total_damage=1,
            encdps=1.0,
            kills=1,
            deaths=0,
            success_level=1,
        )
        eid = pdb.insert_encounter(conn, enc, source_dsn="eq2act", ingested_at=int(_t.time()))
        if encid == "BBB":
            pdb.soft_delete_encounter(conn, eid, hidden_at=int(_t.time()))
    conn.close()

    with patch("web.routes.parses._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    titles = {f["title"] for f in r.json()["results"]}
    assert "Tarinax" in titles
    assert "Venekor" not in titles  # soft-deleted → hidden from list


@pytest.mark.asyncio
async def test_detail_reports_hidden_flag(app):
    enc = {
        "id": 1,
        "act_encid": "X",
        "title": "Tarinax",
        "zone": "Z",
        "started_at": 1,
        "ended_at": 2,
        "duration_s": 1,
        "total_damage": 0,
        "encdps": 0.0,
        "kills": 0,
        "deaths": 0,
        "success_level": 1,
        "hidden_at": 1700000000,
        "combatants": [],
    }
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._encounter_detail_sync", MagicMock(return_value=enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses/1")
    assert r.status_code == 200
    assert r.json()["hidden"] is True


# ---------------------------------------------------------------------------
# Boss soft-delete vs trash hard-delete, admin purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_boss_soft_deletes(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:123456789", "title": "Tarinax", "hidden_at": None}
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    soft.assert_called_once()
    hard.assert_not_called()


@pytest.mark.asyncio
async def test_delete_trash_hard_deletes(app):
    enc = {
        "id": 1,
        "guild_name": "Exordium",
        "source_dsn": "plugin:123456789",
        "title": "a krait patriarch",
        "hidden_at": None,
    }
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1")
    assert r.status_code == 200
    hard.assert_called_once()
    soft.assert_not_called()


@pytest.mark.asyncio
async def test_admin_purge_hard_deletes_boss(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER", "title": "Tarinax", "hidden_at": None}
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1?purge=1")
    assert r.status_code == 200
    hard.assert_called_once()
    soft.assert_not_called()


@pytest.mark.asyncio
async def test_purge_forbidden_for_non_admin(app):
    enc = {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:123456789", "title": "Tarinax", "hidden_at": None}
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_for_fetch(enc)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/1?purge=1")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_batch_boss_soft_deletes_each(app):
    rows = [
        {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER1", "title": "Tarinax", "hidden_at": None},
        {"id": 2, "guild_name": "Exordium", "source_dsn": "plugin:OTHER2", "title": "Tarinax", "hidden_at": None},
    ]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert soft.call_count == 2
    hard.assert_not_called()


@pytest.mark.asyncio
async def test_delete_batch_purge_hard_deletes_each(app):
    rows = [
        {"id": 1, "guild_name": "Exordium", "source_dsn": "plugin:OTHER1", "title": "Tarinax", "hidden_at": None},
        {"id": 2, "guild_name": "Exordium", "source_dsn": "plugin:OTHER2", "title": "Tarinax", "hidden_at": None},
    ]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=_fake_conn_multi(rows)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses/batch?ids=1,2&purge=1")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    assert hard.call_count == 2
    soft.assert_not_called()


# ---------------------------------------------------------------------------
# Bulk-by-filter soft-delete vs hard-delete (Task 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_delete_soft_deletes_bosses(app):
    matches = [
        {"id": 1, "title": "Tarinax", "guild_name": "Exordium", "source_dsn": "plugin:OTHER"},
        {"id": 2, "title": "a krait patriarch", "guild_name": "Exordium", "source_dsn": "plugin:OTHER"},
    ]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=MagicMock()),
        patch("web.routes.parses.parses_db.find_encounters_by_filter", MagicMock(return_value=matches)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium")
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    soft.assert_called_once()  # Tarinax (boss)
    hard.assert_called_once()  # trash


@pytest.mark.asyncio
async def test_bulk_delete_purge_hard_deletes_boss(app):
    matches = [{"id": 1, "title": "Tarinax", "guild_name": "Exordium", "source_dsn": "plugin:OTHER"}]
    soft = MagicMock(return_value=True)
    hard = MagicMock(return_value=True)
    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=True),
        patch("web.routes.parses.parses_db.init_db", return_value=MagicMock()),
        patch("web.routes.parses.parses_db.find_encounters_by_filter", MagicMock(return_value=matches)),
        patch("web.routes.parses.parses_db.soft_delete_encounter", soft),
        patch("web.routes.parses.parses_db.delete_encounter", hard),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&purge=1")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    hard.assert_called_once()  # purge forces hard delete even for a boss
    soft.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_delete_purge_forbidden_for_non_admin(app):
    async def fake_officer_chars(discord_id, guild):
        return {"menludiir"}  # officer, but NOT admin

    with (
        patch("web.routes.parses._require_user", _fake_user),
        patch("web.routes.parses._is_admin", return_value=False),
        patch("web.routes.guild._officer_chars", fake_officer_chars),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/parses?guild=Exordium&purge=1")
    assert r.status_code == 403
