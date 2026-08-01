"""Tests for GET /api/character/{name}/rankings (WCL-style per-boss summary).

The kills dataset is faked at the _cached_kills seam (same data shape the
rankings module builds from parses.db), so these tests pin the percentile,
median, All Stars, and gating semantics without any DB plumbing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api.character import rankings as mod
from tests.fixtures.users import make_fake_require_user, make_fake_user

_fake_user = make_fake_require_user(make_fake_user(id="123"))


def _combatant(name: str, cls: str, dps: float, hps: float = 0.0) -> dict:
    return {"name": name, "ally": 1, "cls": cls, "encdps": dps, "enchps": hps}


def _kill(kid: int, boss: str, duration: int, combatants: list[dict], *, zone: str = "Deathtoll") -> dict:
    return {
        "id": kid,
        "title": boss,
        "zone": zone,
        "guild_name": "Testers",
        "started_at": 1_700_000_000 + kid,
        "duration_s": duration,
        "player_count": 24,
        "scope": "raid",
        "combatants": combatants,
    }


# The curated universe the tab is gated to — mirrors _cached_zones_data's
# (boss_index, raid_tree, dungeon_tree, curated_zone_names) shape. "Uncurated Keep" is absent on
# purpose: kills there must never surface.
FAKE_TREES = (
    {},
    [
        {
            "zone": "Deathtoll",
            "expansion": "KoS",
            "expansion_name": "Kingdom of Sky",
            "bosses": ["Tarinax the Destroyer", "Xerkizh The Creator"],
        }
    ],
    [],
    {"Deathtoll"},
)

# Tarinax: Templar pool = [1000, 1200, 2000] (Menlu twice, Elesine once).
# Wizard pool = [5000]. Elesine also solo-kills Xerkizh (boss the target
# never killed) — it must still count toward zone All Stars coverage.
KILLS = [
    _kill(
        1,
        "Tarinax the Destroyer",
        600,
        [
            _combatant("Menlu", "Templar", 1000, hps=3000),
            _combatant("Wizzy", "Wizard", 5000),
        ],
    ),
    _kill(
        2,
        "Tarinax the Destroyer",
        540,
        [
            _combatant("Menlu", "Templar", 1200, hps=2500),
            _combatant("Elesine", "Templar", 2000, hps=4000),
        ],
    ),
    _kill(
        3,
        "Xerkizh The Creator",
        300,
        [
            _combatant("Elesine", "Templar", 1800, hps=3500),
        ],
    ),
    # Heuristic-matched named in an uncurated zone — huge score, but it must
    # never appear in the tab nor pollute any pool.
    _kill(
        4,
        "A Very Big Named",
        120,
        [
            _combatant("Menlu", "Templar", 99_999, hps=99_999),
        ],
        zone="Uncurated Keep",
    ),
]


def test_percentiles_are_class_scoped():
    resp = mod._build_character_rankings("Menlu", "Wuoshi")
    assert resp.cls == "Templar"
    (zone,) = resp.zones
    assert (zone.zone, zone.scope) == ("Deathtoll", "raid")
    (row,) = zone.bosses  # Menlu only killed Tarinax
    assert row.boss == "Tarinax the Destroyer"
    assert row.kills == 2
    assert row.fastest_s == 540
    assert row.fastest_encounter_id == 2

    dps = row.dps
    assert dps is not None
    # Best 1200 beats 1 of the 2 other Templar parses → 50. The Wizard's
    # 5000 is in a different class pool and must not drag this down.
    assert dps.best_pct == 50
    assert dps.best_score == 1200
    assert dps.encounter_id == 2
    # Parse percentiles: 1000 → 0, 1200 → 50 → median 25.
    assert dps.median_pct == 25
    # All Stars: 100 × 1200/2000 vs the class record; rank 2 of 2 Templars.
    assert dps.points == 60.0
    assert (dps.rank, dps.out_of) == (2, 2)


def test_wizard_pool_is_independent():
    resp = mod._build_character_rankings("Wizzy", "Wuoshi")
    (zone,) = resp.zones
    dps = zone.bosses[0].dps
    assert dps is not None
    assert dps.best_pct == 100  # alone in the Wizard pool → class record
    assert dps.points == 100.0
    assert (dps.rank, dps.out_of) == (1, 1)
    # No healing parses → no hps stats.
    assert zone.bosses[0].hps is None


def test_hps_metric_computed_alongside():
    resp = mod._build_character_rankings("Menlu", "Wuoshi")
    hps = resp.zones[0].bosses[0].hps
    assert hps is not None
    # HPS pool [3000, 2500, 4000]; best 3000 beats 1 of 2 others → 50.
    assert hps.best_pct == 50
    assert hps.best_score == 3000
    assert hps.encounter_id == 1  # best HPS parse was kill 1, not the DPS best
    assert hps.points == 75.0  # 100 × 3000/4000


def test_zone_allstars_cover_bosses_target_never_killed():
    resp = mod._build_character_rankings("Menlu", "Wuoshi")
    allstars = resp.zones[0].dps_allstars
    assert allstars is not None
    # Menlu: 60 pts (Tarinax only). Elesine: 100 (Tarinax record) + 100
    # (Xerkizh record) = 200 — the Xerkizh kill counts even though Menlu
    # never fought it.
    assert allstars.points == 60.0
    assert (allstars.rank, allstars.out_of) == (2, 2)

    elesine = mod._build_character_rankings("Elesine", "Wuoshi")
    e_allstars = elesine.zones[0].dps_allstars
    assert e_allstars is not None
    assert e_allstars.points == 200.0
    assert e_allstars.rank == 1


def test_unranked_character_gets_empty_zones():
    resp = mod._build_character_rankings("Ghostchar", "Wuoshi")
    assert resp.zones == []
    assert resp.cls is None


def test_uncurated_kills_are_excluded_and_expansions_listed():
    """Only curated rankings content surfaces: Menlu's 99,999-DPS kill in
    'Uncurated Keep' must not appear anywhere — no boss row, no pool
    pollution. Zone sections carry the expansion tag for the dropdown."""
    resp = mod._build_character_rankings("Menlu", "Wuoshi")
    (zone,) = resp.zones  # only the curated Deathtoll section
    assert zone.zone == "Deathtoll"
    assert zone.expansion == "KoS"
    assert [b.boss for b in zone.bosses] == ["Tarinax the Destroyer"]
    assert [e.model_dump() for e in resp.expansions] == [{"short": "KoS", "name": "Kingdom of Sky"}]
    # The uncurated monster score never entered the Templar pool: the best
    # percentile still ranks against [1000, 1200, 2000] only.
    assert zone.bosses[0].dps is not None
    assert zone.bosses[0].dps.best_pct == 50


def test_character_with_only_uncurated_kills_gets_empty_zones():
    """A character whose parses are all heuristic-matched noise gets zones=[]
    — the tab stays hidden for them."""
    solo = mod._build_character_rankings("Menlu", "Wuoshi")
    assert solo.zones  # sanity: Menlu has curated kills

    uncurated_only = [k for k in KILLS if k["zone"] == "Uncurated Keep"]
    with patch.object(mod, "_cached_kills", lambda world: uncurated_only):
        resp = mod._build_character_rankings("Menlu", "Wuoshi")
    assert resp.zones == []
    assert resp.expansions == []


@pytest.mark.asyncio
async def test_endpoint_serves_and_validates(app):
    with (
        patch.object(mod, "_cached_kills", lambda world: KILLS),
        patch("backend.server.api.character.rankings._require_user", _fake_user),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            ok = await http.get("/api/character/Menlu/rankings")
            bad = await http.get("/api/character/bad name!!/rankings")
    assert ok.status_code == 200
    body = ok.json()
    assert body["name"] == "Menlu"
    assert body["zones"][0]["bosses"][0]["dps"]["best_pct"] == 50
    assert bad.status_code == 400


@pytest.fixture(autouse=True)
def _fake_kills(monkeypatch):
    monkeypatch.setattr(mod, "_cached_kills", lambda world: KILLS)
    monkeypatch.setattr(mod, "_cached_zones_data", lambda: FAKE_TREES)
