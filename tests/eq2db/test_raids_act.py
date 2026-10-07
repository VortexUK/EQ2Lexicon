"""Tests for the ACT trigger/spell-timer helpers on the raids catalogue — COV-010.

Postgres edition: each test leases an isolated scratch raids schema (the
``raids_schema`` fixture) with one seeded zone + encounter. Covers:
- list_act_triggers_for_encounter ordering + unprovisioned-schema contract
- get_act_trigger unknown id → None
- upsert_act_trigger INSERT vs UPDATE + edited_by stamp
- delete_act_trigger returns True/False
- Spell-timer helpers (same shape)
- upsert_act_spell_timer name_lower UNIQUE collision
- the migrations-owned enrichment DDL (no runtime ALTER/backfill path)
"""

from __future__ import annotations

import psycopg.errors
import pytest

from backend.eq2db.raids import RaidCatalogue
from tests.fixtures.pg import pg_conn

# Conn-taking write helpers are staticmethods — alias for readable call sites.
upsert_act_trigger = RaidCatalogue.upsert_act_trigger
delete_act_trigger = RaidCatalogue.delete_act_trigger
upsert_act_spell_timer = RaidCatalogue.upsert_act_spell_timer
delete_act_spell_timer = RaidCatalogue.delete_act_spell_timer


@pytest.fixture
def db(raids_schema: str) -> str:
    """A fresh scratch raids schema with one zone + encounter seeded —
    the analog of the old tmp_path the raids catalogue fixture. Returns the schema name."""
    with pg_conn(raids_schema) as conn:
        zone_id = conn.execute(
            "INSERT INTO raid_zones (zone_name, zone_name_lower, expansion_short, source) "
            "VALUES ('Test Zone', 'test zone', 'TS', 'manual') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO raid_encounters (raid_zone_id, mob_name, mob_name_lower, source) "
            "VALUES (%s, 'Boss One', 'boss one', 'manual')",
            (zone_id,),
        )
    return raids_schema


@pytest.fixture
def enc_id(db: str) -> int:
    with pg_conn(db) as conn:
        row = conn.execute("SELECT id FROM raid_encounters LIMIT 1").fetchone()
    return row["id"]


@pytest.fixture
def db_conn(db: str):
    conn = RaidCatalogue(db).init_db()
    yield conn
    conn.close()


#: An unprovisioned schema name — the Pg analog of the old "missing path".
#: SchemaBound documents no exists-degrade: migrations run before the app
#: serves, so a missing relation is a real fault (raises), never a soft
#: empty result.
_MISSING = "raids_pytest_never_provisioned"


# ---------------------------------------------------------------------------
# ACT Trigger helpers
# ---------------------------------------------------------------------------


class TestListActTriggersForEncounter:
    def test_raises_on_unprovisioned_schema(self):
        with pytest.raises(psycopg.errors.UndefinedTable):
            RaidCatalogue(_MISSING).list_act_triggers_for_encounter(1)

    def test_returns_empty_for_unknown_encounter(self, db: str):
        assert RaidCatalogue(db).list_act_triggers_for_encounter(9999) == []

    def test_ordering_by_position_then_id(self, db: str, db_conn, enc_id: int):
        # Insert triggers with shuffled positions
        upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="c", position=2)
        upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="a", position=0)
        upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="b", position=1)
        rows = RaidCatalogue(db).list_act_triggers_for_encounter(enc_id)
        assert len(rows) == 3
        assert rows[0]["regex"] == "a"
        assert rows[1]["regex"] == "b"
        assert rows[2]["regex"] == "c"


class TestGetActTrigger:
    def test_raises_on_unprovisioned_schema(self):
        with pytest.raises(psycopg.errors.UndefinedTable):
            RaidCatalogue(_MISSING).get_act_trigger(1)

    def test_returns_none_for_unknown_id(self, db: str):
        assert RaidCatalogue(db).get_act_trigger(9999) is None

    def test_returns_dict_for_existing_trigger(self, db: str, db_conn, enc_id: int):
        trigger_id = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="test-regex", label="Boss Pull")
        row = RaidCatalogue(db).get_act_trigger(trigger_id)
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

    def test_stamps_edited_by(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="x", edited_by="user-123")
        row = RaidCatalogue(db).get_act_trigger(tid)
        assert row["last_edited_by"] == "user-123"

    def test_stamps_last_edited_at(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="x")
        row = RaidCatalogue(db).get_act_trigger(tid)
        assert row["last_edited_at"] is not None and row["last_edited_at"] > 0


class TestDeleteActTrigger:
    def test_returns_true_when_deleted(self, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="to-delete")
        assert delete_act_trigger(db_conn, tid) is True

    def test_returns_false_for_unknown_id(self, db_conn):
        assert delete_act_trigger(db_conn, 9999) is False

    def test_row_gone_after_delete(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="gone")
        delete_act_trigger(db_conn, tid)
        assert RaidCatalogue(db).get_act_trigger(tid) is None


# ---------------------------------------------------------------------------
# ACT Spell Timer helpers
# ---------------------------------------------------------------------------


class TestListActSpellTimersForEncounter:
    def test_raises_on_unprovisioned_schema(self):
        with pytest.raises(psycopg.errors.UndefinedTable):
            RaidCatalogue(_MISSING).list_act_spell_timers_for_encounter(1)

    def test_returns_empty_for_unknown_encounter(self, db: str):
        assert RaidCatalogue(db).list_act_spell_timers_for_encounter(9999) == []

    def test_returns_inserted_timer(self, db: str, db_conn, enc_id: int):
        upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Deathmark", timer_duration_s=30)
        rows = RaidCatalogue(db).list_act_spell_timers_for_encounter(enc_id)
        assert len(rows) == 1
        assert rows[0]["name"] == "Deathmark"


class TestGetActSpellTimer:
    def test_raises_on_unprovisioned_schema(self):
        with pytest.raises(psycopg.errors.UndefinedTable):
            RaidCatalogue(_MISSING).get_act_spell_timer(1)

    def test_returns_none_for_unknown_id(self, db: str):
        assert RaidCatalogue(db).get_act_spell_timer(9999) is None

    def test_returns_dict_for_existing_timer(self, db: str, db_conn, enc_id: int):
        timer_id = upsert_act_spell_timer(
            db_conn, raid_encounter_id=enc_id, name="Arcane Distortion", timer_duration_s=60
        )
        row = RaidCatalogue(db).get_act_spell_timer(timer_id)
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

    def test_stamps_edited_by(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(
            db_conn, raid_encounter_id=enc_id, name="Spell Gamma", timer_duration_s=15, edited_by="officer-1"
        )
        row = RaidCatalogue(db).get_act_spell_timer(tid)
        assert row["last_edited_by"] == "officer-1"

    def test_name_lower_stored_lowercase(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Camelcase Spell", timer_duration_s=5)
        row = RaidCatalogue(db).get_act_spell_timer(tid)
        assert row["name_lower"] == "camelcase spell"

    def test_unique_collision_raises_integrity_error(self, db_conn, enc_id: int):
        upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Unique Spell", timer_duration_s=5)
        with pytest.raises(psycopg.errors.UniqueViolation):
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
    def test_spell_timer_enrichment_round_trips(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(
            db_conn,
            raid_encounter_id=enc_id,
            name="Stench of Death",
            timer_duration_s=16,
            damage_type="poison, disease",
            control_effect="stifle",
        )
        row = RaidCatalogue(db).get_act_spell_timer(tid)
        assert row is not None
        assert row["damage_type"] == "poison, disease"
        assert row["control_effect"] == "stifle"

    def test_trigger_cooldown_round_trips(self, db: str, db_conn, enc_id: int):
        trig_id = upsert_act_trigger(db_conn, raid_encounter_id=enc_id, regex="Feed, my pets!", cooldown_seconds=2.5)
        row = RaidCatalogue(db).get_act_trigger(trig_id)
        assert row is not None
        assert row["cooldown_seconds"] == 2.5

    def test_enrichment_defaults_are_empty(self, db: str, db_conn, enc_id: int):
        tid = upsert_act_spell_timer(db_conn, raid_encounter_id=enc_id, name="Plain", timer_duration_s=30)
        row = RaidCatalogue(db).get_act_spell_timer(tid)
        assert row is not None
        assert (row["damage_type"], row["control_effect"]) == ("", "")

    def test_enrichment_columns_in_migrations_owned_ddl(self, db: str, enc_id: int):
        """The enrichment columns are
        part of the migrations-owned DDL outright (db/migrations/
        0005_raids.sql). A raw INSERT that omits them — the shape a
        pre-enrichment writer produced — reads back the defaults the old
        ALTER backfilled."""
        with pg_conn(db) as conn:
            trig_cols = {
                r["column_name"]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = %s AND table_name = 'act_triggers'",
                    (db,),
                )
            }
            timer_cols = {
                r["column_name"]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = %s AND table_name = 'act_spell_timers'",
                    (db,),
                )
            }
            assert "cooldown_seconds" in trig_cols
            assert {"damage_type", "control_effect"} <= timer_cols

            # Pre-enrichment-shaped writes get the defaults.
            timer_row = conn.execute(
                "INSERT INTO act_spell_timers (raid_encounter_id, name, name_lower, timer_duration_s) "
                "VALUES (%s, 'Old Timer', 'old timer', 30) "
                "RETURNING damage_type, control_effect",
                (enc_id,),
            ).fetchone()
            assert (timer_row["damage_type"], timer_row["control_effect"]) == ("", "")
            trig_row = conn.execute(
                "INSERT INTO act_triggers (raid_encounter_id, regex) VALUES (%s, 'x') RETURNING cooldown_seconds",
                (enc_id,),
            ).fetchone()
            assert trig_row["cooldown_seconds"] == 1.0


class TestEditorParityBackfill:
    def test_init_db_never_mutates_rows(self, db: str, db_conn, enc_id: int):
        """``PgCatalogue.init_db()`` is a pooled-connection
        handle that never rewrites rows. The surviving contract is the
        second half of the old test's intent: a curator's deliberate
        zeros/blanks survive any number of re-inits verbatim."""
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
        # Re-init twice — on Pg this opens (and closes) pooled connections
        # and nothing else: no normalization, no backfill, no meta guard.
        RaidCatalogue(db).init_db().close()
        RaidCatalogue(db).init_db().close()

        cat = RaidCatalogue(db)
        row = cat.get_act_spell_timer(tid)
        assert row is not None
        assert (row["modable"], row["checked"]) == (0, 0)
        assert (row["start_wav"], row["warning_wav"]) == ("", "")
        partial = cat.get_act_spell_timer(deliberate)
        assert partial is not None
        assert (partial["start_wav"], partial["warning_wav"]) == ("", "custom.wav")

        # A later deliberate zero also survives the next init.
        with pg_conn(db) as conn:
            conn.execute("UPDATE act_spell_timers SET modable = 0 WHERE id = %s", (tid,))
        RaidCatalogue(db).init_db().close()
        row = cat.get_act_spell_timer(tid)
        assert row is not None
        assert row["modable"] == 0
