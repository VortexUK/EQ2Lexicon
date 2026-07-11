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
``clear_caches`` / ``_cache_info`` when they hold per-instance caches.

Dunder surface (uniform across every catalogue):

  * ``repr(cat)`` — class, path, provisioned-or-missing, cache sizes;
    safe to drop into any log line.
  * ``bool(cat)`` — True when the DB file exists (``if not catalogue:``
    reads as "not provisioned").
  * ``cat == other`` / ``hash(cat)`` — value semantics by (type, path).
  * ``os.fspath(cat)`` — catalogues are path-like; ``sqlite3.connect(cat)``
    and ``Path(cat)`` both work.
  * ``with cat as conn:`` — init_db + guaranteed close (nest-safe).
  * ``__init_subclass__`` — rejects a concrete subclass that forgets
    ``_create_schema`` at class-definition time, not first call.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from backend.eq2db import _meta as _meta_db

_log = logging.getLogger(__name__)


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
        # Connections opened via the context-manager protocol; a stack so
        # nested ``with cat as conn:`` blocks close their own connection.
        self._ctx_conns: list[sqlite3.Connection] = []

    def __init_subclass__(cls, **kwargs) -> None:
        """Fail at class-definition time when a subclass forgets
        ``_create_schema`` — earlier and clearer than the first
        ``init_db()`` call raising NotImplementedError at runtime."""
        super().__init_subclass__(**kwargs)
        if cls._create_schema is BaseCatalogue._create_schema:
            raise TypeError(f"{cls.__name__} must implement _create_schema(conn)")

    # ── Introspection / logging ──────────────────────────────────────────────

    def __repr__(self) -> str:
        """Debug/trace-friendly one-liner: class, path, whether the DB file
        is provisioned, and any per-instance cache sizes."""
        status = "ready" if self.path.exists() else "missing"
        caches = ", ".join(f"{k}={v}" for k, v in self._cache_info().items())
        extra = f", {caches}" if caches else ""
        return f"{type(self).__name__}(path={str(self.path)!r}, {status}{extra})"

    def _cache_info(self) -> dict[str, int]:
        """Cache-name → entry-count map rendered into ``repr``. Default: no
        caches. Subclasses holding caches override (see AACatalogue)."""
        return {}

    def __bool__(self) -> bool:
        """Truthiness = "is the DB file provisioned". Every read path
        already guards on ``self.path.exists()``; this gives callers and
        log statements the same check as ``if not catalogue:``."""
        return self.path.exists()

    # ── Value semantics ──────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        """Two catalogues are equal when they are the same class over the
        same path — handy in tests (``assert cat == RaidCatalogue(p)``)."""
        if not isinstance(other, BaseCatalogue):
            return NotImplemented
        return type(self) is type(other) and self.path == other.path

    def __hash__(self) -> int:
        """Hash by (type, path) to match ``__eq__``. Note: conftest
        re-points ``catalogue.path`` at session start — don't put a
        catalogue in a set/dict before mutating its path."""
        return hash((type(self), self.path))

    # ── Path-like protocol ───────────────────────────────────────────────────

    def __fspath__(self) -> str:
        """Catalogues are os.PathLike over their DB file: ``Path(cat)``,
        ``os.path.getsize(cat)`` and ``sqlite3.connect(cat)`` all work."""
        return str(self.path)

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def __enter__(self) -> sqlite3.Connection:
        """``with cat as conn:`` — open via init_db (schema guaranteed) and
        close on exit. Unlike ``with cat.init_db() as conn:`` (sqlite3's
        own CM, which commits but never closes), this releases the file
        handle. Nest-safe; not thread-safe on a shared instance."""
        conn = self.init_db()
        self._ctx_conns.append(conn)
        return conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self._ctx_conns.pop().close()

    # ── Read helpers ─────────────────────────────────────────────────────────

    def _fetchall(self, sql: str, params: Sequence | Mapping = ()) -> list[sqlite3.Row]:
        """Run one read query with Row factory; the connection is opened and
        closed per call. Returns [] when the DB file is missing or the table
        isn't built yet — eq2db read paths degrade gracefully on an
        unprovisioned DB rather than 500."""
        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            _log.exception("[eq2db] read failed on %r (unbuilt db?)", self)
            return []
        finally:
            conn.close()

    def _fetchone(self, sql: str, params: Sequence | Mapping = ()) -> sqlite3.Row | None:
        """Single-row variant of :meth:`_fetchall`. None on missing DB,
        unbuilt table, or no match."""
        if not self.path.exists():
            return None
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError:
            _log.exception("[eq2db] read failed on %r (unbuilt db?)", self)
            return None
        finally:
            conn.close()

    # ── DB lifecycle ─────────────────────────────────────────────────────────

    def init_db(self) -> sqlite3.Connection:
        """Create tables/indexes if missing. Returns an open connection.

        Template method: the connection preamble and commit live here;
        the module-specific schema comes from ``_create_schema`` and any
        post-commit backfills from ``_post_init``. ``:memory:`` is
        supported for tests (skips mkdir + WAL).
        """
        _log.debug("[eq2db] init_db %r", self)
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
