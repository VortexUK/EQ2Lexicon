"""Shared parses-DB fixture used by tests/parses/ AND tests/web/.

Hoisted from tests/parses/conftest.py per TEST-011: 7+ tests in
tests/web/test_parses_world_scoping.py were re-doing the same
monkeypatch + init_db setup inline.

Two fixtures are exposed:

  - parses_db_path: yields a tmp_path / "backend.server.parses.db" with the schema
    pre-initialised, AND monkeypatches parses_db.DB_PATH to point at it
    for the duration of the test. This is what the web tests need.

  - parses_db_conn: yields an in-memory connection, the schema applied.
    Matches the existing tests/parses/conftest.py fixture name — kept
    for parses unit tests that need a connection handle.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from backend.server.parses import db as parses_db


@pytest.fixture
def parses_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tmp-path-backed parses.db with schema initialised + DB_PATH patched."""
    db_file = tmp_path / "backend.server.parses.db"
    monkeypatch.setattr(parses_db, "DB_PATH", db_file)
    parses_db.init_db(db_file).close()
    return db_file


@pytest.fixture
def parses_db_conn() -> Generator[sqlite3.Connection]:
    """In-memory parses DB connection (schema already applied)."""
    conn = parses_db.init_db(":memory:")  # type: ignore[arg-type]
    yield conn
    conn.close()
