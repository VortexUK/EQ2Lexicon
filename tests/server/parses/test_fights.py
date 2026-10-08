"""Fights (backend/server/parses/fights.py): mirror-groups decided at ingest.

Encounters and their player rosters are seeded with raw SQL so each test
controls exactly the inputs the merge gates read (uploader, start time,
guild, title, roster). The store hooks (hide / delete / guild change) are
exercised through the real store methods.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.server.parses import db as parses_db
from backend.server.parses import fights
from backend.server.parses.boss import boss_key

WORLD = "Varsoon"
BASE = 1_800_000_000


@pytest.fixture
def conn(parses_db_path: str):
    c = parses_db.ParsesStore(parses_db_path).init_db()
    yield c
    c.close()


def seed(
    conn: Any,
    *,
    title: str = "Tarinax",
    uploaded_by: str,
    started_at: int = BASE,
    duration_s: int = 60,
    success_level: int = 1,
    guild_name: str | None = "Exordium",
    roster: tuple[str, ...] = ("P1", "P2", "P3"),
    uploader_verified: int = 1,
    hidden_at: int | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, duration_s, success_level,"
        " source_dsn, uploaded_by, guild_name, ingested_at, uploader_verified, hidden_at)"
        " VALUES (%s, %s, %s, 'Vetrovia', %s, %s, %s, %s, 'eq2act', %s, %s, %s, %s, %s) RETURNING id",
        (
            WORLD,
            f"enc-{started_at}-{uploaded_by}-{title}",
            title,
            started_at,
            started_at + duration_s,
            duration_s,
            success_level,
            uploaded_by,
            guild_name,
            started_at,
            uploader_verified,
            hidden_at,
        ),
    ).fetchone()
    eid = row["id"]
    for i, name in enumerate(roster):
        conn.execute(
            "INSERT INTO combatants (encounter_id, name, ally, is_player, encdps, damage) VALUES (%s, %s, 1, 1, %s, %s)",
            (eid, name, float(100 - i), 1000 - i),
        )
    conn.commit()
    return eid


def fight_of(conn: Any, eid: int) -> dict:
    fid = fights.fight_id_of(conn, eid)
    assert fid is not None
    f = fights.get_fight(conn, fid)
    assert f is not None
    return f


# ── attach rules ─────────────────────────────────────────────────────────────


def test_mirrors_share_a_fight_and_the_longest_is_primary(conn):
    a = seed(conn, uploaded_by="Alpha", duration_s=60)
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 5, duration_s=90)
    assert fights.attach_encounter(conn, a) == fights.attach_encounter(conn, b)
    f = fight_of(conn, a)
    assert f["upload_count"] == 2
    assert f["primary_encounter_id"] == b
    assert f["primary_winning_encounter_id"] == b
    assert (f["first_started_at"], f["last_started_at"]) == (BASE, BASE + 5)
    assert fights.attach_encounter(conn, a) == f["id"]  # idempotent


def test_same_uploader_is_two_fights(conn):
    a = seed(conn, uploaded_by="Alpha")
    b = seed(conn, uploaded_by="Alpha", started_at=BASE + 5)
    assert fights.attach_encounter(conn, a) != fights.attach_encounter(conn, b)


def test_outside_the_window_is_a_new_fight(conn):
    a = seed(conn, uploaded_by="Alpha")
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 61)
    assert fights.attach_encounter(conn, a) != fights.attach_encounter(conn, b)


def test_title_codepoint_variants_and_guild_split(conn):
    a = seed(conn, title="D'Lizta Cheroon", uploaded_by="Alpha")
    b = seed(conn, title="D’Lizta Cheroon", uploaded_by="Bravo", started_at=BASE + 2)
    c = seed(conn, title="D'Lizta Cheroon", uploaded_by="Charlie", started_at=BASE + 3, guild_name="Others")
    fa, fb, fc = (fights.attach_encounter(conn, x) for x in (a, b, c))
    assert fa == fb != fc
    assert fight_of(conn, a)["title_key"] == boss_key("D'Lizta Cheroon")
    assert fight_of(conn, c)["guild_name"] == "Others"


def test_roster_mismatch_does_not_merge(conn):
    a = seed(conn, uploaded_by="Alpha", roster=("P1", "P2", "P3"))
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 5, roster=("Q1", "Q2", "Q3"))
    assert fights.attach_encounter(conn, a) != fights.attach_encounter(conn, b)


def test_raid_sized_uses_top_three(conn):
    eight = tuple(f"P{i}" for i in range(8))
    a = seed(conn, uploaded_by="Alpha", roster=eight)
    # Top 2 match (P0, P1) but the third-best (P2) is missing from b: a raid
    # fight compares top-3, so these are two fights.
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 5, roster=("P0", "P1", "X2", "P3", "P4", "P5", "P6", "P7"))
    assert fights.attach_encounter(conn, a) != fights.attach_encounter(conn, b)


def test_late_straggler_attaches_via_any_member(conn):
    a = seed(conn, uploaded_by="Alpha", started_at=BASE)
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 50)
    c = seed(conn, uploaded_by="Charlie", started_at=BASE + 100)  # 100 s from a, 50 s from b
    fa = fights.attach_encounter(conn, a)
    assert fights.attach_encounter(conn, b) == fa
    assert fights.attach_encounter(conn, c) == fa


# ── primaries follow visibility / verification ───────────────────────────────


def test_primaries_skip_hidden_unverified_and_losing_uploads(conn):
    a = seed(conn, uploaded_by="Alpha", duration_s=60, success_level=2)  # loss
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 1, duration_s=90, uploader_verified=0)
    c = seed(conn, uploaded_by="Charlie", started_at=BASE + 2, duration_s=70)
    for x in (a, b, c):
        fights.attach_encounter(conn, x)
    f = fight_of(conn, a)
    assert f["primary_encounter_id"] == b  # longest visible, verification irrelevant for the list
    assert f["primary_winning_encounter_id"] == c  # longest visible+verified+winning


def test_hide_unhide_and_delete_hooks_refresh_the_fight(conn):
    a = seed(conn, uploaded_by="Alpha", duration_s=60)
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 1, duration_s=90)
    for x in (a, b):
        fights.attach_encounter(conn, x)
    fid = fights.fight_id_of(conn, a)

    assert parses_db.store.soft_delete_encounter(conn, b, hidden_at=1, hidden_by="admin")
    assert fight_of(conn, a)["primary_winning_encounter_id"] == a
    assert parses_db.store.unhide_encounter(conn, b)
    assert fight_of(conn, a)["primary_winning_encounter_id"] == b

    assert parses_db.store.delete_encounter(conn, b)
    f = fight_of(conn, a)
    assert (f["upload_count"], f["primary_encounter_id"]) == (1, a)
    assert parses_db.store.delete_encounter(conn, a)
    assert fights.get_fight(conn, fid) is None  # an emptied fight goes


def test_guild_backfill_moves_the_upload_to_its_guilds_fight(conn):
    a = seed(conn, uploaded_by="Alpha", guild_name="Exordium")
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 1, guild_name=None)  # guild unknown at ingest
    fa = fights.attach_encounter(conn, a)
    fb = fights.attach_encounter(conn, b)
    assert fa != fb
    assert parses_db.store.set_encounter_guild_name(conn, b, "Exordium")
    assert fights.fight_id_of(conn, b) == fa
    assert fights.get_fight(conn, fb) is None
    assert fight_of(conn, a)["upload_count"] == 2


# ── backfill: the bulk grouper and the incremental attach agree ───────────────


def _partition(conn: Any, ids: list[int]) -> set[frozenset[int]]:
    by_fight: dict[int, set[int]] = {}
    for eid in ids:
        by_fight.setdefault(fights.fight_id_of(conn, eid), set()).add(eid)  # type: ignore[arg-type]
    return {frozenset(v) for v in by_fight.values()}


def test_bulk_backfill_matches_incremental_attach(conn):
    ids: list[int] = []
    t = BASE
    # 25 pulls x 3 uploaders = 75 uploads (> BULK_THRESHOLD), interleaved
    # with same-boss pulls by the same raid 10 minutes later.
    for pull in range(25):
        title = "Tarinax" if pull % 2 == 0 else "Cazel"
        roster = tuple(f"R{pull % 4}_{i}" for i in range(8))
        for k, up in enumerate(("Alpha", "Bravo", "Charlie")):
            ids.append(seed(conn, title=title, uploaded_by=up, started_at=t + 3 * k, duration_s=60 + k, roster=roster))
        t += 600
    assert fights.backfill_world(conn, WORLD) == 75
    bulk = _partition(conn, ids)
    assert len(bulk) == 25

    conn.execute("UPDATE encounters SET fight_id = NULL")
    conn.execute("DELETE FROM fights")
    conn.commit()
    for eid in ids:  # chronological, like ingest
        fights.attach_encounter(conn, eid)
    assert _partition(conn, ids) == bulk
    for eid in ids:
        f = fight_of(conn, eid)
        assert f["upload_count"] == 3
        # longest = the Charlie upload (duration 62) in every pull
        primary = conn.execute("SELECT uploaded_by FROM encounters WHERE id = %s", (f["primary_encounter_id"],))
        assert primary.fetchone()["uploaded_by"] == "Charlie"


def test_backfill_is_a_noop_when_everything_is_grouped(conn):
    a = seed(conn, uploaded_by="Alpha")
    assert fights.backfill_world(conn, WORLD) == 1
    assert fights.backfill_world(conn, WORLD) == 0
    assert fight_of(conn, a)["upload_count"] == 1


# ── incremental rankings sync ────────────────────────────────────────────────


def test_sync_fight_replaces_the_fights_kill_in_the_cached_dataset(conn):
    from backend.server.api import rankings as rk

    key = f"{rk._KILLS_KEY}:{WORLD}"
    rk.rankings_cache.set(key, [])  # warm (empty) cache: sync must keep it live
    a = seed(conn, uploaded_by="Alpha", duration_s=60, roster=tuple(f"P{i}" for i in range(8)))
    b = seed(conn, uploaded_by="Bravo", started_at=BASE + 1, duration_s=90, roster=tuple(f"P{i}" for i in range(8)))
    for x in (a, b):
        fights.attach_encounter(conn, x)
    conn.commit()  # attach leaves the transaction to its caller; the sync reads on its own connection
    fid = fights.fight_id_of(conn, a)
    assert fid is not None

    rk._sync_fight_sync(WORLD, fid)
    kills = rk.rankings_cache.peek(key)
    assert [k["id"] for k in kills] == [b]
    assert kills[0]["scope"] == "raid"
    assert {c["name"] for c in kills[0]["combatants"]} == {f"P{i}" for i in range(8)}

    # Hiding the primary promotes the other upload; the cache follows.
    parses_db.store.soft_delete_encounter(conn, b, hidden_at=1, hidden_by="admin")
    rk._sync_fight_sync(WORLD, fid)
    assert [k["id"] for k in rk.rankings_cache.peek(key)] == [a]

    # Hiding the last visible upload drops the fight from the boards.
    parses_db.store.soft_delete_encounter(conn, a, hidden_at=1, hidden_by="admin")
    rk._sync_fight_sync(WORLD, fid)
    assert rk.rankings_cache.peek(key) == []

    # A cold cache is left for the full build.
    rk.rankings_cache.clear()
    rk._sync_fight_sync(WORLD, fid)
    assert rk.rankings_cache.peek(key) is None
