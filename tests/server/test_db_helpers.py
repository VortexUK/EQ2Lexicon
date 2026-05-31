"""Tests for web/lib/db_helpers — connection lifecycle + LIKE escape."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.server.core.db_helpers import like_escape, read_only_conn


def test_like_escape_handles_wildcards() -> None:
    assert like_escape("foo%bar") == "foo\\%bar"
    assert like_escape("foo_bar") == "foo\\_bar"
    assert like_escape("foo\\bar") == "foo\\\\bar"
    assert like_escape("plain") == "plain"


def test_read_only_conn_returns_row_factory(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    # Seed the DB first via a writable connection.
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE foo (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO foo VALUES (1, 'bar')")
        conn.commit()

    with read_only_conn(db) as ro:
        row = ro.execute("SELECT * FROM foo").fetchone()
        assert row["id"] == 1
        assert row["name"] == "bar"


def test_read_only_conn_blocks_writes(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.commit()

    with read_only_conn(db) as ro:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO foo VALUES (1)")
