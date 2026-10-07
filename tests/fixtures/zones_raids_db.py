"""Shared zones + raids family fixtures (Postgres schemas).

``zones_schema`` / ``raids_schema`` lease isolated scratch schemas built
from db/migrations/0004_zones.sql / 0005_raids.sql, re-point the shared
catalogue instances, and yield the schema name.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from backend.eq2db import raids as raids_module
from backend.eq2db import zones as zones_module
from tests.fixtures.pg import SchemaLeaser

zones_leaser = SchemaLeaser("zones")
raids_leaser = SchemaLeaser("raids")


@pytest.fixture
def zones_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased zones schema with the shared catalogue re-pointed."""
    name = zones_leaser.acquire()
    monkeypatch.setattr(zones_module, "SCHEMA", name)
    monkeypatch.setattr(zones_module.catalogue, "schema", name)
    try:
        yield name
    finally:
        zones_leaser.release(name)


@pytest.fixture
def raids_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased raids schema with the shared catalogue re-pointed."""
    name = raids_leaser.acquire()
    monkeypatch.setattr(raids_module, "SCHEMA", name)
    monkeypatch.setattr(raids_module.catalogue, "schema", name)
    try:
        yield name
    finally:
        raids_leaser.release(name)
