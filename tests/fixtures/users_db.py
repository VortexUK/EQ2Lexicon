"""Shared users-family test plumbing.

Every users-family store carries a ``schema`` attribute (default
``"users"``, the session schema). Re-pointing the family for a test
means re-pointing the facade constant AND every store instance — this
helper is the one place that knows that, so per-file loops can't fork.

Most tests should just take the ``users_schema`` fixture
(tests/fixtures/pg.py), which leases a clean scratch schema and calls
this for you::

    def test_something(users_schema):
        ...  # every store now points at an isolated schema
"""

from __future__ import annotations

import pytest

from backend.server import db as users_db


def point_users_db_at(monkeypatch: pytest.MonkeyPatch, schema: str) -> None:
    """Re-point the facade constant + every domain store at ``schema``."""
    monkeypatch.setattr(users_db, "SCHEMA", schema)
    for store in users_db.ALL_STORES:
        monkeypatch.setattr(store, "schema", schema)
