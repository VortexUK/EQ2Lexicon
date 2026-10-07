"""Shared parses fixtures used by tests/parses/ AND tests/server/.

- parses_db_path: leases a scratch parses schema, points the shared store
  at it, and yields the SCHEMA NAME (str).
- parses_db_conn: leases a scratch schema and yields an open schema-scoped
  connection (dict rows, %s params) for the store's conn-taking staticmethods.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from backend.server.parses import db as parses_db
from tests.fixtures.pg import SchemaLeaser

#: Scratch-schema pool for the parses family (parses_s<pid>_N).
parses_leaser = SchemaLeaser("parses")


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
