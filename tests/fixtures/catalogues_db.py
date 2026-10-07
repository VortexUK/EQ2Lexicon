"""Shared items + spells + recipes family fixtures (Postgres schemas).

``items_schema`` / ``spells_schema`` / ``recipes_schema`` lease isolated
scratch schemas built from db/migrations/0007-0009, re-point the shared
catalogue instances, and yield the schema name — the analog of the old
``XCatalogue(tmp_db)`` + ``catalogue.path`` re-point (and of the
zones/raids fixtures in tests/fixtures/zones_raids_db.py).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from backend.eq2db import aas as aas_module
from backend.eq2db import classes as classes_module
from backend.eq2db import items as items_module
from backend.eq2db import recipes as recipes_module
from backend.eq2db import spells as spells_module
from tests.fixtures.pg import SchemaLeaser

items_leaser = SchemaLeaser("items", "0007_items.sql")
spells_leaser = SchemaLeaser("spells", "0008_spells.sql")
recipes_leaser = SchemaLeaser("recipes", "0009_recipes.sql")
classes_leaser = SchemaLeaser("classes", "0011_classes.sql")
aas_leaser = SchemaLeaser("aas", "0012_aas.sql")


@pytest.fixture
def items_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased items schema with the shared catalogue re-pointed."""
    name = items_leaser.acquire()
    monkeypatch.setattr(items_module, "SCHEMA", name)
    monkeypatch.setattr(items_module.catalogue, "schema", name)
    items_module.catalogue.clear_caches()
    try:
        yield name
    finally:
        items_module.catalogue.clear_caches()
        items_leaser.release(name)


@pytest.fixture
def spells_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased spells schema with the shared catalogue re-pointed."""
    name = spells_leaser.acquire()
    monkeypatch.setattr(spells_module, "SCHEMA", name)
    monkeypatch.setattr(spells_module.catalogue, "schema", name)
    spells_module.catalogue.clear_caches()
    try:
        yield name
    finally:
        spells_module.catalogue.clear_caches()
        spells_leaser.release(name)


@pytest.fixture
def recipes_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased recipes schema with the shared catalogue re-pointed."""
    name = recipes_leaser.acquire()
    monkeypatch.setattr(recipes_module, "SCHEMA", name)
    monkeypatch.setattr(recipes_module.catalogue, "schema", name)
    recipes_module.catalogue.clear_caches()
    try:
        yield name
    finally:
        recipes_module.catalogue.clear_caches()
        recipes_leaser.release(name)


@pytest.fixture
def classes_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased classes schema (migration-seeded with the full 35
    rows) with the shared catalogue re-pointed."""
    name = classes_leaser.acquire()
    monkeypatch.setattr(classes_module, "SCHEMA", name)
    monkeypatch.setattr(classes_module.catalogue, "schema", name)
    classes_module.catalogue.clear_caches()
    try:
        yield name
    finally:
        classes_module.catalogue.clear_caches()
        classes_leaser.release(name)


@pytest.fixture
def aas_schema(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Isolated leased aas schema (migration-seeded with the full 157-tree
    dataset) with the shared catalogue re-pointed."""
    name = aas_leaser.acquire()
    monkeypatch.setattr(aas_module, "SCHEMA", name)
    monkeypatch.setattr(aas_module.catalogue, "schema", name)
    aas_module.catalogue.clear_caches()
    try:
        yield name
    finally:
        aas_module.catalogue.clear_caches()
        aas_leaser.release(name)
