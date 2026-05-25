from __future__ import annotations

from web.routes.rankings import _percentile, _scope_for


class TestPercentile:
    def test_best_is_100(self):
        assert _percentile(1, 4) == 100

    def test_quartiles(self):
        assert [_percentile(r, 4) for r in (1, 2, 3, 4)] == [100, 75, 50, 25]

    def test_single_entry_is_100(self):
        assert _percentile(1, 1) == 100


class TestScopeFor:
    def test_group(self):
        assert _scope_for(2) == "group" and _scope_for(6) == "group"

    def test_raid(self):
        assert _scope_for(7) == "raid" and _scope_for(24) == "raid"

    def test_individual_and_oversize_excluded(self):
        assert _scope_for(1) is None and _scope_for(0) is None


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


def _c(name, cls, encdps, *, ally=1, guild="Exordium", level=95):
    return {
        "name": name,
        "cls": cls,
        "ally": ally,
        "encdps": encdps,
        "enchps": 0.0,
        "guild_name": guild,
        "level": level,
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

    def test_percentile_within_class(self):
        kills = [
            _kill(
                1,
                zone="Z",
                title="Tarinax",
                pcount=24,
                combatants=[
                    _c("A", "Wizard", 900.0),
                    _c("B", "Wizard", 500.0),
                    _c("H", "Templar", 100.0),
                ],
            )
        ]
        rows, classes = _build_character_board(kills, size="raid", zone="Z", boss="Tarinax", metric="dps")
        pct = {r["name"]: r["percentile"] for r in rows}
        assert pct["A"] == 100 and pct["B"] == 50  # two Wizards
        assert pct["H"] == 100  # only Templar
        assert classes == ["Templar", "Wizard"]

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
        assert [r["guild_name"] for r in rows] == ["Exordium", "Misfits"]
        assert rows[0]["duration_s"] == 168 and rows[0]["encounter_id"] == 2
        assert rows[0]["percentile"] == 100 and rows[1]["percentile"] == 50

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


class TestFilters:
    def test_tree_groups_by_scope_zone_boss(self):
        kills = [
            {"scope": "raid", "zone": "Vetrovia", "title": "Tarinax"},
            {"scope": "raid", "zone": "Vetrovia", "title": "Cazel"},
            {"scope": "group", "zone": "Crypt", "title": "Bonebreaker"},
        ]
        tree = _build_filters(kills)
        raid = next(s for s in tree["scopes"] if s["key"] == "raid")
        zone = raid["zones"][0]
        assert zone["zone"] == "Vetrovia" and zone["bosses"] == ["Cazel", "Tarinax"]
        assert {s["key"] for s in tree["scopes"]} == {"raid", "group"}


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
