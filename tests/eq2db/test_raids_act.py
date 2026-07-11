"""Tests for census.raids_act_db — COV-010.

Uses in-memory SQLite per test (via census.raids_db.init_db). Covers:
- list_act_triggers_for_encounter ordering + missing-path fallback
- get_act_trigger unknown id → None
- upsert_act_trigger INSERT vs UPDATE + edited_by stamp
- delete_act_trigger returns True/False
- Spell-timer helpers (same shape)
- upsert_act_spell_timer name_lower UNIQUE collision

Target: ≥ 80% on census.raids_act_db.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.eq2db.raids import RaidCatalogue

# Conn-taking write helpers are staticmethods — alias for readable call sites.
upsert_act_trigger = RaidCatalogue.upsert_act_trigger
delete_act_trigger = RaidCatalogue.delete_act_trigger
upsert_act_spell_timer = RaidCatalogue.upsert_act_spell_timer
delete_act_spell_timer = RaidCatalogue.delete_act_spell_timer


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh raids.db (file-backed via tmp_path) with one zone + encounter."""
    p = tmp_path / "raids.db"
    conn = RaidCatalogue(p).init_db()
    # Insert a seed raid_zone + encounter so FK-style references are valid
    conn.execute(
        "INSERT INTO raid_zones (zone_name, zone_name_lower, expansion_short, source) "
        "VALUES ('Test Zone', 'test zone', 'TS', 'manual')"
    )
    zone_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO raid_encounters (raid_zone_id, mob_name, mob_name_lower, source) "
        "VALUES (?, 'Boss One', 'boss one', 'manual')",
        (zone_id,),
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def enc_id(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM raid_encounters LIMIT 1").fetchone()
    return row[0]


@pytest.fixture
def db_conn(db_path: Path):
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# ACT Trigger helpers
# ---------------------------------------------------------------------------


class TestListActTriggersForEncounter:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.db"
        assert RaidCatalogue(missing).list_act_triggers_for_encounter(1) == []

    def test_returns_empty_for_unknown_encounter(self, db_path: Path):
        assert RaidCatalogue(db_path).list_act_triggers_for_encounter(9999) == []

    def test_ordering_by_position_then_id(self, db_path: Path, db_conn, enc_id: int):
        # Insert triggers with shuffled positions
        upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="c", position=2)
        upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="a", position=0)
        upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="b", position=1)
        rows = RaidCatalogue(db_path).list_act_triggers_for_encounter(enc_id)
        assert len(rows) == 3
        assert rows[0]["regex"] == "a"
        assert rows[1]["regex"] == "b"
        assert rows[2]["regex"] == "c"


class TestGetActTrigger:
    def test_returns_none_when_path_missing(self, tmp_path: Path):
        assert RaidCatalogue(tmp_path / "no.db").get_act_trigger(1) is None

    def test_returns_none_for_unknown_id(self, db_path: Path):
        assert RaidCatalogue(db_path).get_act_trigger(9999) is None

    def test_returns_dict_for_existing_trigger(self, db_path: Path, db_conn, enc_id: int):
        trigger_id = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="test-regex", label="Boss Pull")
        row = RaidCatalogue(db_path).get_act_trigger(trigger_id)
        assert row is not None
        assert row["regex"] == "test-regex"
        assert row["label"] == "Boss Pull"


class TestUpsertActTrigger:
    def test_insert_returns_new_id(self, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="new-trigger")
        assert isinstance(tid, int)
        assert tid > 0

    def test_update_returns_same_id(self, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="original")
        returned = upsert_act_trigger(db_conn, trigger_id=tid, raid_encounter_id=enc_id, regex="updated")
        assert returned == tid

    def test_stamps_edited_by(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="x", edited_by="user-123")
        row = RaidCatalogue(db_path).get_act_trigger(tid)
        assert row["last_edited_by"] == "user-123"

    def test_stamps_last_edited_at(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="x")
        row = RaidCatalogue(db_path).get_act_trigger(tid)
        assert row["last_edited_at"] is not None and row["last_edited_at"] > 0


class TestDeleteActTrigger:
    def test_returns_true_when_deleted(self, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="to-delete")
        assert delete_act_trigger(db_conn, tid) is True

    def test_returns_false_for_unknown_id(self, db_conn):
        assert delete_act_trigger(db_conn, 9999) is False

    def test_row_gone_after_delete(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="gone")
        delete_act_trigger(db_conn, tid)
        assert RaidCatalogue(db_path).get_act_trigger(tid) is None


# ---------------------------------------------------------------------------
# ACT Spell Timer helpers
# ---------------------------------------------------------------------------


class TestListActSpellTimersForEncounter:
    def test_returns_empty_when_path_missing(self, tmp_path: Path):
        assert RaidCatalogue(tmp_path / "no.db").list_act_spell_timers_for_encounter(1) == []

    def test_returns_empty_for_unknown_encounter(self, db_path: Path):
        assert RaidCatalogue(db_path).list_act_spell_timers_for_encounter(9999) == []

    def test_returns_inserted_timer(self, db_path: Path, db_conn, enc_id: int):
        upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Deathmark", timer_duration_s=30)
        rows = RaidCatalogue(db_path).list_act_spell_timers_for_encounter(enc_id)
        assert len(rows) == 1
        assert rows[0]["name"] == "Deathmark"


class TestGetActSpellTimer:
    def test_returns_none_when_path_missing(self, tmp_path: Path):
        assert RaidCatalogue(tmp_path / "no.db").get_act_spell_timer(1) is None

    def test_returns_none_for_unknown_id(self, db_path: Path):
        assert RaidCatalogue(db_path).get_act_spell_timer(9999) is None

    def test_returns_dict_for_existing_timer(self, db_path: Path, db_conn, enc_id: int):
        timer_id = upsert_act_spell_timer(
            db_conn, raid_encounter_id=enc_id, name="Arcane Distortion", timer_duration_s=60
        )
        row = RaidCatalogue(db_path).get_act_spell_timer(timer_id)
        assert row is not None
        assert row["name"] == "Arcane Distortion"
        assert row["timer_duration_s"] == 60


class TestUpsertActSpellTimer:
    def test_insert_returns_new_id(self, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Spell Alpha", timer_duration_s=10)
        assert isinstance(tid, int)
        assert tid > 0

    def test_update_returns_same_id(self, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Spell Beta", timer_duration_s=10)
        returned = upsert_act_spell_timer(
            db_conn, timer_id=tid, raid_encounter_id=enc_id, name="Spell Beta", timer_duration_s=20
        )
        assert returned == tid

    def test_stamps_edited_by(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(
            db_conn, raid_encounter_id=enc_id, name="Spell Gamma", timer_duration_s=15, edited_by="officer-1"
        )
        row = RaidCatalogue(db_path).get_act_spell_timer(tid)
        assert row["last_edited_by"] == "officer-1"

    def test_name_lower_stored_lowercase(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Camelcase Spell", timer_duration_s=5)
        row = RaidCatalogue(db_path).get_act_spell_timer(tid)
        assert row["name_lower"] == "camelcase spell"

    def test_unique_collision_raises_integrity_error(self, db_conn, enc_id: int):
        upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Unique Spell", timer_duration_s=5)
        with pytest.raises(sqlite3.IntegrityError):
            upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Unique Spell", timer_duration_s=10)


class TestDeleteActSpellTimer:
    def test_returns_true_when_deleted(self, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Del Spell", timer_duration_s=5)
        assert delete_act_spell_timer(db_conn, tid) is True

    def test_returns_false_for_unknown_id(self, db_conn):
        assert delete_act_spell_timer(db_conn, 9999) is False
