from __future__ import annotations

from web.routes.rankings import _apply_percentiles, _scope_for


class TestApplyPercentiles:
    def test_leader_is_100_rest_relative(self):
        rows = [{"score": 1000.0}, {"score": 500.0}, {"score": 250.0}]
        _apply_percentiles(rows, score_key="score", higher_better=True)
        assert [r["percentile"] for r in rows] == [100, 50, 25]

    def test_single_entry_is_100(self):
        rows = [{"score": 42.0}]
        _apply_percentiles(rows, score_key="score", higher_better=True)
        assert rows[0]["percentile"] == 100

    def test_speed_lower_time_is_better(self):
        rows = [{"duration_s": 100}, {"duration_s": 200}]
        _apply_percentiles(rows, score_key="duration_s", higher_better=False)
        assert [r["percentile"] for r in rows] == [100, 50]

    def test_empty_is_zero(self):
        rows = [{"score": 0.0}]
        _apply_percentiles(rows, score_key="score", higher_better=True)
        assert rows[0]["percentile"] == 0


class TestScopeFor:
    def test_group(self):
        assert _scope_for(2) == "group" and _scope_for(6) == "group"

    def test_raid(self):
        assert _scope_for(7) == "raid" and _scope_for(24) == "raid"

    def test_solo_and_zero_excluded(self):
        assert _scope_for(1) is None and _scope_for(0) is None

    def test_large_raid_not_capped(self):
        # EQ2 ACT counts mercs/pets/swap-ins, so a 24-player raid often tallies
        # higher. Anything above the group max is a raid — never dropped.
        assert _scope_for(25) == "raid"
        assert _scope_for(30) == "raid"  # the real Wuoshi kill counted 30


from web.routes.rankings import _build_character_board


def _kill(eid, *, zone, title, pcount, combatants):
    return {
        "id": eid,
        "title": title,
        "zone": zone,
        "guild_name": "Exordium",
        "started_at": 1700000000,
        "duration_s": 60,
        "player_count": pcount,
        "scope": "raid",
        "combatants": combatants,
    }


def _c(name, cls, encdps, *, ally=1, guild="Exordium", level=95, ilvl=None):
    return {
        "name": name,
        "cls": cls,
        "ally": ally,
        "encdps": encdps,
        "enchps": 0.0,
        "guild_name": guild,
        "level": level,
        "ilvl": ilvl,
    }


class TestCharacterBoard:
    def test_keeps_personal_best_per_character(self):
        kills = [
            _kill(1, zone="Z", title="Tarinax", pcount=24, combatants=[_c("Menludiir", "Wizard", 500.0)]),
            _kill(2, zone="Z", title="Tarinax", pcount=24, combatants=[_c("Menludiir", "Wizard", 900.0)]),
        ]
        rows, classes = _build_character_board(kills, size="raid", zone="Z", boss="Tarinax", metric="dps")
        assert len(rows) == 1
        assert rows[0]["score"] == 900.0 and rows[0]["encounter_id"] == 2
        assert classes == ["Wizard"]

    def test_percentile_relative_to_board_leader(self):
        # Only the top scorer is 100%; the rest scale by score vs the leader,
        # across the whole board (not per class) — fixes the "all 100%" bug when
        # each resolved character is a different class.
        kills = [
            _kill(
                1,
                zone="Z",
                title="Tarinax",
                pcount=24,
                combatants=[
                    _c("A", "Wizard", 900.0),
                    _c("B", "Brigand", 450.0),
                    _c("H", "Templar", 90.0),
                ],
            )
        ]
        rows, classes = _build_character_board(kills, size="raid", zone="Z", boss="Tarinax", metric="dps")
        _apply_percentiles(rows, score_key="score", higher_better=True)
        pct = {r["name"]: r["percentile"] for r in rows}
        assert pct["A"] == 100  # leader
        assert pct["B"] == 50  # 450/900
        assert pct["H"] == 10  # 90/900
        assert classes == ["Brigand", "Templar", "Wizard"]

    def test_excludes_unresolved_class_and_pets(self):
        kills = [
            _kill(
                1,
                zone="Z",
                title="Tarinax",
                pcount=24,
                combatants=[
                    _c("Menludiir", "Wizard", 900.0),
                    _c("Nopclass", None, 800.0),  # unresolved class -> excluded
                    _c("a pet thing", "Wizard", 700.0),  # multi-word -> excluded
                    _c("Enemy", "Wizard", 999.0, ally=0),  # not ally -> excluded
                ],
            )
        ]
        rows, _ = _build_character_board(kills, size="raid", zone="Z", boss="Tarinax", metric="dps")
        assert [r["name"] for r in rows] == ["Menludiir"]

    def test_dedupes_case_insensitively(self):
        kills = [
            _kill(1, zone="Z", title="Tarinax", pcount=24, combatants=[_c("Menludiir", "Wizard", 500.0)]),
            _kill(2, zone="Z", title="Tarinax", pcount=24, combatants=[_c("menludiir", "Wizard", 900.0)]),
        ]
        rows, _ = _build_character_board(kills, size="raid", zone="Z", boss="Tarinax", metric="dps")
        assert len(rows) == 1
        assert rows[0]["score"] == 900.0  # same character, higher score kept

    def test_unsupported_metric_raises(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            _build_character_board([], size="raid", zone="Z", boss="Tarinax", metric="speed")

    def test_carries_character_ilvl(self):
        kills = [
            _kill(1, zone="Z", title="Tarinax", pcount=24, combatants=[_c("Menludiir", "Wizard", 900.0, ilvl=372.2)])
        ]
        rows, _ = _build_character_board(kills, size="raid", zone="Z", boss="Tarinax", metric="dps")
        assert rows[0]["ilvl"] == 372.2


from web.routes.rankings import _build_filters, _build_speed_board


class TestSpeedBoard:
    def test_best_time_per_guild(self):
        kills = [
            {
                "id": 1,
                "title": "Tarinax",
                "zone": "Z",
                "guild_name": "Exordium",
                "started_at": 1,
                "duration_s": 200,
                "player_count": 24,
                "scope": "raid",
                "combatants": [],
            },
            {
                "id": 2,
                "title": "Tarinax",
                "zone": "Z",
                "guild_name": "Exordium",
                "started_at": 2,
                "duration_s": 168,
                "player_count": 24,
                "scope": "raid",
                "combatants": [],
            },
            {
                "id": 3,
                "title": "Tarinax",
                "zone": "Z",
                "guild_name": "Misfits",
                "started_at": 3,
                "duration_s": 211,
                "player_count": 24,
                "scope": "raid",
                "combatants": [],
            },
        ]
        rows = _build_speed_board(kills, size="raid", zone="Z", boss="Tarinax")
        _apply_percentiles(rows, score_key="duration_s", higher_better=False)
        assert [r["guild_name"] for r in rows] == ["Exordium", "Misfits"]
        assert rows[0]["duration_s"] == 168 and rows[0]["encounter_id"] == 2
        # Fastest = 100%; slower scales by fastest/this (168/211 ≈ 80).
        assert rows[0]["percentile"] == 100 and rows[1]["percentile"] == 80

    def test_excludes_unresolved_guild(self):
        kills = [
            {
                "id": 1,
                "title": "Tarinax",
                "zone": "Z",
                "guild_name": None,
                "started_at": 1,
                "duration_s": 100,
                "player_count": 24,
                "scope": "raid",
                "combatants": [],
            }
        ]
        assert _build_speed_board(kills, size="raid", zone="Z", boss="Tarinax") == []

    def test_ilvl_is_raid_average(self):
        # Averages resolved players' ilvls; pets/unresolved ilvls excluded.
        kills = [
            _kill(
                1,
                zone="Z",
                title="Tarinax",
                pcount=24,
                combatants=[
                    _c("Menludiir", "Wizard", 900.0, ilvl=400.0),
                    _c("Buddy", "Templar", 100.0, ilvl=300.0),
                    _c("Nogeared", "Brigand", 50.0, ilvl=None),  # no ilvl -> excluded
                    _c("a pet thing", "Wizard", 80.0, ilvl=999.0),  # not a player -> excluded
                ],
            )
        ]
        rows = _build_speed_board(kills, size="raid", zone="Z", boss="Tarinax")
        assert rows[0]["ilvl"] == 350.0  # mean(400, 300)


class TestFilters:
    def test_tree_groups_by_scope_zone_boss(self):
        from unittest.mock import patch

        kills = [
            {"scope": "raid", "zone": "Vetrovia", "title": "Tarinax"},
            {"scope": "raid", "zone": "Vetrovia", "title": "Cazel"},
            {"scope": "group", "zone": "Crypt", "title": "Bonebreaker"},
        ]
        # Isolate from zones.db so this exercises the kills-only merge path.
        with patch("web.routes.rankings._cached_zones_data", return_value=({}, [])):
            tree = _build_filters(kills)
        raid = next(s for s in tree["scopes"] if s["key"] == "raid")
        zone = raid["zones"][0]
        assert zone["zone"] == "Vetrovia" and set(zone["bosses"]) == {"Cazel", "Tarinax"}
        assert {s["key"] for s in tree["scopes"]} == {"raid", "group"}

    def test_raid_tree_comes_from_zones_db_with_kills_appended(self):
        from unittest.mock import patch

        # zones.db supplies the full raid structure (wing order preserved);
        # group + unpopulated raid kills are merged on top.
        raid_tree = [{"zone": "Veeshan's Peak", "expansion": "RoK", "bosses": ["Kluzen", "Nexona", "Phara Dar"]}]
        kills = [
            {"scope": "raid", "zone": "Some Unpopulated Raid", "title": "Mystery Boss"},
            {"scope": "group", "zone": "Crypt", "title": "Bonebreaker"},
        ]
        with patch("web.routes.rankings._cached_zones_data", return_value=({}, raid_tree)):
            tree = _build_filters(kills)
        raid = next(s for s in tree["scopes"] if s["key"] == "raid")
        # zones.db zone first, bosses in wing order (not alphabetical).
        assert raid["zones"][0]["zone"] == "Veeshan's Peak"
        assert raid["zones"][0]["bosses"] == ["Kluzen", "Nexona", "Phara Dar"]
        assert raid["zones"][0]["expansion"] == "RoK"
        # heuristic-matched raid kill for an unlisted zone still appears, under "Other".
        other = next(z for z in raid["zones"] if z["zone"] == "Some Unpopulated Raid")
        assert other["expansion"] == "Other"

    def test_expansion_list_and_default(self, monkeypatch):
        from unittest.mock import patch

        raid_tree = [
            {"zone": "VP", "expansion": "RoK", "expansion_name": "Rise of Kunark", "bosses": ["Phara Dar"]},
            {"zone": "EH", "expansion": "EoF", "expansion_name": "Echoes of Faydwer", "bosses": ["Wuoshi"]},
        ]
        with patch("web.routes.rankings._cached_zones_data", return_value=({}, raid_tree)):
            monkeypatch.delenv("SERVER_CURRENT_XPAC", raising=False)
            f = _build_filters([])
            # newest expansion first; each raid zone tagged with its expansion.
            assert [e["short"] for e in f["raid_expansions"]] == ["RoK", "EoF"]
            assert f["raid_expansions"][0]["name"] == "Rise of Kunark"
            assert f["default_expansion"] == "RoK"  # no env → most recent
            raid = next(s for s in f["scopes"] if s["key"] == "raid")
            assert {z["zone"]: z["expansion"] for z in raid["zones"]} == {"VP": "RoK", "EH": "EoF"}

            monkeypatch.setenv("SERVER_CURRENT_XPAC", "EoF")
            assert _build_filters([])["default_expansion"] == "EoF"  # short code

            monkeypatch.setenv("SERVER_CURRENT_XPAC", "Echoes of Faydwer")
            assert _build_filters([])["default_expansion"] == "EoF"  # full name

            monkeypatch.setenv("SERVER_CURRENT_XPAC", "echoes of faydwer")
            assert _build_filters([])["default_expansion"] == "EoF"  # case-insensitive

            monkeypatch.setenv("SERVER_CURRENT_XPAC", "ZZZ")
            assert _build_filters([])["default_expansion"] == "RoK"  # invalid env → most recent

    def test_resolve_boss_uses_zones_db_for_raids(self):
        from unittest.mock import patch

        from web.routes.rankings import _resolve_boss

        index = {"phara dar": [("Veeshan's Peak", "Phara Dar")]}
        with patch("web.routes.rankings._cached_zones_data", return_value=(index, [])):
            # Raid: matches zones.db → canonical zone + encounter.
            assert _resolve_boss("Phara Dar", "ACT Zone Name", "raid") == (True, "Veeshan's Peak", "Phara Dar")
            # Raid, unknown to zones.db → heuristic fallback keeps ACT zone/title.
            assert _resolve_boss("Tarinax", "Vetrovia", "raid") == (True, "Vetrovia", "Tarinax")
            # Heuristic correctly rejects trash ("a "/"an " prefixes).
            assert _resolve_boss("a decaying skeleton", "Vetrovia", "raid")[0] is False
            # Group scope never consults zones.db — pure heuristic.
            assert _resolve_boss("Phara Dar", "Crypt", "group") == (True, "Crypt", "Phara Dar")


import time as _time

import pytest

from parses import db as pdb
from parses.models import Combatant, CombatantSnapshot, Encounter


def _ins(conn, encid, title, *, success, players, guild, duration):
    enc = Encounter(
        encid=encid,
        title=title,
        zone="Vetrovia",
        started_at=None,
        ended_at=None,
        duration_s=duration,
        total_damage=1,
        encdps=1.0,
        kills=1,
        deaths=0,
        success_level=success,
    )
    eid = pdb.insert_encounter(
        conn, enc, source_dsn="eq2act", ingested_at=int(_time.time()), uploaded_by="Up", guild_name=guild
    )
    combs = [
        Combatant(
            encid=encid,
            name=f"P{i}",
            ally=True,
            started_at=None,
            ended_at=None,
            duration_s=duration,
            damage=1,
            damage_perc=0.0,
            kills=0,
            healed=0,
            healed_perc=0.0,
            crit_heals=0,
            heals=0,
            cure_dispels=0,
            power_drain=0,
            power_replenish=0,
            dps=0.0,
            encdps=float(100 - i),
            enchps=0.0,
            hits=0,
            crit_hits=0,
            blocked=0,
            misses=0,
            swings=0,
            heals_taken=0,
            damage_taken=0,
            deaths=0,
            to_hit=0.0,
            crit_dam_perc=0.0,
            crit_heal_perc=0.0,
            crit_types=None,
            threat_str=None,
            threat_delta=0,
        )
        for i in range(players)
    ]
    snaps = {f"P{i}": CombatantSnapshot(level=95, guild_name=guild, cls="Wizard") for i in range(players)}
    pdb.insert_combatants_bulk(conn, eid, combs, snaps)
    conn.commit()


@pytest.fixture()
def rankings_db(tmp_path, monkeypatch):
    db_file = tmp_path / "parses.db"
    monkeypatch.setattr(pdb, "DB_PATH", db_file)
    conn = pdb.init_db(db_file)
    _ins(conn, "WIN", "Tarinax", success=1, players=8, guild="Exordium", duration=60)  # boss, raid
    _ins(conn, "TRASH", "a krait", success=1, players=8, guild="Exordium", duration=30)  # not boss
    _ins(conn, "LOSS", "Cazel", success=2, players=8, guild="Exordium", duration=90)  # not a win
    conn.close()
    from web.routes import rankings as rk

    rk.rankings_cache.delete(rk._KILLS_KEY)
    return db_file


def test_loader_keeps_only_winning_boss_kills(rankings_db):
    from web.routes.rankings import _load_primary_boss_kills

    kills = _load_primary_boss_kills()
    assert [k["title"] for k in kills] == ["Tarinax"]
    assert kills[0]["scope"] == "raid" and kills[0]["player_count"] == 8
    assert len(kills[0]["combatants"]) == 8


from unittest.mock import patch

from httpx import ASGITransport, AsyncClient


def _fake_user(request=None) -> dict:
    return {"id": "123", "username": "alice"}


@pytest.mark.asyncio
async def test_filters_endpoint(app, rankings_db):
    with patch("web.routes.rankings._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/rankings/filters")
    assert r.status_code == 200
    scopes = {s["key"] for s in r.json()["scopes"]}
    assert "raid" in scopes


@pytest.mark.asyncio
async def test_rankings_dps_board(app, rankings_db):
    with patch("web.routes.rankings._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/rankings?size=raid&zone=Vetrovia&boss=Tarinax&metric=dps")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 8 and body["rows"][0]["kind"] == "character"
    assert body["rows"][0]["percentile"] == 100
    assert body["classes"] == ["Wizard"]


@pytest.mark.asyncio
async def test_rankings_rejects_bad_metric(app, rankings_db):
    with patch("web.routes.rankings._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/rankings?size=raid&zone=Vetrovia&boss=Tarinax&metric=bogus")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rankings_speed_board(app, rankings_db):
    with patch("web.routes.rankings._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/rankings?size=raid&zone=Vetrovia&boss=Tarinax&metric=speed")
    assert r.status_code == 200
    body = r.json()
    assert body["classes"] == []
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["kind"] == "guild"
    assert row["guild_name"] == "Exordium"
    assert row["duration_s"] == 60
    assert row["score"] is None  # guild rows carry duration, not score


@pytest.mark.asyncio
async def test_rankings_class_filter(app, rankings_db):
    with patch("web.routes.rankings._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            wiz = await client.get("/api/rankings?size=raid&zone=Vetrovia&boss=Tarinax&metric=dps&class=Wizard")
            templar = await client.get("/api/rankings?size=raid&zone=Vetrovia&boss=Tarinax&metric=dps&class=Templar")
    assert wiz.status_code == 200
    wiz_body = wiz.json()
    assert wiz_body["total"] == 8
    assert all(row["cls"] == "Wizard" for row in wiz_body["rows"])
    # No Templar entries on this board → empty rows (classes still lists what exists).
    assert templar.json()["total"] == 0


@pytest.mark.asyncio
async def test_rankings_rejects_bad_size(app, rankings_db):
    with patch("web.routes.rankings._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/rankings?size=bogus&zone=Vetrovia&boss=Tarinax&metric=dps")
    assert r.status_code == 400
