"""Shared base class for the eq2db catalogue classes.

Every data module under ``backend/eq2db/`` exposes one catalogue class
(AACatalogue, ClassCatalogue, SpellCatalogue, RecipeCatalogue,
ZoneCatalogue, ItemCatalogue, RaidCatalogue) following the same
convention:

  * the DB path lives on the instance (``self.path``); a shared
    module-level ``catalogue`` instance is the runtime entry point and
    tests construct ``XCatalogue(tmp_db)``;
  * pure domain helpers are staticmethods with their full bodies in the
    class; DB reads are instance methods; conn-taking write helpers are
    staticmethods (callers own the transaction);
  * ``tests/conftest.py`` re-points ``catalogue.path`` alongside the
    module ``DB_PATH`` after env-based re-resolution.

:class:`BaseCatalogue` owns the parts that were copy-pasted across all
seven: the constructor, the ``init_db`` connection preamble (mkdir /
WAL / synchronous / optional FK pragma / shared ``_meta`` table) and the
no-op ``clear_caches``. Subclasses implement ``_create_schema`` (their
CREATE TABLE / INDEX / migration statements — committed by the base)
and optionally ``_post_init`` (post-commit backfills) or override
``clear_caches`` when they hold per-instance caches.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import ClassVar

from backend.eq2db import _meta as _meta_db


class BaseCatalogue:
    """Read (and build) access to one eq2db SQLite file."""

    #: Enable ``PRAGMA foreign_keys`` per connection — set True when the
    #: schema relies on ON DELETE CASCADE (aas, zones, raids).
    FOREIGN_KEYS: ClassVar[bool] = False

    #: Create the shared ``_meta`` provenance table in init_db. Every
    #: module uses it except classes.db (committed pre-populated, no
    #: download provenance to track).
    CREATE_META: ClassVar[bool] = True

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def init_db(self) -> sqlite3.Connection:
        """Create tables/indexes if missing. Returns an open connection.

        Template method: the connection preamble and commit live here;
        the module-specific schema comes from ``_create_schema`` and any
        post-commit backfills from ``_post_init``. ``:memory:`` is
        supported for tests (skips mkdir + WAL).
        """
        if str(self.path) == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        if self.FOREIGN_KEYS:
            conn.execute("PRAGMA foreign_keys = ON;")
        if self.CREATE_META:
            _meta_db.create_table(conn)
        self._create_schema(conn)
        conn.commit()
        self._post_init(conn)
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Module-specific CREATE TABLE / INDEX / migration statements.

        Runs inside init_db before the commit — don't commit here."""
        raise NotImplementedError

    def _post_init(self, conn: sqlite3.Connection) -> None:
        """Optional post-commit startup work (data backfills). Default: none."""

    def clear_caches(self) -> None:
        """Reset per-instance caches — used by tests and build scripts.

        Default: no caches. Subclasses holding caches override."""
