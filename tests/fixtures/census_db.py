"""Shared census-family fixture (Postgres ``census`` schema).

``census_schema`` leases an isolated scratch schema built from
db/migrations/0003_census.sql, re-points the shared CensusStore at it,
and yields the schema name — the analog of the old
``CensusStore(tmp_db)`` + ``store.path`` re-point.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from backend.census import store as census_store_module
from tests.fixtures.pg import SchemaLeaser

#: Scratch-schema pool for the census family (census_s<pid>_N).
census_leaser = SchemaLeaser("census", "0003_census.sql")


@pytest.fixture
def census_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased census schema with the shared store re-pointed."""
    name = census_leaser.acquire()
    monkeypatch.setattr(census_store_module, "SCHEMA", name)
    monkeypatch.setattr(census_store_module.store, "schema", name)
    try:
        yield name
    finally:
        census_leaser.release(name)
