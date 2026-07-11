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
    """Tmp-path-backed parses.db with schema initialised + store re-pointed."""
    db_file = tmp_path / "backend.server.parses.db"
    monkeypatch.setattr(parses_db.store, "path", db_file)
    monkeypatch.setattr(parses_db.store, "path", db_file)
    parses_db.ParsesStore(db_file).init_db().close()
    return db_file


@pytest.fixture
def parses_db_conn(tmp_path: Path) -> Generator[sqlite3.Connection]:
    """Throwaway parses DB connection (schema already applied).

    File-backed (BaseCatalogue dropped :memory: support — per-read
    connections would each see a fresh empty memory DB)."""
    conn = parses_db.ParsesStore(tmp_path / "conn_parses.db").init_db()
    yield conn
    conn.close()
