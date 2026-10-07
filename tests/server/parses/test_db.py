"""Tests for the parses schema — schema invariants (migrations-owned DDL) + helpers."""

from __future__ import annotations

from datetime import datetime

import psycopg.errors
import pytest

from backend import pg_migrate
from backend.server.parses import db as parses_db
from backend.server.parses.models import AttackType, Combatant, DamageType, Encounter
from tests.fixtures.pg import pg_conn


class TestSchema:
    """The schema DDL is owned by db/migrations/0002_parses.sql — these pin
    its invariants."""

    def test_creates_all_tables(self, parses_db_path):
        with pg_conn(parses_db_path) as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (parses_db_path,),
            ).fetchall()
        tables = {r["table_name"] for r in rows}
        assert tables >= {"encounters", "combatants", "damage_types", "attack_types", "ingest_log"}

    def test_creates_indexes(self, parses_db_path):
        with pg_conn(parses_db_path) as conn:
            rows = conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s",
                (parses_db_path,),
            ).fetchall()
        indexes = {r["indexname"] for r in rows}
        assert "idx_encounters_started_desc" in indexes
        assert "idx_attack_types_damage_desc" in indexes
        assert "idx_combatants_ally" in indexes

    def test_migrations_idempotent(self):
        """Re-running the migration runner on an already-migrated database is
        a no-op (the ledger skips every applied file)."""
        assert pg_migrate.run() == []

    def test_encounters_has_hidden_at_column(self, parses_db_path):
        with pg_conn(parses_db_path) as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'encounters'",
                (parses_db_path,),
            ).fetchall()
        cols = {r["column_name"] for r in rows}
        assert "hidden_at" in cols

    def test_attack_types_unique_tuple_is_swing_scoped(self, parses_db_path):
        """The (combatant_id, swing_type,
        attack_name) tuple is part of the schema outright. The behavioural
        halves (same name across swing types allowed / same tuple collides)
        are pinned in TestUniqueConstraints."""
        with pg_conn(parses_db_path) as conn:
            rows = conn.execute(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc"
                " JOIN information_schema.key_column_usage kcu"
                "   ON kcu.constraint_name = tc.constraint_name"
                "  AND kcu.constraint_schema = tc.constraint_schema"
                " WHERE tc.table_schema = %s AND tc.table_name = 'attack_types'"
                "   AND tc.constraint_type = 'UNIQUE'"
                " ORDER BY kcu.ordinal_position",
                (parses_db_path,),
            ).fetchall()
        assert [r["column_name"] for r in rows] == ["combatant_id", "swing_type", "attack_name"]


def _sample_encounter() -> Encounter:
    return Encounter(
        encid="18cf3eb9",
        title="a krait patriarch",
        zone="Great Divide",
        started_at=datetime(2026, 5, 24, 13, 51, 56),
        ended_at=datetime(2026, 5, 24, 13, 52, 42),
        duration_s=46,
        total_damage=502718,
        encdps=10928.65,
        kills=4,
        deaths=0,
    )


def _sample_combatant(name: str, *, ally: bool, damage: int) -> Combatant:
    return Combatant(
        encid="18cf3eb9",
        name=name,
        ally=ally,
        started_at=datetime(2026, 5, 24, 13, 51, 56),
        ended_at=datetime(2026, 5, 24, 13, 52, 42),
        duration_s=46,
        damage=damage,
        damage_perc=100.0 if ally else 0.0,
        kills=4 if ally else 0,
        healed=11637 if ally else 0,
        healed_perc=100.0 if ally else 0.0,
        crit_heals=1,
        heals=40,
        cure_dispels=0,
        power_drain=0,
        power_replenish=0,
        dps=10696.13,
        encdps=10928.65,
        enchps=252.98,
        hits=132,
        crit_hits=123,
        blocked=0,
        misses=0,
        swings=132,
        heals_taken=11637,
        damage_taken=27557 if ally else 145877,
        deaths=0 if ally else 1,
        to_hit=100.0,
        crit_dam_perc=93.0,
        crit_heal_perc=3.0,
        crit_types="0.8%L - 0.0%F - 0.0%M",
        threat_str="+(0)20000/-(0)0",
        threat_delta=20000,
    )


class TestInsertHelpers:
    def test_insert_encounter_returns_id(self, parses_db_conn):
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        assert eid >= 1

    def test_insert_encounter_writes_uploaded_by(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            uploaded_by="Menludiir",
        )
        row = parses_db_conn.execute(
            "SELECT uploaded_by FROM encounters WHERE act_encid = %s",
            ("18cf3eb9",),
        ).fetchone()
        assert row["uploaded_by"] == "Menludiir"

    def test_insert_encounter_defaults_uploaded_by_to_local(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        row = parses_db_conn.execute(
            "SELECT uploaded_by FROM encounters WHERE act_encid = %s",
            ("18cf3eb9",),
        ).fetchone()
        assert row["uploaded_by"] == "local"

    def test_insert_encounter_writes_guild_name(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            uploaded_by="Menludiir",
            guild_name="Exordium",
        )
        row = parses_db_conn.execute(
            "SELECT guild_name FROM encounters WHERE act_encid = %s",
            ("18cf3eb9",),
        ).fetchone()
        assert row["guild_name"] == "Exordium"

    def test_insert_encounter_defaults_guild_to_null(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        row = parses_db_conn.execute(
            "SELECT guild_name FROM encounters WHERE act_encid = %s",
            ("18cf3eb9",),
        ).fetchone()
        assert row["guild_name"] is None

    def test_insert_combatants_writes_snapshot(self, parses_db_conn):
        from backend.server.parses.models import CombatantSnapshot

        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000)
        combatants = [
            _sample_combatant("Menludiir", ally=True, damage=500000),
            _sample_combatant("a krait patriarch", ally=False, damage=5716),
        ]
        snapshots = {"Menludiir": CombatantSnapshot(level=90, guild_name="Exordium", cls="Templar", ilvl=372.2)}
        parses_db.store.insert_combatants_bulk(parses_db_conn, eid, combatants, snapshots)

        rows = {r["name"]: r for r in parses_db.store.get_combatants_for_encounter(parses_db_conn, eid)}
        assert (
            rows["Menludiir"]["level"],
            rows["Menludiir"]["guild_name"],
            rows["Menludiir"]["cls"],
            rows["Menludiir"]["ilvl"],
        ) == (90, "Exordium", "Templar", 372.2)
        # Unresolved combatant (no snapshot) stores NULLs.
        assert rows["a krait patriarch"]["level"] is None
        assert rows["a krait patriarch"]["guild_name"] is None
        assert rows["a krait patriarch"]["ilvl"] is None

    def test_insert_combatants_snapshot_optional(self, parses_db_conn):
        # Back-compat: omitting snapshots leaves the columns NULL.
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000)
        parses_db.store.insert_combatants_bulk(parses_db_conn, eid, [_sample_combatant("Solo", ally=True, damage=1)])
        row = parses_db.store.get_combatants_for_encounter(parses_db_conn, eid)[0]
        assert row["level"] is None and row["cls"] is None

    def test_update_combatant_snapshots_fills_rows(self, parses_db_conn):
        from backend.server.parses.models import CombatantSnapshot

        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000)
        parses_db.store.insert_combatants_bulk(
            parses_db_conn, eid, [_sample_combatant("Menludiir", ally=True, damage=1)]
        )
        n = parses_db.store.update_combatant_snapshots(
            parses_db_conn,
            eid,
            {"Menludiir": CombatantSnapshot(level=90, guild_name="Exordium", cls="Templar", ilvl=372.2)},
        )
        assert n == 1
        row = next(
            c for c in parses_db.store.get_combatants_for_encounter(parses_db_conn, eid) if c["name"] == "Menludiir"
        )
        assert (row["level"], row["guild_name"], row["cls"], row["ilvl"]) == (90, "Exordium", "Templar", 372.2)

    def test_soft_delete_sets_hidden_at_and_by(self, parses_db_conn):
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000)
        assert (
            parses_db.store.soft_delete_encounter(parses_db_conn, eid, hidden_at=1700001111, hidden_by="admin-1")
            is True
        )
        row = parses_db_conn.execute("SELECT hidden_at, hidden_by FROM encounters WHERE id = %s", (eid,)).fetchone()
        assert (row["hidden_at"], row["hidden_by"]) == (1700001111, "admin-1")
        # Idempotent: re-soft-deleting an already-hidden row is a no-op (returns False)
        # and never overwrites the original actor.
        assert (
            parses_db.store.soft_delete_encounter(parses_db_conn, eid, hidden_at=1700002222, hidden_by="admin-2")
            is False
        )
        row = parses_db_conn.execute("SELECT hidden_by FROM encounters WHERE id = %s", (eid,)).fetchone()
        assert row["hidden_by"] == "admin-1"

    def test_unhide_encounter_clears_marker(self, parses_db_conn):
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000)
        parses_db.store.soft_delete_encounter(parses_db_conn, eid, hidden_at=1700001111, hidden_by="admin-1")
        assert parses_db.store.unhide_encounter(parses_db_conn, eid) is True
        row = parses_db_conn.execute("SELECT hidden_at, hidden_by FROM encounters WHERE id = %s", (eid,)).fetchone()
        assert (row["hidden_at"], row["hidden_by"]) == (None, None)
        # Already-visible row → no-op, returns False.
        assert parses_db.store.unhide_encounter(parses_db_conn, eid) is False

    def test_full_ingest_chain(self, parses_db_conn):
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            enc,
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        combatants = [
            _sample_combatant("Menludiir", ally=True, damage=502718),
            _sample_combatant("a krait patriarch", ally=False, damage=5716),
        ]
        name_to_id = parses_db.store.insert_combatants_bulk(parses_db_conn, eid, combatants)
        assert set(name_to_id) == {"Menludiir", "a krait patriarch"}

        damage_types = [
            DamageType(
                encid=enc.encid,
                combatant_name="Menludiir",
                grouping_label="Group 1",
                damage_type="divine",
                started_at=datetime(2026, 5, 24, 13, 51, 56),
                ended_at=datetime(2026, 5, 24, 13, 52, 42),
                duration_s=46,
                damage=400000,
                encdps=8000.0,
                char_dps=8000.0,
                dps=8500.0,
                average=3030.0,
                median=3000,
                min_hit=100,
                max_hit=8000,
                hits=100,
                crit_hits=90,
                blocked=0,
                misses=0,
                swings=100,
                to_hit=100.0,
                average_delay=0.47,
                crit_perc=90.0,
                crit_types="0.8%L - 0.0%F - 0.0%M",
            ),
        ]
        n = parses_db.store.insert_damage_types_bulk(parses_db_conn, name_to_id, damage_types)
        assert n == 1

        attacks = [
            AttackType(
                encid=enc.encid,
                combatant_name="Menludiir",
                victim="a krait patriarch",
                swing_type=1,
                attack_name="Smite",
                started_at=datetime(2026, 5, 24, 13, 51, 56),
                ended_at=datetime(2026, 5, 24, 13, 52, 42),
                duration_s=46,
                damage=400000,
                encdps=8000.0,
                char_dps=8500.0,
                dps=8500.0,
                average=4000.0,
                median=3500,
                min_hit=100,
                max_hit=8000,
                resist="divine",
                hits=100,
                crit_hits=90,
                blocked=0,
                misses=0,
                swings=100,
                to_hit=100.0,
                average_delay=0.47,
                crit_perc=90.0,
                crit_types="0.8%L - 0.0%F - 0.0%M",
            ),
        ]
        n = parses_db.store.insert_attack_types_bulk(parses_db_conn, name_to_id, attacks)
        assert n == 1

        parses_db.store.mark_ingested(
            parses_db_conn,
            enc.encid,
            eid,
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        assert parses_db.store.is_ingested(parses_db_conn, enc.encid)
        assert not parses_db.store.is_ingested(parses_db_conn, "NOTREAL")

    def test_list_encounters_for_admin_includes_hidden_and_search(self, parses_db_conn):
        from dataclasses import replace

        a = _sample_encounter()  # title "a krait patriarch", encid "18cf3eb9"
        b = replace(a, encid="boss01", title="Wuoshi")
        aid = parses_db.store.insert_encounter(
            parses_db_conn, a, source_dsn="eq2act", ingested_at=1, guild_name="Exordium"
        )
        bid = parses_db.store.insert_encounter(
            parses_db_conn, b, source_dsn="eq2act", ingested_at=2, guild_name="Exordium"
        )
        parses_db.store.soft_delete_encounter(parses_db_conn, bid, hidden_at=99)  # hide the boss one

        rows = parses_db.store.list_encounters_for_admin(parses_db_conn)
        ids = {r["id"] for r in rows}
        assert aid in ids and bid in ids  # hidden row still listed for admin
        by_id = {r["id"]: r for r in rows}
        assert by_id[bid]["hidden_at"] == 99 and by_id[aid]["hidden_at"] is None
        assert "player_count" in by_id[aid]

        # Search narrows by title.
        hits = parses_db.store.list_encounters_for_admin(parses_db_conn, search="wuoshi")
        assert [r["id"] for r in hits] == [bid]

        # hidden_only narrows to the soft-deleted rows (the restore workflow).
        hidden = parses_db.store.list_encounters_for_admin(parses_db_conn, hidden_only=True)
        assert [r["id"] for r in hidden] == [bid]

    def test_list_encounters_for_admin_search_by_id(self, parses_db_conn):
        """A numeric search (with or without the UI's '#' prefix) matches the
        encounter id exactly — so an admin can jump from a parse URL straight
        to the row. Non-numeric search never touches the id clause."""
        from dataclasses import replace

        a = _sample_encounter()
        b = replace(a, encid="boss01", title="Wuoshi")
        aid = parses_db.store.insert_encounter(parses_db_conn, a, source_dsn="eq2act", ingested_at=1)
        parses_db.store.insert_encounter(parses_db_conn, b, source_dsn="eq2act", ingested_at=2)

        for term in (str(aid), f"#{aid}", f"  #{aid} "):
            hits = parses_db.store.list_encounters_for_admin(parses_db_conn, search=term)
            assert [r["id"] for r in hits] == [aid], f"search {term!r}"

        # A numeric term that is nobody's id (and matches no text) finds nothing.
        assert parses_db.store.list_encounters_for_admin(parses_db_conn, search="999999") == []


class TestUniqueConstraints:
    def test_duplicate_act_encid_rejected(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            parses_db.store.insert_encounter(
                parses_db_conn,
                _sample_encounter(),
                source_dsn="eq2act",
                ingested_at=1700000001,
            )

    def test_duplicate_combatant_in_encounter_rejected(self, parses_db_conn):
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            enc,
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        cs = [_sample_combatant("Menludiir", ally=True, damage=1)]
        parses_db.store.insert_combatants_bulk(parses_db_conn, eid, cs)
        with pytest.raises(psycopg.errors.UniqueViolation):
            parses_db.store.insert_combatants_bulk(parses_db_conn, eid, cs)

    def test_same_attack_name_across_swing_types_allowed(self, parses_db_conn):
        """Cleanse-style spells deal damage (swing_type=2) AND heal (swing_type=3)
        — both rows must coexist for the same combatant. Old UNIQUE
        constraint of (combatant_id, attack_name) blocked this."""
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        parses_db.store.insert_combatants_bulk(
            parses_db_conn, eid, [_sample_combatant("Menludiir", ally=True, damage=1)]
        )
        cid = parses_db_conn.execute(
            "SELECT id FROM combatants WHERE encounter_id = %s AND name = %s",
            (eid, "Menludiir"),
        ).fetchone()["id"]
        parses_db_conn.executemany(
            "INSERT INTO attack_types (combatant_id, victim, swing_type, attack_name, "
            "damage, hits, swings, crit_hits, max_hit, resist) "
            "VALUES (%s, '', %s, 'Cleanse', %s, 1, 1, 0, 0, '')",
            [(cid, 2, 1000), (cid, 3, 500)],
        )
        rows = parses_db_conn.execute(
            "SELECT swing_type, damage FROM attack_types "
            "WHERE combatant_id = %s AND attack_name = 'Cleanse' ORDER BY swing_type",
            (cid,),
        ).fetchall()
        assert [(r["swing_type"], r["damage"]) for r in rows] == [(2, 1000), (3, 500)]

    def test_duplicate_attack_within_same_swing_type_rejected(self, parses_db_conn):
        """The new tuple is (combatant_id, swing_type, attack_name) — same
        attack twice at the same swing type still collides."""
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        parses_db.store.insert_combatants_bulk(
            parses_db_conn, eid, [_sample_combatant("Menludiir", ally=True, damage=1)]
        )
        cid = parses_db_conn.execute(
            "SELECT id FROM combatants WHERE encounter_id = %s AND name = %s",
            (eid, "Menludiir"),
        ).fetchone()["id"]
        parses_db_conn.execute(
            "INSERT INTO attack_types (combatant_id, victim, swing_type, attack_name, "
            "damage, hits, swings, crit_hits, max_hit, resist) "
            "VALUES (%s, '', 2, 'Cleanse', 1000, 1, 1, 0, 0, '')",
            (cid,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            parses_db_conn.execute(
                "INSERT INTO attack_types (combatant_id, victim, swing_type, attack_name, "
                "damage, hits, swings, crit_hits, max_hit, resist) "
                "VALUES (%s, '', 2, 'Cleanse', 2000, 1, 1, 0, 0, '')",
                (cid,),
            )


class TestLookupHelpers:
    def test_recent_encounters_orders_by_started_desc(self, parses_db_conn):
        e1 = _sample_encounter()
        e2 = Encounter(
            encid="2B3C4D5E",
            title="a goblin shaman",
            zone="Antonica",
            started_at=datetime(2026, 5, 24, 14, 5, 0),
            ended_at=datetime(2026, 5, 24, 14, 5, 30),
            duration_s=30,
            total_damage=20000,
            encdps=666.66,
            kills=1,
            deaths=0,
        )
        parses_db.store.insert_encounter(parses_db_conn, e1, source_dsn="eq2act", ingested_at=1)
        parses_db.store.insert_encounter(parses_db_conn, e2, source_dsn="eq2act", ingested_at=2)
        rows = parses_db.store.recent_encounters(parses_db_conn, limit=10)
        assert [r["act_encid"] for r in rows] == ["2B3C4D5E", "18cf3eb9"]

    def test_recent_encounters_zone_filter(self, parses_db_conn):
        e1 = _sample_encounter()
        e2 = Encounter(
            encid="2B3C4D5E",
            title="b",
            zone="Commonlands",
            started_at=datetime(2026, 5, 24, 15, 0, 0),
            ended_at=datetime(2026, 5, 24, 15, 0, 30),
            duration_s=30,
            total_damage=1,
            encdps=1,
            kills=0,
            deaths=0,
        )
        parses_db.store.insert_encounter(parses_db_conn, e1, source_dsn="eq2act", ingested_at=1)
        parses_db.store.insert_encounter(parses_db_conn, e2, source_dsn="eq2act", ingested_at=2)
        rows = parses_db.store.recent_encounters(parses_db_conn, zone="Great Divide")
        assert [r["act_encid"] for r in rows] == ["18cf3eb9"]

    def test_find_encounter_by_act_encid(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        row = parses_db.store.find_encounter_by_act_encid(parses_db_conn, "18cf3eb9")
        assert row is not None
        assert row["title"] == "a krait patriarch"

    def test_find_encounter_missing_returns_none(self, parses_db_conn):
        assert parses_db.store.find_encounter_by_act_encid(parses_db_conn, "NOPE") is None


class TestSwingTypeSplit:
    """get_top_attacks vs get_top_heals must partition attack_types rows by
    swing_type (1/2 = damage, 3 = heal) — heal rows would otherwise leak into
    the Damage tab and out-rank damage abilities for support classes."""

    def _seed(self, parses_db_conn):
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            enc,
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        name_to_id = parses_db.store.insert_combatants_bulk(
            parses_db_conn,
            eid,
            [_sample_combatant("Menludiir", ally=True, damage=10000)],
        )
        cid = name_to_id["Menludiir"]
        # Insert rows directly to control swing_type per row.
        parses_db_conn.executemany(
            """
            INSERT INTO attack_types (
                combatant_id, victim, swing_type, attack_name,
                damage, hits, swings, crit_hits, max_hit, resist
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (cid, "", 1, "crush", 7000, 10, 10, 1, 1500, "crushing"),
                (cid, "", 2, "Smite", 5000, 5, 5, 2, 2000, "divine"),
                (cid, "", 3, "Reverence", 8000, 12, 12, 0, 1297, "Hitpoints"),
                (cid, "", 3, "Stonewill", 3000, 8, 8, 0, 700, "Absorption"),
                # Cure (swing_type=20, resist='relieves'; `damage` column = effects removed)
                (cid, "", 20, "Cure", 4, 4, 4, 0, 1, "relieves"),
                # Threat proc (swing_type=100, type != 'All')
                (cid, "", 100, "Undeniable Malice", 27240, 10, 10, 0, 5000, "Increase"),
            ],
        )
        return cid

    def test_top_attacks_excludes_heals_and_rollups(self, parses_db_conn):
        cid = self._seed(parses_db_conn)
        attacks = parses_db.store.get_top_attacks_for_combatant(parses_db_conn, cid)
        names = [a["attack_name"] for a in attacks]
        assert names == ["crush", "Smite"]  # heals + rollup absent

    def test_top_heals_only_swing_type_3(self, parses_db_conn):
        cid = self._seed(parses_db_conn)
        heals = parses_db.store.get_top_heals_for_combatant(parses_db_conn, cid)
        names = [h["attack_name"] for h in heals]
        # Sorted by damage DESC
        assert names == ["Reverence", "Stonewill"]
        rev = next(h for h in heals if h["attack_name"] == "Reverence")
        assert rev["resist"] == "Hitpoints"
        sw = next(h for h in heals if h["attack_name"] == "Stonewill")
        assert sw["resist"] == "Absorption"

    def test_top_cures_only_swing_type_20(self, parses_db_conn):
        cid = self._seed(parses_db_conn)
        cures = parses_db.store.get_top_cures_for_combatant(parses_db_conn, cid)
        assert [c["attack_name"] for c in cures] == ["Cure"]
        assert cures[0]["resist"] == "relieves"

    def test_top_threats_excludes_All_rollup(self, parses_db_conn):
        """swing_type=100 + type='All' must NOT be returned, but
        swing_type=100 + type != 'All' (Undeniable Malice) must be."""
        cid = self._seed(parses_db_conn)
        # Add an aggregate that we expect to be filtered.
        parses_db_conn.execute(
            "INSERT INTO attack_types (combatant_id, victim, swing_type, attack_name, "
            "damage, hits, swings, crit_hits, max_hit, resist) "
            "VALUES (%s, '', 100, 'All', 999999, 999, 999, 0, 0, 'All')",
            (cid,),
        )
        threats = parses_db.store.get_top_threats_for_combatant(parses_db_conn, cid)
        names = [t["attack_name"] for t in threats]
        assert names == ["Undeniable Malice"]
        assert threats[0]["resist"] == "Increase"

    def test_no_heals_returns_empty(self, parses_db_conn):
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(
            parses_db_conn,
            enc,
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        name_to_id = parses_db.store.insert_combatants_bulk(
            parses_db_conn,
            eid,
            [_sample_combatant("Sihtric", ally=True, damage=5000)],
        )
        assert parses_db.store.get_top_heals_for_combatant(parses_db_conn, name_to_id["Sihtric"]) == []


class TestDeleteHelpers:
    def _seed(self, conn, *, encid: str, guild_name: str, uploaded_by: str = "Menludiir") -> int:
        from dataclasses import replace

        enc = replace(_sample_encounter(), encid=encid)
        eid = parses_db.store.insert_encounter(
            conn,
            enc,
            source_dsn="eq2act",
            ingested_at=1700000000,
            uploaded_by=uploaded_by,
            guild_name=guild_name,
        )
        parses_db.store.insert_combatants_bulk(
            conn,
            eid,
            [_sample_combatant("Menludiir", ally=True, damage=1000)],
        )
        parses_db.store.mark_ingested(conn, encid, eid, source_dsn="eq2act", ingested_at=1700000000)
        return eid

    def test_delete_encounter_removes_row(self, parses_db_conn):
        eid = self._seed(parses_db_conn, encid="enc1", guild_name="Exordium")
        assert parses_db.store.delete_encounter(parses_db_conn, eid) is True
        assert parses_db_conn.execute("SELECT COUNT(*) AS n FROM encounters").fetchone()["n"] == 0

    def test_delete_encounter_returns_false_when_missing(self, parses_db_conn):
        assert parses_db.store.delete_encounter(parses_db_conn, 99999) is False

    def test_delete_encounter_cascades_children(self, parses_db_conn):
        eid = self._seed(parses_db_conn, encid="enc2", guild_name="Exordium")
        # Sanity: rows exist before delete.
        assert (
            parses_db_conn.execute("SELECT COUNT(*) AS n FROM combatants WHERE encounter_id = %s", (eid,)).fetchone()[
                "n"
            ]
            > 0
        )
        assert (
            parses_db_conn.execute("SELECT COUNT(*) AS n FROM ingest_log WHERE encounter_id = %s", (eid,)).fetchone()[
                "n"
            ]
            == 1
        )
        parses_db.store.delete_encounter(parses_db_conn, eid)
        # All children gone via FK cascade.
        assert (
            parses_db_conn.execute("SELECT COUNT(*) AS n FROM combatants WHERE encounter_id = %s", (eid,)).fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            parses_db_conn.execute("SELECT COUNT(*) AS n FROM ingest_log WHERE encounter_id = %s", (eid,)).fetchone()[
                "n"
            ]
            == 0
        )


# ---------------------------------------------------------------------------
# Per-server world scoping: (world, act_encid) uniqueness
# ---------------------------------------------------------------------------


class TestWorldScoping:
    """Verify that world is stored, uniqueness is per (world, act_encid),
    and all lookup helpers honour the world filter."""

    def test_encounters_has_world_column(self, parses_db_path):
        with pg_conn(parses_db_path) as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'encounters'",
                (parses_db_path,),
            ).fetchall()
        assert "world" in {r["column_name"] for r in rows}

    def test_ingest_log_has_world_column(self, parses_db_path):
        with pg_conn(parses_db_path) as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'ingest_log'",
                (parses_db_path,),
            ).fetchall()
        assert "world" in {r["column_name"] for r in rows}

    def test_world_stored_on_encounter(self, parses_db_conn):
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Wuoshi",
        )
        row = parses_db_conn.execute("SELECT world FROM encounters WHERE act_encid = %s", ("18cf3eb9",)).fetchone()
        assert row["world"] == "Wuoshi"

    def test_same_act_encid_different_world_both_insert(self, parses_db_conn):
        """The UNIQUE constraint is (world, act_encid), so the same encid
        from two different servers must coexist without a collision."""
        eid_v = parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Varsoon",
        )
        eid_w = parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000001,
            world="Wuoshi",
        )
        assert eid_v != eid_w
        count = parses_db_conn.execute(
            "SELECT COUNT(*) AS n FROM encounters WHERE act_encid = %s", ("18cf3eb9",)
        ).fetchone()["n"]
        assert count == 2

    def test_same_world_same_encid_still_collides(self, parses_db_conn):
        """Duplicate (world, act_encid) within the same server must still
        raise a UniqueViolation."""
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Varsoon",
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            parses_db.store.insert_encounter(
                parses_db_conn,
                _sample_encounter(),
                source_dsn="eq2act",
                ingested_at=1700000001,
                world="Varsoon",
            )

    def test_is_ingested_world_scoped(self, parses_db_conn):
        """is_ingested(world='Varsoon') must NOT report True just because
        the same encid is ingested under 'Wuoshi'."""
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(
            parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000, world="Wuoshi"
        )
        parses_db.store.mark_ingested(
            parses_db_conn,
            enc.encid,
            eid,
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Wuoshi",
        )
        # On the ingest world → True.
        assert parses_db.store.is_ingested(parses_db_conn, enc.encid, "Wuoshi") is True
        # On a different world → False.
        assert parses_db.store.is_ingested(parses_db_conn, enc.encid, "Varsoon") is False

    def test_ingest_log_world_stored(self, parses_db_conn):
        """mark_ingested must write the world column into ingest_log."""
        enc = _sample_encounter()
        eid = parses_db.store.insert_encounter(
            parses_db_conn, enc, source_dsn="eq2act", ingested_at=1700000000, world="Kaladim"
        )
        parses_db.store.mark_ingested(
            parses_db_conn,
            enc.encid,
            eid,
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Kaladim",
        )
        row = parses_db_conn.execute("SELECT world FROM ingest_log WHERE act_encid = %s", (enc.encid,)).fetchone()
        assert row["world"] == "Kaladim"

    def test_find_encounter_by_act_encid_world_scoped(self, parses_db_conn):
        """find_encounter_by_act_encid must only return the row for the
        requested world."""
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Varsoon",
        )
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000001,
            world="Wuoshi",
        )
        v_row = parses_db.store.find_encounter_by_act_encid(parses_db_conn, "18cf3eb9", "Varsoon")
        w_row = parses_db.store.find_encounter_by_act_encid(parses_db_conn, "18cf3eb9", "Wuoshi")
        assert v_row is not None and v_row["world"] == "Varsoon"
        assert w_row is not None and w_row["world"] == "Wuoshi"
        assert v_row["id"] != w_row["id"]

    def test_recent_encounters_world_filter(self, parses_db_conn):
        """recent_encounters(world='Varsoon') must exclude Wuoshi rows."""
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
            world="Varsoon",
        )
        from dataclasses import replace

        enc2 = replace(_sample_encounter(), encid="WUOSHI01")
        parses_db.store.insert_encounter(
            parses_db_conn,
            enc2,
            source_dsn="eq2act",
            ingested_at=1700000001,
            world="Wuoshi",
        )
        v_rows = parses_db.store.recent_encounters(parses_db_conn, world="Varsoon")
        assert [r["act_encid"] for r in v_rows] == ["18cf3eb9"]
        w_rows = parses_db.store.recent_encounters(parses_db_conn, world="Wuoshi")
        assert [r["act_encid"] for r in w_rows] == ["WUOSHI01"]

    def test_encounters_default_world_is_varsoon(self, parses_db_conn):
        """insert_encounter with no explicit world stores 'Varsoon'."""
        parses_db.store.insert_encounter(
            parses_db_conn,
            _sample_encounter(),
            source_dsn="eq2act",
            ingested_at=1700000000,
        )
        row = parses_db_conn.execute("SELECT world FROM encounters WHERE act_encid = %s", ("18cf3eb9",)).fetchone()
        assert row["world"] == "Varsoon"

    def test_world_defaults_to_varsoon_at_the_schema_level(self, parses_db_conn):
        """The DEFAULT lives
        in the migrations-owned DDL outright — a raw INSERT that omits the
        world column must land as 'Varsoon' in both encounters and
        ingest_log.
        """
        row = parses_db_conn.execute(
            "INSERT INTO encounters (act_encid, title, zone, started_at, ended_at, "
            "duration_s, total_damage, encdps, kills, deaths, source_dsn, ingested_at) "
            "VALUES ('legacy01', 'OldBoss', 'OldZone', 0, 0, 30, 1000, 33.3, 1, 0, 'eq2act', 0) "
            "RETURNING id, world"
        ).fetchone()
        assert row["world"] == "Varsoon"
        parses_db_conn.execute(
            "INSERT INTO ingest_log (act_encid, encounter_id, ingested_at, source_dsn) "
            "VALUES ('legacy01', %s, 0, 'eq2act')",
            (row["id"],),
        )
        log = parses_db_conn.execute("SELECT world FROM ingest_log WHERE act_encid = 'legacy01'").fetchone()
        assert log["world"] == "Varsoon"
