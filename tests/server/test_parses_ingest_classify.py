"""Tests for the Phase-3 classifier integration into the ingest path.

Verifies the classifier runs at upload time (populates is_player on
every newly-inserted combatant row) and re-runs after the async
snapshot fill completes (cls flips → is_player can flip too).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest

from backend.server.parses import db as parses_db
from backend.server.parses.models import Combatant, CombatantSnapshot, Encounter


def _enc(*, encid: str, zone: str, title: str = "Test Boss") -> Encounter:
    return Encounter(
        encid=encid,
        title=title,
        zone=zone,
        started_at=datetime(2026, 5, 24, 13, 51, 56),
        ended_at=datetime(2026, 5, 24, 13, 52, 56),
        duration_s=60,
        total_damage=120000,
        encdps=2000.0,
        kills=1,
        deaths=0,
    )


def _combatant(
    name: str,
    *,
    encid: str,
    ally: bool,
    encdps: float = 100.0,
    enchps: float = 0.0,
) -> Combatant:
    """Factory helper: build a fully-valid Combatant with test defaults."""
    return Combatant(
        encid=encid,
        name=name,
        ally=ally,
        started_at=datetime(2026, 5, 24, 13, 51, 56),
        ended_at=datetime(2026, 5, 24, 13, 52, 42),
        duration_s=46,
        damage=int(encdps * 46),
        damage_perc=100.0 if ally else 0.0,
        kills=0,
        healed=0,
        healed_perc=0.0,
        crit_heals=0,
        heals=0,
        cure_dispels=0,
        power_drain=0,
        power_replenish=0,
        dps=encdps,
        encdps=encdps,
        enchps=enchps,
        hits=10,
        crit_hits=9,
        blocked=0,
        misses=0,
        swings=10,
        heals_taken=0,
        damage_taken=0,
        deaths=0,
        to_hit=100.0,
        crit_dam_perc=90.0,
        crit_heal_perc=0.0,
        crit_types=None,
        threat_str=None,
        threat_delta=0,
    )


def _snap(*, cls=None, level=None, guild_name=None, ilvl=None) -> CombatantSnapshot:
    return CombatantSnapshot(level=level, guild_name=guild_name, cls=cls, ilvl=ilvl)


def _encounter_id_for(conn: Any, act_encid: str) -> int:
    row = conn.execute("SELECT id FROM encounters WHERE act_encid = %s", (act_encid,)).fetchone()
    return int(row["id"])


@pytest.fixture
def parses_conn(parses_db_path):
    """Test-owned connection to the leased scratch parses schema.

    ``parses_db_path`` has already re-pointed the shared store, so
    _insert_encounter_rows_sync (caller-supplied conn, commits at the end)
    and _update_snapshots_sync (checks out + closes its own connection via
    parses_db.store.init_db()) both target the same schema; committed writes
    are visible across connections (READ COMMITTED), so the old SQLite
    shared-connection/_NoCloseConn trick is no longer needed.
    """
    conn = parses_db.store.init_db()
    try:
        yield conn
    finally:
        conn.close()


def test_ingest_populates_is_player_on_every_combatant(parses_conn):
    """Synthesise an ingest call directly (skip HTTP) and confirm
    is_player flows onto every combatant row."""
    from backend.server.api.parses.ingest import _insert_encounter_rows_sync

    enc = _enc(encid="testenc", zone="Halls of Fate")
    combatants = [
        # confirmed player via cls (will be set via snapshot)
        _combatant("Alpha", encid="testenc", ally=True, encdps=1000.0, enchps=200.0),
        # multi-word pet
        _combatant("a krait warrior", encid="testenc", ally=True, encdps=500.0, enchps=0.0),
        # regex-match pet
        _combatant("Gibab", encid="testenc", ally=True, encdps=400.0, enchps=0.0),
        # enemy (ally=False)
        _combatant("Test Boss", encid="testenc", ally=False, encdps=0.0, enchps=300.0),
    ]
    snapshots = {"Alpha": _snap(cls="Wizard")}

    with patch("backend.server.api.parses.ingest._classify_zone", return_value="dungeon"):
        _insert_encounter_rows_sync(
            parses_conn,
            enc,
            combatants=combatants,
            damage_types=[],
            attack_types=[],
            snapshots=snapshots,
            uploaded_by="Alpha",
            guild_name="Exordium",
            source_dsn="test",
            world="Varsoon",
        )

    rows = parses_conn.execute("SELECT name, ally, is_player FROM combatants ORDER BY id").fetchall()
    by_name = {r["name"]: r for r in rows}

    # Stage 5 — cls set → player
    assert by_name["Alpha"]["is_player"] == 1, by_name["Alpha"]
    # Stage 3 — multi-word → pet
    assert by_name["a krait warrior"]["is_player"] == 0, by_name["a krait warrior"]
    # Stage 4 — regex match → pet
    assert by_name["Gibab"]["is_player"] == 0, by_name["Gibab"]
    # Enemy (ally=0) → classifier omits, is_player stays NULL
    assert by_name["Test Boss"]["is_player"] is None, by_name["Test Boss"]


def test_async_snapshot_fill_reclassifies(parses_conn):
    """After ingest, an async snapshot fill updates cls. The classifier
    must re-run so is_player can flip from 0 → 1 for a previously-
    unconfirmed combatant that Census just resolved."""
    from backend.server.api.parses.ingest import _insert_encounter_rows_sync, _update_snapshots_sync

    enc = _enc(encid="testenc2", zone="Antonica", title="Other Boss")
    # 5 unconfirmed allies (n_total=5, zone='other' → no bucket-fill).
    combatants = [_combatant(f"P{i}", encid="testenc2", ally=True, encdps=100.0 * i, enchps=0.0) for i in range(1, 6)]

    with patch("backend.server.api.parses.ingest._classify_zone", return_value="other"):
        _insert_encounter_rows_sync(
            parses_conn,
            enc,
            combatants=combatants,
            damage_types=[],
            attack_types=[],
            snapshots=None,
            uploaded_by="P1",
            guild_name=None,
            source_dsn="test",
            world="Varsoon",
        )

    # All five start as is_player=0 because no cls + no bucket-fill in 'other' n_total=5.
    baseline = {r["name"]: r["is_player"] for r in parses_conn.execute("SELECT name, is_player FROM combatants")}
    assert all(v == 0 for v in baseline.values()), baseline

    # Simulate async fill: cls becomes known for P1 + P2. (_update_snapshots_sync
    # opens — and commits + closes — its own connection via the re-pointed store.)
    enc_id = _encounter_id_for(parses_conn, "testenc2")
    snapshots = {"P1": _snap(cls="Wizard"), "P2": _snap(cls="Berserker")}
    with patch("backend.server.api.parses.ingest._classify_zone", return_value="other"):
        _update_snapshots_sync(enc_id, snapshots)

    after = {r["name"]: r["is_player"] for r in parses_conn.execute("SELECT name, is_player FROM combatants")}
    assert after["P1"] == 1
    assert after["P2"] == 1
    assert after["P3"] == 0
    assert after["P4"] == 0
    assert after["P5"] == 0
