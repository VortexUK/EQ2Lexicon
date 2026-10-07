"""Tiered breakdown-detail retention: attack_types/damage_types kept 30/14/7 days
(curated raid / group instance / other), encounters + combatants forever, and
detail_pruned_at stamped. The zone→tier classifier is monkeypatched.
"""

from __future__ import annotations

import pytest

from backend.server.parses import cleanup
from backend.server.parses.db import store as parses_db

NOW = 1_760_000_000
DAY = 86_400

_TIERS = {"Raid Zone": 30, "Group Zone": 14}

#: Captured before the autouse fixture monkeypatches the module attribute,
#: so the mapping test can exercise the real function.
_REAL_TIER_FN = cleanup.detail_retention_days


@pytest.fixture(autouse=True)
def _fake_tiers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cleanup, "detail_retention_days", lambda zone: _TIERS.get(zone or "", 7))


def _seed(conn, *, zone: str, age_days: int, title: str = "Boss") -> int:
    started = NOW - age_days * DAY
    row = conn.execute(
        "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, duration_s,"
        " success_level, source_dsn, uploaded_by, ingested_at)"
        " VALUES ('Wuoshi', %s, %s, %s, %s, %s, 300, 1, 'seed', 'u1', %s) RETURNING id",
        (f"e-{zone}-{age_days}", title, zone, started, started + 300, NOW),
    ).fetchone()
    eid = row["id"]
    cid = conn.execute(
        "INSERT INTO combatants (encounter_id, name, ally, damage, is_player)"
        " VALUES (%s, 'Menludiir', 1, 100, 1) RETURNING id",
        (eid,),
    ).fetchone()["id"]
    conn.execute("INSERT INTO attack_types (combatant_id, swing_type, attack_name) VALUES (%s, 2, 'Smite')", (cid,))
    conn.execute("INSERT INTO damage_types (combatant_id, damage_type) VALUES (%s, 'divine')", (cid,))
    conn.commit()
    return eid


def _detail_counts(conn, eid: int) -> tuple[int, int]:
    at = conn.execute(
        "SELECT COUNT(*) AS n FROM attack_types a JOIN combatants c ON c.id = a.combatant_id WHERE c.encounter_id = %s",
        (eid,),
    ).fetchone()["n"]
    dt = conn.execute(
        "SELECT COUNT(*) AS n FROM damage_types d JOIN combatants c ON c.id = d.combatant_id WHERE c.encounter_id = %s",
        (eid,),
    ).fetchone()["n"]
    return at, dt


def test_tiers_prune_only_overdue_zones(parses_db_conn):
    conn = parses_db_conn
    raid_young = _seed(conn, zone="Raid Zone", age_days=10)
    raid_old = _seed(conn, zone="Raid Zone", age_days=31)
    dungeon_young = _seed(conn, zone="Group Zone", age_days=10)
    dungeon_old = _seed(conn, zone="Group Zone", age_days=15)
    other_young = _seed(conn, zone="Backyard", age_days=6)
    other_old = _seed(conn, zone="Backyard", age_days=8)

    pruned = cleanup._sweep_detail_retention(conn, NOW)
    assert pruned == 3

    stamps = {r["id"]: r["detail_pruned_at"] for r in conn.execute("SELECT id, detail_pruned_at FROM encounters")}
    for eid in (raid_old, dungeon_old, other_old):
        assert stamps[eid] == NOW, (eid, stamps)
        assert _detail_counts(conn, eid) == (0, 0)
    for eid in (raid_young, dungeon_young, other_young):
        assert stamps[eid] is None, (eid, stamps)
        assert _detail_counts(conn, eid) == (1, 1)

    # Summary rows are untouched.
    assert conn.execute("SELECT COUNT(*) AS n FROM encounters").fetchone()["n"] == 6
    assert conn.execute("SELECT COUNT(*) AS n FROM combatants").fetchone()["n"] == 6


def test_sweep_is_idempotent(parses_db_conn):
    conn = parses_db_conn
    _seed(conn, zone="Backyard", age_days=9)
    assert cleanup._sweep_detail_retention(conn, NOW) == 1
    assert cleanup._sweep_detail_retention(conn, NOW) == 0  # already stamped


def test_run_parse_cleanup_reports_detail_pruned(parses_db_path, monkeypatch):
    # Boss title (capitalised) so the row-retention half keeps the fight.
    conn = parses_db.init_db()
    try:
        _seed(conn, zone="Backyard", age_days=9, title="Venekor")
    finally:
        conn.close()
    out = cleanup.run_parse_cleanup(now=NOW)
    assert out["detail_pruned"] == 1, out
    assert out["trash_deleted"] == 0 and out["dup_uploads_deleted"] == 0, out


def test_default_tier_mapping_uses_zone_classifier(monkeypatch):
    import backend.server.api.parses.list as parses_list

    monkeypatch.setattr(parses_list, "_classify_zone", lambda zone: {"R": "raid", "D": "dungeon"}.get(zone, "other"))
    assert _REAL_TIER_FN("R") == cleanup.DETAIL_RETENTION_RAID_DAYS
    assert _REAL_TIER_FN("D") == cleanup.DETAIL_RETENTION_DUNGEON_DAYS
    assert _REAL_TIER_FN("X") == cleanup.DETAIL_RETENTION_OTHER_DAYS
