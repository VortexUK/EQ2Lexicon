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


# ---------------------------------------------------------------------------
# EQ2Parser enrichment (damage_type / control_effect / cooldown_seconds)
# ---------------------------------------------------------------------------


class TestEnrichment:
    def test_spell_timer_enrichment_round_trips(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(
            db_conn,
            raid_encounter_id=enc_id,
            name="Stench of Death",
            timer_duration_s=16,
            damage_type="poison, disease",
            control_effect="stifle",
        )
        row = RaidCatalogue(db_path).get_act_spell_timer(tid)
        assert row is not None
        assert row["damage_type"] == "poison, disease"
        assert row["control_effect"] == "stifle"

    def test_trigger_cooldown_round_trips(self, db_path: Path, db_conn, enc_id: int):
        trig_id = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="Feed, my pets!", cooldown_seconds=2.5)
        row = RaidCatalogue(db_path).get_act_trigger(trig_id)
        assert row is not None
        assert row["cooldown_seconds"] == 2.5

    def test_enrichment_defaults_are_empty(self, db_path: Path, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Plain", timer_duration_s=30)
        row = RaidCatalogue(db_path).get_act_spell_timer(tid)
        assert row is not None
        assert (row["damage_type"], row["control_effect"]) == ("", "")

    def test_init_db_migrates_a_pre_enrichment_db(self, tmp_path: Path):
        """A raids.db created before the enrichment columns existed must
        gain them on init_db (the _apply_migrations ALTERs) — the exact
        failure mode the test-migrations-against-old-DB-shape rule exists
        for."""
        p = tmp_path / "old-shape.db"
        conn = sqlite3.connect(p)
        # The pre-2026-08 table shapes, frozen: no cooldown_seconds /
        # damage_type / control_effect.
        conn.execute(
            """
            CREATE TABLE act_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_encounter_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                label TEXT, notes TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                regex TEXT NOT NULL,
                sound_data TEXT NOT NULL DEFAULT '',
                sound_type INTEGER NOT NULL DEFAULT 3,
                category_restrict INTEGER NOT NULL DEFAULT 0,
                category TEXT,
                timer INTEGER NOT NULL DEFAULT 0,
                timer_name TEXT,
                tabbed INTEGER NOT NULL DEFAULT 0,
                last_edited_at INTEGER, last_edited_by TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE act_spell_timers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raid_encounter_id INTEGER NOT NULL,
                name TEXT NOT NULL, name_lower TEXT NOT NULL,
                checked INTEGER NOT NULL DEFAULT 0,
                timer_duration_s INTEGER NOT NULL,
                only_master_ticks INTEGER NOT NULL DEFAULT 0,
                restrict INTEGER NOT NULL DEFAULT 0,
                absolute_ INTEGER NOT NULL DEFAULT 0,
                start_wav TEXT NOT NULL DEFAULT '',
                warning_wav TEXT NOT NULL DEFAULT '',
                warning_value INTEGER NOT NULL DEFAULT 10,
                radial_display INTEGER NOT NULL DEFAULT 0,
                modable INTEGER NOT NULL DEFAULT 0,
                tooltip TEXT NOT NULL DEFAULT '',
                fill_color INTEGER NOT NULL DEFAULT -16776961,
                panel1 INTEGER NOT NULL DEFAULT 1,
                panel2 INTEGER NOT NULL DEFAULT 0,
                remove_value INTEGER NOT NULL DEFAULT -15,
                category TEXT,
                restrict_category INTEGER NOT NULL DEFAULT 0,
                last_edited_at INTEGER, last_edited_by TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE (raid_encounter_id, name_lower)
            )
            """
        )
        conn.execute(
            "INSERT INTO act_spell_timers (raid_encounter_id, name, name_lower, timer_duration_s) "
            "VALUES (1, 'Old Timer', 'old timer', 30)"
        )
        conn.commit()
        conn.close()

        RaidCatalogue(p).init_db().close()

        with sqlite3.connect(p) as check:
            trig_cols = {r[1] for r in check.execute("PRAGMA table_info(act_triggers)")}
            timer_cols = {r[1] for r in check.execute("PRAGMA table_info(act_spell_timers)")}
            assert "cooldown_seconds" in trig_cols
            assert {"damage_type", "control_effect"} <= timer_cols
            # Pre-existing rows read back with the defaults.
            row = check.execute(
                "SELECT damage_type, control_effect FROM act_spell_timers WHERE name = 'Old Timer'"
            ).fetchone()
            assert row == ("", "")
        # Idempotent: a second init on the migrated file is a no-op.
        RaidCatalogue(p).init_db().close()


class TestEditorParityBackfill:
    def test_backfill_flips_editor_stripped_fields_once(self, db_path: Path, db_conn, enc_id: int):
        """Rows saved by the pre-parity web editor (modable/checked 0,
        blank sounds) flip to the real defaults exactly ONCE — a curator's
        later deliberate zero survives re-init (meta-guarded)."""
        tid = upsert_act_spell_timer(
            db_conn,
            raid_encounter_id=enc_id,
            name="Stripped",
            timer_duration_s=30,
            checked=False,
            modable=False,
            start_wav="",
            warning_wav="",
        )
        deliberate = upsert_act_spell_timer(
            db_conn,
            raid_encounter_id=enc_id,
            name="Silent By Choice",
            timer_duration_s=30,
            checked=False,
            modable=False,
            start_wav="",
            warning_wav="custom.wav",
        )
        # Clear the guard the fixture's init already set, then re-init.
        db_conn.execute("DELETE FROM _meta WHERE key = 'act_editor_parity_backfill'")
        db_conn.commit()
        RaidCatalogue(db_path).init_db().close()

        cat = RaidCatalogue(db_path)
        row = cat.get_act_spell_timer(tid)
        assert row is not None
        assert (row["modable"], row["checked"]) == (1, 1)
        assert (row["start_wav"], row["warning_wav"]) == ("tts", "tts")
        # One sound set deliberately → the pair is NOT blank-both → kept.
        partial = cat.get_act_spell_timer(deliberate)
        assert partial is not None
        assert (partial["start_wav"], partial["warning_wav"]) == ("", "custom.wav")

        # Post-backfill: a deliberate off survives the next init.
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE act_spell_timers SET modable = 0 WHERE id = ?", (tid,))
            conn.commit()
        RaidCatalogue(db_path).init_db().close()
        row = cat.get_act_spell_timer(tid)
        assert row is not None
        assert row["modable"] == 0
