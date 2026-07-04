from __future__ import annotations

from backend.census import store as cs


def test_init_db_creates_tables(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"characters", "guilds"} <= tables
        char_cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)")}
        assert {
            "name_lower",
            "world",
            "name",
            "level",
            "guild_name",
            "data_json",
            "last_resolved_at",
            "updated_at",
        } <= char_cols
    finally:
        conn.close()


def test_upsert_character_resolved_then_get(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        cs.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "level": 90, "guild_name": "Exordium", "cls": "Templar"},
            resolved=True,
            now=1000,
        )
        rec = cs.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["level"] == 90
        assert rec["last_resolved_at"] == 1000
    finally:
        conn.close()


def test_sparse_refresh_never_clobbers(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        cs.upsert_character(
            conn, "Menludiir", "Varsoon", {"name": "Menludiir", "level": 90, "cls": "Templar"}, resolved=True, now=1000
        )
        cs.upsert_character(
            conn, "Menludiir", "Varsoon", {"name": "Menludiir", "level": None, "cls": None}, resolved=False, now=2000
        )
        rec = cs.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["level"] == 90  # kept
        assert rec["last_resolved_at"] == 1000  # unchanged
    finally:
        conn.close()


def test_roster_overview_does_not_wipe_resolved_gear(tmp_path):
    """A guild-roster overview (no equipment key) must NOT null an individually
    resolved character's gear — it overlays its scalar fields and preserves the
    rest, without advancing the freshness clock."""
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        # Full individual resolve: gear + id present.
        cs.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "id": "123", "level": 70, "cls": "Templar", "equipment": [{"slot": "Head"}]},
            resolved=True,
            now=1000,
        )
        # Later guild-roster refresh: sparse overview, resolved=True, newer ts.
        cs.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "level": 71, "cls": "Templar", "deity": "Tunare"},
            resolved=True,
            now=2000,
        )
        rec = cs.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["equipment"] == [{"slot": "Head"}]  # gear preserved
        assert rec["data"]["id"] == "123"  # id preserved
        assert rec["data"]["level"] == 71  # scalar refreshed from the overview
        assert rec["data"]["deity"] == "Tunare"  # new field merged in
        assert rec["last_resolved_at"] == 1000  # partial overlay didn't advance freshness
    finally:
        conn.close()


def test_full_resolve_replaces_and_advances_freshness(tmp_path):
    """A full resolve (equipment key present) overlays gear and advances the
    freshness clock — genuine gear changes still take effect."""
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        cs.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "id": "123", "equipment": [{"slot": "Head"}]},
            resolved=True,
            now=1000,
        )
        cs.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "id": "123", "equipment": [{"slot": "Chest"}]},
            resolved=True,
            now=2000,
        )
        rec = cs.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["equipment"] == [{"slot": "Chest"}]  # replaced
        assert rec["last_resolved_at"] == 2000  # advanced
    finally:
        conn.close()


def test_unresolved_first_sight_is_not_stored(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        cs.upsert_character(conn, "Ghost", "Varsoon", {"name": "Ghost"}, resolved=False, now=1000)
        assert cs.get_character(conn, "Ghost", "Varsoon") is None
    finally:
        conn.close()


def test_get_missing_returns_none(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        assert cs.get_character(conn, "Nobody", "Varsoon") is None
    finally:
        conn.close()


def test_upsert_guild_then_get(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        blob = {"name": "Exordium", "members": [{"name": "Menludiir", "rank": "Leader"}]}
        cs.upsert_guild(conn, "Exordium", "Varsoon", blob, now=1000)
        rec = cs.get_guild(conn, "Exordium", "Varsoon")
        assert rec is not None
        assert rec["data"]["members"][0]["name"] == "Menludiir"
        assert rec["last_resolved_at"] == 1000
    finally:
        conn.close()


def test_guild_get_missing_returns_none(tmp_path):
    conn = cs.init_db(tmp_path / "backend.census.db")
    try:
        assert cs.get_guild(conn, "Nope", "Varsoon") is None
    finally:
        conn.close()
