from __future__ import annotations

from backend.census import store as cs


def test_init_db_creates_tables(tmp_path):
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
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
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        cs.CensusStore.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "level": 90, "guild_name": "Exordium", "cls": "Templar"},
            resolved=True,
            now=1000,
        )
        rec = cs.CensusStore.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["level"] == 90
        assert rec["last_resolved_at"] == 1000
    finally:
        conn.close()


def test_sparse_refresh_never_clobbers(tmp_path):
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        cs.CensusStore.upsert_character(
            conn, "Menludiir", "Varsoon", {"name": "Menludiir", "level": 90, "cls": "Templar"}, resolved=True, now=1000
        )
        cs.CensusStore.upsert_character(
            conn, "Menludiir", "Varsoon", {"name": "Menludiir", "level": None, "cls": None}, resolved=False, now=2000
        )
        rec = cs.CensusStore.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["level"] == 90  # kept
        assert rec["last_resolved_at"] == 1000  # unchanged
    finally:
        conn.close()


def test_roster_overview_does_not_wipe_resolved_gear(tmp_path):
    """A guild-roster overview (no equipment key) must NOT null an individually
    resolved character's gear — it overlays its scalar fields and preserves the
    rest, without advancing the freshness clock."""
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        # Full individual resolve: gear + id present.
        cs.CensusStore.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "id": "123", "level": 70, "cls": "Templar", "equipment": [{"slot": "Head"}]},
            resolved=True,
            now=1000,
        )
        # Later guild-roster refresh: sparse overview, resolved=True, newer ts.
        cs.CensusStore.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "level": 71, "cls": "Templar", "deity": "Tunare"},
            resolved=True,
            now=2000,
        )
        rec = cs.CensusStore.get_character(conn, "Menludiir", "Varsoon")
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
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        cs.CensusStore.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "id": "123", "equipment": [{"slot": "Head"}]},
            resolved=True,
            now=1000,
        )
        cs.CensusStore.upsert_character(
            conn,
            "Menludiir",
            "Varsoon",
            {"name": "Menludiir", "id": "123", "equipment": [{"slot": "Chest"}]},
            resolved=True,
            now=2000,
        )
        rec = cs.CensusStore.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["equipment"] == [{"slot": "Chest"}]  # replaced
        assert rec["last_resolved_at"] == 2000  # advanced
    finally:
        conn.close()


def test_unresolved_first_sight_is_not_stored(tmp_path):
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        cs.CensusStore.upsert_character(conn, "Ghost", "Varsoon", {"name": "Ghost"}, resolved=False, now=1000)
        assert cs.CensusStore.get_character(conn, "Ghost", "Varsoon") is None
    finally:
        conn.close()


def test_get_missing_returns_none(tmp_path):
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        assert cs.CensusStore.get_character(conn, "Nobody", "Varsoon") is None
    finally:
        conn.close()


def test_upsert_guild_then_get(tmp_path):
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        blob = {"name": "Exordium", "members": [{"name": "Menludiir", "rank": "Leader"}]}
        cs.CensusStore.upsert_guild(conn, "Exordium", "Varsoon", blob, now=1000)
        rec = cs.CensusStore.get_guild(conn, "Exordium", "Varsoon")
        assert rec is not None
        assert rec["data"]["members"][0]["name"] == "Menludiir"
        assert rec["last_resolved_at"] == 1000
    finally:
        conn.close()


def test_guild_get_missing_returns_none(tmp_path):
    conn = cs.CensusStore(tmp_path / "backend.census.db").init_db()
    try:
        assert cs.CensusStore.get_guild(conn, "Nope", "Varsoon") is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Guild history — one row per guild per UTC day
# ---------------------------------------------------------------------------

_DAY = 86400
_T0 = 1_800_000_000  # 2027-01-15T08:00:00Z


def _history_conn(tmp_path):
    return cs.CensusStore(tmp_path / "backend.census.db").init_db()


def _write(conn, now: int, level: int = 300, name: str = "Exordium", retention_days: int = 400) -> None:
    cs.CensusStore.upsert_guild_history(
        conn,
        name,
        "Varsoon",
        {
            "level": level,
            "members": 40,
            "accounts": 30,
            "achievement_count": 5,
            "max_level_members": 12,
            "distinct_classes": 9,
        },
        now=now,
        retention_days=retention_days,
    )


def test_init_db_creates_guild_history(tmp_path):
    conn = _history_conn(tmp_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(guild_history)")}
        assert {
            "world",
            "name_lower",
            "day",
            "captured_at",
            "level",
            "members",
            "accounts",
            "achievement_count",
            "max_level_members",
            "distinct_classes",
        } == cols
    finally:
        conn.close()


def test_same_day_writes_collapse_to_one_row_last_capture_wins(tmp_path):
    conn = _history_conn(tmp_path)
    try:
        _write(conn, _T0, level=300)
        _write(conn, _T0 + 3600, level=301)  # 15-minute refresh later the same UTC day
        rows = cs.CensusStore.get_guild_history(conn, "exordium", "Varsoon", 7, now=_T0 + 3600)
        assert len(rows) == 1
        assert rows[0]["level"] == 301
        assert rows[0]["captured_at"] == _T0 + 3600
        assert rows[0]["day"] == cs.CensusStore._utc_day(_T0)
        assert rows[0]["members"] == 40 and rows[0]["distinct_classes"] == 9
    finally:
        conn.close()


def test_window_is_inclusive_of_the_boundary_day_and_ordered_oldest_first(tmp_path):
    conn = _history_conn(tmp_path)
    try:
        for d in (10, 7, 3, 0):
            _write(conn, _T0 - d * _DAY, level=100 + d)
        week = cs.CensusStore.get_guild_history(conn, "Exordium", "Varsoon", 7, now=_T0)
        assert [r["level"] for r in week] == [107, 103, 100]
        assert [r["day"] for r in week] == sorted(r["day"] for r in week)
        assert cs.CensusStore.get_guild_history(conn, "Exordium", "Varsoon", 400, now=_T0)[0]["level"] == 110
    finally:
        conn.close()


def test_write_prunes_only_this_guilds_rows_past_retention(tmp_path):
    conn = _history_conn(tmp_path)
    try:
        _write(conn, _T0 - 500 * _DAY, retention_days=400)
        _write(conn, _T0 - 500 * _DAY, name="Other", retention_days=400)
        _write(conn, _T0 - 100 * _DAY, retention_days=400)
        _write(conn, _T0, retention_days=400)  # prunes Exordium's 500-day-old row
        mine = cs.CensusStore.get_guild_history(conn, "Exordium", "Varsoon", 1000, now=_T0)
        assert len(mine) == 2
        assert cs.CensusStore.get_guild_history(conn, "Other", "Varsoon", 1000, now=_T0)  # untouched
    finally:
        conn.close()


def test_history_missing_guild_is_empty(tmp_path):
    conn = _history_conn(tmp_path)
    try:
        assert cs.CensusStore.get_guild_history(conn, "Nobody", "Varsoon", 90) == []
    finally:
        conn.close()
