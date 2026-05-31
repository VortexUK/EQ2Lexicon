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
