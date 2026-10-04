"""Shared parses fixtures used by tests/parses/ AND tests/server/.

Postgres edition: the parses family lives in the ``parses`` schema
(db/migrations/0002_parses.sql). Two fixtures are exposed, keeping their
historical names so test bodies don't churn:

  - parses_db_path: leases an isolated scratch parses schema, points the
    shared store at it for the test, and yields the SCHEMA NAME (str) —
    the analog of the old tmp-file + DB_PATH re-point.

  - parses_db_conn: leases a scratch schema and yields an open,
    schema-scoped connection (PgConnProxy — dict rows, %s params); the
    store's conn-taking staticmethods accept it directly.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from backend.server.parses import db as parses_db
from tests.fixtures.pg import SchemaLeaser

#: Scratch-schema pool for the parses family (parses_s<pid>_N).
parses_leaser = SchemaLeaser("parses", "0002_parses.sql")


@pytest.fixture
def parses_db_path(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased parses schema with the shared store re-pointed."""
    name = parses_leaser.acquire()
    monkeypatch.setattr(parses_db, "SCHEMA", name)
    monkeypatch.setattr(parses_db.store, "schema", name)
    try:
        yield name
    finally:
        parses_leaser.release(name)


@pytest.fixture
def parses_db_conn() -> Generator[Any]:
    """Throwaway schema-scoped parses connection (leased scratch schema)."""
    name = parses_leaser.acquire()
    conn = parses_db.ParsesStore(name).init_db()
    try:
        yield conn
    finally:
        conn.close()
        parses_leaser.release(name)
