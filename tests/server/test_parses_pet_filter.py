"""Tests for the Phase-4 SQL filter switch + lazy backfill in
web/routes/parses/list.py.

After Phase 4: _PLAYER_COUNT_SQL filters on is_player=1, not on the
old multi-word/Unknown predicate. _ensure_classified runs lazy
backfill for any encounter whose combatants still have is_player=NULL
(i.e. pre-migration historic rows).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.parses import db as parses_db
from tests.fixtures.users import make_fake_require_user, make_fake_user

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


@pytest.fixture
def parses_db_in_memory(tmp_path, monkeypatch):
    """Point parses_db.DB_PATH at a tmp-path-backed DB. The schema is
    pre-initialised by opening + closing a connection; subsequent
    ``parses_db.store.init_db()`` calls (from the route handler in another
    thread) open a fresh connection against the same file, which is
    safe with sqlite3 + WAL.

    We can't use ``:memory:`` here because the route handler's read
    path early-returns when ``DB_PATH.exists()`` is False, and
    ``Path(':memory:').exists()`` is always False."""
    db_file = tmp_path / "backend.server.parses.db"
    parses_db.ParsesStore(db_file).init_db().close()
    monkeypatch.setattr(parses_db.store, "path", db_file)
    # The test body needs an open connection to seed + assert against.
    # The route handler opens its OWN connection via the unpatched
    # init_db(path) — same file, separate handle, no thread issues.
    conn = parses_db.ParsesStore(db_file).init_db()
    try:
        yield conn
    finally:
        conn.close()


def _insert_encounter(conn: sqlite3.Connection, act_encid: str, zone: str = "Z") -> int:
    cur = conn.execute(
        """
        INSERT INTO encounters (
            act_encid, title, zone, started_at, ended_at, duration_s,
            total_damage, encdps, kills, deaths, source_dsn, ingested_at, world
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (act_encid, "Test", zone, 1, 2, 1, 100, 100.0, 0, 0, "test", 1, "Varsoon"),
    )
    return int(cur.lastrowid or 0)


@pytest.mark.asyncio
async def test_player_count_reads_is_player_flag(app, parses_db_in_memory):
    """A combatant whose is_player=0 must NOT count toward player_count
    even if its name is single-word + ally=1 (the old heuristic would
    have counted it)."""
    enc_id = _insert_encounter(parses_db_in_memory, "encA")
    for name in ("Alpha", "Bravo", "Charlie"):
        parses_db_in_memory.execute(
            "INSERT INTO combatants (encounter_id, name, ally, is_player) VALUES (?, ?, ?, ?)",
            (enc_id, name, 1, 0),
        )
    parses_db_in_memory.commit()

    with patch("backend.server.api.parses.list._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses?limit=10")

    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["player_count"] == 0, "single-word allies with is_player=0 must not count"


def test_ensure_classified_backfills_null_rows(parses_db_in_memory):
    """Inserting an encounter with combatant.is_player=NULL (mimicking a
    historic pre-migration row) then calling _ensure_classified on it
    must populate is_player on every row."""
    from backend.server.api.parses.list import _ensure_classified

    enc_id = _insert_encounter(parses_db_in_memory, "encB", zone="Halls of Fate")
    # Two confirmed (cls set) and one multi-word pet, all is_player=NULL.
    parses_db_in_memory.execute(
        "INSERT INTO combatants (encounter_id, name, ally, cls, is_player) VALUES (?, ?, ?, ?, ?)",
        (enc_id, "Alpha", 1, "Wizard", None),
    )
    parses_db_in_memory.execute(
        "INSERT INTO combatants (encounter_id, name, ally, cls, is_player) VALUES (?, ?, ?, ?, ?)",
        (enc_id, "Bravo", 1, "Wizard", None),
    )
    parses_db_in_memory.execute(
        "INSERT INTO combatants (encounter_id, name, ally, cls, is_player) VALUES (?, ?, ?, ?, ?)",
        (enc_id, "a krait warrior", 1, None, None),
    )
    parses_db_in_memory.commit()

    # Sanity: every is_player is NULL.
    nulls = parses_db_in_memory.execute("SELECT COUNT(*) FROM combatants WHERE is_player IS NULL").fetchone()[0]
    assert nulls == 3

    with patch("backend.server.api.parses.list._classify_zone", return_value="dungeon"):
        _ensure_classified(parses_db_in_memory, enc_id, "Halls of Fate")

    rows = {r[0]: r[1] for r in parses_db_in_memory.execute("SELECT name, is_player FROM combatants")}
    assert rows["Alpha"] == 1
    assert rows["Bravo"] == 1
    assert rows["a krait warrior"] == 0


def test_ensure_classified_is_noop_when_already_classified(parses_db_in_memory):
    """Once every combatant has is_player populated, _ensure_classified
    must not re-run the classifier (no extra writes)."""
    from backend.server.api.parses.list import _ensure_classified

    enc_id = _insert_encounter(parses_db_in_memory, "encC")
    parses_db_in_memory.execute(
        "INSERT INTO combatants (encounter_id, name, ally, is_player) VALUES (?, ?, ?, ?)",
        (enc_id, "Alpha", 1, 1),
    )
    parses_db_in_memory.commit()

    with patch("backend.server.parses.pet_detection.classify_combatants") as fake:
        _ensure_classified(parses_db_in_memory, enc_id, None)
        fake.assert_not_called()


@pytest.mark.asyncio
async def test_phase4_merger_top_n_uses_is_player(app, parses_db_in_memory):
    """Phase-4-of-parse-grouping-redo merger's top-N gate must filter on
    is_player=1, so a bucket-promoted player CAN appear in top-N for
    merge decisions and a regex-matched pet CANNOT (even with high encdps).
    Two uploads of the same fight with identical top-N should merge."""
    for encid, uploader in (("encD1", "Alpha"), ("encD2", "Bravo")):
        enc_id = _insert_encounter(parses_db_in_memory, encid)
        # Override the guild/uploader on the freshly-inserted encounter row.
        parses_db_in_memory.execute(
            "UPDATE encounters SET uploaded_by = ?, guild_name = ?, title = ? WHERE id = ?",
            (uploader, "Exordium", "Bossy", enc_id),
        )
        # Top-3 by encdps in both encounters are the same three players —
        # plus one regex-matching pet with very high encdps that MUST NOT
        # qualify as top-N.
        for name, encdps, is_player in (
            ("Alpha", 5000.0, 1),
            ("Bravo", 4000.0, 1),
            ("Charlie", 3000.0, 1),
            ("Gibab", 99999.0, 0),  # pet — must not show in top-N
        ):
            parses_db_in_memory.execute(
                "INSERT INTO combatants (encounter_id, name, ally, encdps, is_player) VALUES (?, ?, ?, ?, ?)",
                (enc_id, name, 1, encdps, is_player),
            )
    parses_db_in_memory.commit()

    with patch("backend.server.api.parses.list._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses?limit=10")

    assert r.status_code == 200
    data = r.json()
    # player_count = 3 → group bucket → N=2 in the merger gate.
    # Identical top-2 ⇒ merge.
    assert data["total"] == 1, f"merger should treat both uploads as the same fight; got {data}"
