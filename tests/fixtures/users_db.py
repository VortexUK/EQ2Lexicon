"""Shared users.db test plumbing.

The seven users.db domain stores each carry their own ``path`` (captured
from DB_PATH at import). Re-pointing users.db for a test therefore means
re-pointing the module constant AND every store instance — this helper is
the one place that knows that, so per-file loops can't fork (some copies
had drifted to skip the DB_PATH half).

Usage in a fixture::

    from tests.fixtures.users_db import point_users_db_at

    @pytest.fixture(autouse=True)
    def _users_tmp(tmp_path, monkeypatch):
        db_file = tmp_path / "users.db"
        init_db(db_file)
        point_users_db_at(monkeypatch, db_file)
        return db_file
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.server import db as users_db


def point_users_db_at(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Re-point the users.db module constant + every domain store at ``path``."""
    monkeypatch.setattr(users_db, "DB_PATH", path)
    for store in users_db.ALL_STORES:
        monkeypatch.setattr(store, "path", path)
