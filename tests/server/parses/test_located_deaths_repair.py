"""The located-deaths data repair (db/migrations/0028_parses_located_deaths.sql)
and its undo (scripts/dev/undo_located_deaths.sql).

Both files are run as they ship: the migration retargeted at a leased scratch
schema (it already ran once there, on empty tables, when the schema was
built), the undo with its search_path line pointed at the same schema.
Encounters and combatants are seeded with raw SQL so each test controls
exactly what the repair reads. Every name is invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend import pg, pg_migrate
from backend.server.parses import db as parses_db
from backend.server.parses import fights

MIGRATION = pg_migrate.MIGRATIONS_DIR / "0028_parses_located_deaths.sql"
UNDO = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "undo_located_deaths.sql"
_UNDO_SEARCH_PATH = "set search_path to parses, public;"

WORLD = "Varsoon"
BASE = 1_800_000_000
ZONE = "The Protector's Realm"
BOSS = "Doomcoil"
PLAYERS = ("Alpha", "Bravo", "Charlie")

_DATA_TABLES = ("encounters", "combatants", "attack_types", "damage_types", "fights")
_RECORD_TABLES = ("located_death_encounters", "located_death_rows", "located_death_children")


@pytest.fixture
def schema(parses_db_path: str) -> str:
    return parses_db_path


@pytest.fixture
def conn(schema: str):
    c = parses_db.ParsesStore(schema).init_db()
    yield c
    c.close()


def located(name: str, x: float = 29.08, zone: str = ZONE) -> str:
    return f"{name} in {zone} ({x}, -10.09, 298.89)"


def run_repair(schema: str) -> None:
    with pg.connection() as c:
        c.execute(pg_migrate.retarget(MIGRATION.read_text(encoding="utf-8"), schema))
        c.commit()
        pg.forget_schema(c)  # the file SETs search_path itself


def run_undo(schema: str) -> None:
    text = UNDO.read_text(encoding="utf-8")
    assert _UNDO_SEARCH_PATH in text
    with pg.connection(autocommit=True) as c:  # the file carries its own BEGIN / COMMIT
        c.execute(text.replace(_UNDO_SEARCH_PATH, f'set search_path to "{schema}", public;'))
        pg.forget_schema(c)


def add_encounter(
    conn: Any,
    *,
    title: str = BOSS,
    success_level: int = 2,
    uploaded_by: str = "Alpha",
    started_at: int = BASE,
    duration_s: int = 300,
    zone: str | None = ZONE,
    deaths: int = 0,
    hidden_at: int | None = None,
    uploader_verified: int = 1,
    ranking_barred_at: int | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, duration_s, deaths,"
        " success_level, source_dsn, uploaded_by, guild_name, ingested_at, hidden_at, uploader_verified,"
        " ranking_barred_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'test', %s, 'Exordium', %s, %s, %s, %s) RETURNING id",
        (
            WORLD,
            f"enc-{started_at}-{uploaded_by}-{title}",
            title,
            zone,
            started_at,
            started_at + duration_s,
            duration_s,
            deaths,
            success_level,
            uploaded_by,
            started_at,
            hidden_at,
            uploader_verified,
            ranking_barred_at,
        ),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def add_combatant(
    conn: Any,
    encounter_id: int,
    name: str,
    *,
    ally: int = 0,
    deaths: int = 0,
    is_player: int | None = None,
    damage_taken: int = 0,
    encdps: float = 0.0,
) -> int:
    row = conn.execute(
        "INSERT INTO combatants (encounter_id, name, ally, deaths, is_player, damage_taken, encdps)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (encounter_id, name, ally, deaths, is_player, damage_taken, encdps),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def add_players(conn: Any, encounter_id: int, *, is_player: int | None = 1) -> dict[str, int]:
    return {
        name: add_combatant(conn, encounter_id, name, ally=1, is_player=is_player, encdps=100.0 - i)
        for i, name in enumerate(PLAYERS)
    }


def add_misfiled_kill(conn: Any, **encounter: Any) -> tuple[int, int]:
    """A clean kill as the broken client stored it: the boss row shows no
    death, a located row holds it, three players never died. Returns
    (encounter id, boss row id)."""
    eid = add_encounter(conn, **encounter)
    boss = add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    add_combatant(conn, eid, located(BOSS), deaths=1)
    add_players(conn, eid)
    return eid, boss


def combatants(conn: Any, encounter_id: int) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM combatants WHERE encounter_id = %s", (encounter_id,)).fetchall()
    conn.commit()
    return {r["name"]: dict(r) for r in rows}


def success_of(conn: Any, encounter_id: int) -> int:
    row = conn.execute("SELECT success_level FROM encounters WHERE id = %s", (encounter_id,)).fetchone()
    conn.commit()
    return int(row["success_level"])


def table(conn: Any, name: str) -> list[dict]:
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {name}").fetchall()]  # noqa: S608 — fixed test names
    conn.commit()  # never leave a read transaction open: the repair re-runs DDL on these tables
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))


def snapshot(conn: Any, tables: tuple[str, ...]) -> dict[str, list[dict]]:
    return {name: table(conn, name) for name in tables}


# ── the fold ─────────────────────────────────────────────────────────────────


def test_folds_the_deaths_into_the_real_row_and_removes_the_located_row(schema, conn):
    eid, boss = add_misfiled_kill(conn)
    run_repair(schema)
    rows = combatants(conn, eid)
    assert set(rows) == {BOSS, *PLAYERS}
    assert rows[BOSS]["id"] == boss
    assert rows[BOSS]["deaths"] == 1
    assert rows[BOSS]["damage_taken"] == 5_000_000  # nothing but the death count moves


def test_several_located_rows_for_one_name_add_up(schema, conn):
    eid = add_encounter(conn, success_level=3)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    bravo = add_combatant(conn, eid, "Bravo", ally=1, is_player=1, deaths=1)
    # The located rows land on either side: only encounter and name decide the target.
    add_combatant(conn, eid, located("Bravo", 1.5), ally=1, is_player=0, deaths=1)
    add_combatant(conn, eid, located("Bravo", 2.5), ally=0, deaths=2)
    run_repair(schema)
    rows = combatants(conn, eid)
    assert set(rows) == {BOSS, "Bravo"}
    assert rows["Bravo"]["id"] == bravo
    assert rows["Bravo"]["deaths"] == 4


def test_without_a_real_row_the_first_located_row_is_renamed_and_the_rest_merge(schema, conn):
    eid = add_encounter(conn, success_level=3)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    first = add_combatant(conn, eid, located("Delta", 1.5), ally=1, is_player=0, deaths=1)
    add_combatant(conn, eid, located("Delta", 2.5), ally=1, is_player=0, deaths=2)
    add_combatant(conn, eid, located("a coil tender", 3.5), ally=0, deaths=1)
    run_repair(schema)
    rows = combatants(conn, eid)
    assert set(rows) == {BOSS, "Delta", "a coil tender"}
    assert rows["Delta"]["id"] == first
    assert rows["Delta"]["deaths"] == 3
    # It was classified under a name with spaces in it; an allied row goes
    # back to unclassified so the next read classifies the encounter again.
    assert rows["Delta"]["is_player"] is None
    # An enemy row keeps the classifier's NULL.
    assert rows["a coil tender"]["deaths"] == 1
    assert rows["a coil tender"]["ally"] == 0
    assert rows["a coil tender"]["is_player"] is None


def test_a_tail_that_is_not_the_encounters_zone_is_left_alone(schema, conn):
    # Cutting at the first ' in ' would fold this death into "a prisoner".
    eid = add_encounter(conn)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    add_combatant(conn, eid, "a prisoner", deaths=0)
    odd = located("a prisoner in chains")
    add_combatant(conn, eid, odd, deaths=1)
    add_players(conn, eid)
    before = snapshot(conn, _DATA_TABLES)
    run_repair(schema)
    assert snapshot(conn, _DATA_TABLES) == before
    assert table(conn, "located_death_encounters") == []


def test_an_encounter_without_a_zone_cuts_at_the_first_in(schema, conn):
    eid = add_encounter(conn, zone=None)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    add_combatant(conn, eid, located(BOSS), deaths=1)
    add_players(conn, eid)
    run_repair(schema)
    assert combatants(conn, eid)[BOSS]["deaths"] == 1
    assert success_of(conn, eid) == 1


def test_encounter_deaths_gain_only_the_allied_player_deaths(schema, conn):
    # encounters.deaths is the client's sum over allies with a player-shaped name.
    eid = add_encounter(conn, success_level=3, deaths=1)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    add_combatant(conn, eid, located(BOSS), deaths=1)  # an enemy: not counted
    add_combatant(conn, eid, "Alpha", ally=1, is_player=1, deaths=1)  # the 1 already stored
    add_combatant(conn, eid, located("Alpha"), ally=1, is_player=0, deaths=2)
    add_combatant(conn, eid, "Bravo's pet", ally=1, is_player=0)
    add_combatant(conn, eid, located("Bravo's pet"), ally=1, is_player=0, deaths=1)  # has a space: not counted
    add_combatant(conn, eid, located("Delta"), ally=1, is_player=0, deaths=1)  # renamed: counted
    run_repair(schema)
    row = conn.execute("SELECT deaths, kills FROM encounters WHERE id = %s", (eid,)).fetchone()
    conn.commit()
    assert row == {"deaths": 4, "kills": 0}
    record = table(conn, "located_death_encounters")[0]
    assert (record["old_deaths"], record["new_deaths"]) == (1, 4)


# ── the outcome ──────────────────────────────────────────────────────────────


def test_a_partial_becomes_a_win(schema, conn):
    eid, _ = add_misfiled_kill(conn)
    run_repair(schema)
    assert success_of(conn, eid) == 1
    record = table(conn, "located_death_encounters")[0]
    assert (record["encounter_id"], record["old_success_level"], record["new_success_level"]) == (eid, 2, 1)


def test_a_partial_stays_partial_when_every_allied_player_died(schema, conn):
    eid, _ = add_misfiled_kill(conn)
    for i, name in enumerate(PLAYERS):
        add_combatant(conn, eid, located(name, 40.0 + i), ally=1, is_player=0, deaths=1)
    run_repair(schema)
    rows = combatants(conn, eid)
    assert rows[BOSS]["deaths"] == 1
    assert [rows[p]["deaths"] for p in PLAYERS] == [1, 1, 1]
    assert success_of(conn, eid) == 2


def test_a_partial_stays_partial_when_the_title_mob_did_not_die(schema, conn):
    # The dead add out-scores the title mob, so only the title rule holds this back.
    eid = add_encounter(conn)
    add_combatant(conn, eid, BOSS, damage_taken=1_000)
    add_combatant(conn, eid, "a coil tender", damage_taken=5_000_000)
    add_combatant(conn, eid, located("a coil tender"), deaths=1)
    add_players(conn, eid)
    run_repair(schema)
    assert combatants(conn, eid)["a coil tender"]["deaths"] == 1
    assert success_of(conn, eid) == 2


@pytest.mark.parametrize("stored", [0, 1, 3])
def test_any_other_outcome_is_never_changed(schema, conn, stored):
    eid, _ = add_misfiled_kill(conn, success_level=stored)
    run_repair(schema)
    assert combatants(conn, eid)[BOSS]["deaths"] == 1  # the fold still happens
    assert success_of(conn, eid) == stored


def test_an_encounter_without_located_rows_is_not_touched(schema, conn):
    # A real partial: the boss died, and so did everyone at some point.
    eid = add_encounter(conn)
    add_combatant(conn, eid, BOSS, deaths=1, damage_taken=5_000_000)
    for name in PLAYERS:
        add_combatant(conn, eid, name, ally=1, is_player=1, deaths=1)
    clean = add_encounter(conn, started_at=BASE + 1000)  # would be a win by the rule, but has nothing to repair
    add_combatant(conn, clean, BOSS, deaths=1, damage_taken=5_000_000)
    add_players(conn, clean)
    before = snapshot(conn, _DATA_TABLES + _RECORD_TABLES)
    run_repair(schema)
    assert snapshot(conn, _DATA_TABLES + _RECORD_TABLES) == before


def test_unclassified_allies_are_judged_by_the_clients_name_test(schema, conn):
    # is_player NULL = the classifier has not reached this encounter.
    win = add_encounter(conn, started_at=BASE)
    add_combatant(conn, win, BOSS, damage_taken=5_000_000)
    add_combatant(conn, win, located(BOSS), deaths=1)
    add_players(conn, win, is_player=None)

    pet_only = add_encounter(conn, started_at=BASE + 1000)
    add_combatant(conn, pet_only, BOSS, damage_taken=5_000_000)
    add_combatant(conn, pet_only, located(BOSS), deaths=1)
    add_combatant(conn, pet_only, "Alpha", ally=1, deaths=1)
    add_combatant(conn, pet_only, "Alpha's pet", ally=1)  # alive, but a name with a space is no player

    run_repair(schema)
    assert success_of(conn, win) == 1
    assert success_of(conn, pet_only) == 2


def test_a_survivor_the_classifier_calls_a_pet_does_not_count(schema, conn):
    eid = add_encounter(conn)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    add_combatant(conn, eid, located(BOSS), deaths=1)
    add_combatant(conn, eid, "Alpha", ally=1, is_player=1, deaths=1)
    add_combatant(conn, eid, "Echo", ally=1, is_player=0)  # player-shaped name, classified as a pet
    run_repair(schema)
    assert success_of(conn, eid) == 2


def test_a_renamed_row_is_never_the_survivor(schema, conn):
    # A located row exists because of a death line; one that somehow carries
    # no death must not turn the fight into a win once it has a player's name.
    eid = add_encounter(conn)
    add_combatant(conn, eid, BOSS, damage_taken=5_000_000)
    add_combatant(conn, eid, located(BOSS), deaths=1)
    add_combatant(conn, eid, "Alpha", ally=1, deaths=1)
    add_combatant(conn, eid, located("Delta"), ally=1, deaths=0)
    run_repair(schema)
    assert combatants(conn, eid)["Delta"]["is_player"] is None
    assert success_of(conn, eid) == 2


@pytest.mark.parametrize(("add_damage_taken", "expected"), [(4_000_000, 1), (6_000_000, 2)])
def test_a_stronger_enemy_left_alive_keeps_the_partial(schema, conn, add_damage_taken, expected):
    # The client titles a fight after the enemy with the most damage taken
    # per death. Two deaths halve the title mob's score (10M -> 5M): an add
    # that never died and soaked more than that is what a fixed client would
    # have called the main enemy, and it lived.
    eid = add_encounter(conn)
    add_combatant(conn, eid, BOSS, damage_taken=10_000_000)
    add_combatant(conn, eid, located(BOSS, 1.5), deaths=1)
    add_combatant(conn, eid, located(BOSS, 2.5), deaths=1)
    add_combatant(conn, eid, "a coil tender", damage_taken=add_damage_taken)
    add_players(conn, eid)
    run_repair(schema)
    assert combatants(conn, eid)[BOSS]["deaths"] == 2
    assert success_of(conn, eid) == expected


# ── fights ───────────────────────────────────────────────────────────────────


def _mirrored_fight(conn: Any, **repaired: Any) -> tuple[int, int, int]:
    """One pull, two uploads: a shorter one that already ranks, and a longer
    misfiled one. Returns (fight id, misfiled upload, ranking upload)."""
    misfiled, _ = add_misfiled_kill(conn, uploaded_by="Alpha", duration_s=300, **repaired)
    ranking = add_encounter(conn, uploaded_by="Bravo", started_at=BASE + 5, duration_s=250, success_level=1)
    add_combatant(conn, ranking, BOSS, deaths=1, damage_taken=5_000_000)
    add_players(conn, ranking)
    fight_id = fights.attach_encounter(conn, misfiled)
    assert fights.attach_encounter(conn, ranking) == fight_id
    conn.commit()
    assert fight_id is not None
    return fight_id, misfiled, ranking


def _fight(conn: Any, fight_id: int) -> dict:
    f = fights.get_fight(conn, fight_id)
    conn.commit()
    assert f is not None
    return f


def test_the_repaired_upload_becomes_its_fights_ranking_primary(schema, conn):
    fight_id, misfiled, ranking = _mirrored_fight(conn)
    assert _fight(conn, fight_id)["primary_winning_encounter_id"] == ranking
    run_repair(schema)
    after = _fight(conn, fight_id)
    assert after["primary_winning_encounter_id"] == misfiled  # the longest winning upload
    assert after["primary_encounter_id"] == misfiled
    # The store's own rule agrees with what the migration wrote.
    refreshed = fights.refresh_fight(conn, fight_id)
    conn.commit()
    assert refreshed is not None
    assert refreshed["primary_winning_encounter_id"] == misfiled


def test_a_fight_with_no_win_gains_one(schema, conn):
    eid, _ = add_misfiled_kill(conn)
    fight_id = fights.attach_encounter(conn, eid)
    conn.commit()
    assert fight_id is not None
    assert _fight(conn, fight_id)["primary_winning_encounter_id"] is None
    run_repair(schema)
    assert _fight(conn, fight_id)["primary_winning_encounter_id"] == eid


@pytest.mark.parametrize(
    "repaired",
    [{"hidden_at": BASE + 10}, {"uploader_verified": 0}, {"ranking_barred_at": BASE + 10}],
    ids=["hidden", "unverified", "ranking-barred"],
)
def test_an_upload_that_may_not_rank_is_repaired_but_never_the_primary(schema, conn, repaired):
    fight_id, misfiled, ranking = _mirrored_fight(conn, **repaired)
    run_repair(schema)
    assert success_of(conn, misfiled) == 1
    assert _fight(conn, fight_id)["primary_winning_encounter_id"] == ranking
    refreshed = fights.refresh_fight(conn, fight_id)
    conn.commit()
    assert refreshed is not None
    assert refreshed["primary_winning_encounter_id"] == ranking


# ── idempotency and undo ─────────────────────────────────────────────────────


def _seed_everything(conn: Any) -> dict[str, int]:
    """One database with every shape the repair handles."""
    fight_id, misfiled, _ = _mirrored_fight(conn)
    # A wipe: folded rows on both sides, a renamed row, a merged row, and
    # breakdown rows hanging off a located row.
    wipe = add_encounter(conn, started_at=BASE + 5000, success_level=3, deaths=2)
    add_combatant(conn, wipe, BOSS, damage_taken=900_000)
    add_combatant(conn, wipe, "Alpha", ally=1, is_player=1, deaths=1)
    with_children = add_combatant(conn, wipe, located("Alpha"), ally=1, is_player=0, deaths=1)
    add_combatant(conn, wipe, located("Delta", 1.5), ally=1, is_player=0, deaths=1)
    add_combatant(conn, wipe, located("Delta", 2.5), ally=1, is_player=0, deaths=1)
    conn.execute(
        "INSERT INTO attack_types (combatant_id, swing_type, attack_name, damage) VALUES (%s, 1, 'Strike', 7)",
        (with_children,),
    )
    conn.execute(
        "INSERT INTO damage_types (combatant_id, damage_type, damage) VALUES (%s, 'Outgoing Damage', 7)",
        (with_children,),
    )
    conn.commit()
    return {"fight": fight_id, "misfiled": misfiled, "wipe": wipe, "with_children": with_children}


def test_a_second_run_changes_nothing(schema, conn):
    _seed_everything(conn)
    run_repair(schema)
    first = snapshot(conn, _DATA_TABLES + _RECORD_TABLES)
    assert first["located_death_rows"]  # it did something
    run_repair(schema)
    assert snapshot(conn, _DATA_TABLES + _RECORD_TABLES) == first


def test_the_undo_record_is_complete(schema, conn):
    ids = _seed_everything(conn)
    before = snapshot(conn, _DATA_TABLES)
    located_before = {r["id"]: r for r in before["combatants"] if " in " in r["name"]}
    assert len(located_before) == 4
    run_repair(schema)

    # Every located row is recorded whole, with what happened to its deaths.
    rows = {r["combatant_id"]: r for r in table(conn, "located_death_rows")}
    assert set(rows) == set(located_before)
    for cid, original in located_before.items():
        rec = rows[cid]
        assert json.loads(rec["row_json"]) == json.loads(json.dumps(original))
        assert (rec["encounter_id"], rec["located_name"], rec["deaths"]) == (
            original["encounter_id"],
            original["name"],
            original["deaths"],
        )
    by_action: dict[str, list[dict]] = {}
    for rec in rows.values():
        by_action.setdefault(rec["action"], []).append(rec)
    assert {a: len(v) for a, v in by_action.items()} == {"folded": 2, "renamed": 1, "merged": 1}
    current = {r["id"]: r for r in table(conn, "combatants")}
    for rec in by_action["folded"]:
        assert current[rec["target_combatant_id"]]["name"] == rec["real_name"]
        assert rec["combatant_id"] not in current
    renamed = by_action["renamed"][0]
    assert renamed["target_combatant_id"] == renamed["combatant_id"]
    assert by_action["merged"][0]["target_combatant_id"] == renamed["combatant_id"]

    # Every touched encounter, with its outcome and death total before and after.
    encounters = {r["encounter_id"]: r for r in table(conn, "located_death_encounters")}
    assert set(encounters) == {ids["misfiled"], ids["wipe"]}
    kill, wipe = encounters[ids["misfiled"]], encounters[ids["wipe"]]
    assert (kill["world"], kill["old_success_level"], kill["new_success_level"]) == (WORLD, 2, 1)
    assert (wipe["old_success_level"], wipe["new_success_level"]) == (3, 3)
    assert (wipe["old_deaths"], wipe["new_deaths"]) == (2, 5)

    # The breakdown rows the delete cascaded away.
    children = table(conn, "located_death_children")
    assert sorted(c["source_table"] for c in children) == ["attack_types", "damage_types"]
    assert {c["combatant_id"] for c in children} == {ids["with_children"]}
    for child in children:
        (original,) = [r for r in before[child["source_table"]] if r["id"] == child["row_id"]]
        assert json.loads(child["row_json"]) == json.loads(json.dumps(original))
    assert table(conn, "attack_types") == []
    assert table(conn, "damage_types") == []


def _without_fight_clock(state: dict[str, list[dict]]) -> dict[str, list[dict]]:
    out = dict(state)
    out["fights"] = [{k: v for k, v in f.items() if k != "updated_at"} for f in state["fights"]]
    return out


def test_the_undo_puts_every_row_back(schema, conn):
    ids = _seed_everything(conn)
    before = _without_fight_clock(snapshot(conn, _DATA_TABLES))
    run_repair(schema)
    repaired = _without_fight_clock(snapshot(conn, _DATA_TABLES))
    assert repaired != before
    assert _fight(conn, ids["fight"])["primary_winning_encounter_id"] == ids["misfiled"]

    run_undo(schema)
    assert _without_fight_clock(snapshot(conn, _DATA_TABLES)) == before
    assert snapshot(conn, _RECORD_TABLES) == {name: [] for name in _RECORD_TABLES}

    # New rows still get fresh ids after the located rows came back with theirs.
    fresh = add_combatant(conn, ids["wipe"], "Foxtrot", ally=1)
    assert fresh > max(r["id"] for r in before["combatants"])
    conn.execute("DELETE FROM combatants WHERE id = %s", (fresh,))
    conn.commit()

    # The repair can be applied again by hand, to the same result.
    run_repair(schema)
    assert _without_fight_clock(snapshot(conn, _DATA_TABLES)) == repaired
