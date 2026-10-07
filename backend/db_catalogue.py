"""Shared base class for SQLite catalogue/store classes.

Originally extracted from the eq2db data modules; now the base for every
per-file SQLite data interface in the codebase (eq2db catalogues, the
census store, ...). Every data module under ``backend/eq2db/`` exposes one
catalogue class
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
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    import aiosqlite

from backend.db_helpers import like_escape
from backend.eq2db import _meta as _meta_db

_log = logging.getLogger(__name__)


def _is_unbuilt_schema(exc: sqlite3.OperationalError) -> bool:
    """True for the "DB file exists but the schema hasn't been created yet"
    class of errors the read helpers degrade gracefully on (fresh volume,
    pre-seeded stub file). Anything else — locked database, disk I/O, SQL
    syntax — is a real fault and must propagate."""
    msg = str(exc)
    return "no such table" in msg or "no such column" in msg


class PathBound:
    """The path-bound identity + dunder surface shared by every SQLite data
    interface: sync catalogues/stores (:class:`BaseCatalogue`) and the async
    users.db domain stores (:class:`AsyncStoreBase`)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

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
        if not isinstance(other, PathBound):
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

    def clear_caches(self) -> None:
        """Reset per-instance caches — used by tests and build scripts.

        Default: no caches. Subclasses holding caches override."""


class BaseCatalogue(PathBound):
    """Read (and build) access to one SQLite file via synchronous sqlite3."""

    #: Enable ``PRAGMA foreign_keys`` per connection — set True when the
    #: schema relies on ON DELETE CASCADE (aas, zones, raids).
    FOREIGN_KEYS: ClassVar[bool] = False

    #: Create the shared ``_meta`` provenance table in init_db. Every
    #: module uses it except classes.db (committed pre-populated, no
    #: download provenance to track).
    CREATE_META: ClassVar[bool] = True

    def __init__(self, path: Path) -> None:
        super().__init__(path)
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
        unprovisioned DB rather than 500. Other OperationalErrors (locked DB,
        disk I/O) propagate: a transient failure must surface as an error,
        not be served — and possibly cached — as an empty result."""
        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if not _is_unbuilt_schema(exc):
                raise
            _log.warning("[db-catalogue] read on unbuilt db %r: %s", self, exc)
            return []
        finally:
            conn.close()

    def _fetchone(self, sql: str, params: Sequence | Mapping = ()) -> sqlite3.Row | None:
        """Single-row variant of :meth:`_fetchall`. None on missing DB,
        unbuilt table, or no match; other OperationalErrors propagate."""
        if not self.path.exists():
            return None
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError as exc:
            if not _is_unbuilt_schema(exc):
                raise
            _log.warning("[db-catalogue] read on unbuilt db %r: %s", self, exc)
            return None
        finally:
            conn.close()

    def _find_exact_then_like(self, exact_sql: str, like_sql: str, name: str) -> list[sqlite3.Row]:
        """The shared name-search protocol: exact lowercased match first, then
        a LIKE fallback with user wildcards escaped (BE-006) — both queries on
        ONE connection. ``exact_sql`` takes the lowercased name; ``like_sql``
        takes the escaped %-wrapped pattern with ``ESCAPE '\\'`` semantics."""
        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(exact_sql, (name.lower(),)).fetchall()
            if not rows:
                rows = conn.execute(like_sql, (f"%{like_escape(name.lower())}%",)).fetchall()
            return rows
        except sqlite3.OperationalError as exc:
            if not _is_unbuilt_schema(exc):
                raise
            _log.warning("[db-catalogue] read on unbuilt db %r: %s", self, exc)
            return []
        finally:
            conn.close()

    # ── DB lifecycle ─────────────────────────────────────────────────────────

    def init_db(self) -> sqlite3.Connection:
        """Create tables/indexes if missing. Returns an open connection.

        Template method: the connection preamble and commit live here;
        the module-specific schema comes from ``_create_schema`` and any
        post-commit backfills from ``_post_init``.

        ``:memory:`` is deliberately NOT supported: every read helper opens
        its own connection against ``self.path``, so a memory DB would be a
        fresh empty database per read — tests use a tmp_path file instead.
        """
        _log.debug("[db-catalogue] init_db %r", self)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        # Wait out transient writer contention (raid-night ingest bursts vs
        # background backfills) instead of raising "database is locked" —
        # supersedes sqlite3.connect's 5 s default.
        conn.execute("PRAGMA busy_timeout = 15000;")
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

    def _apply_migrations(self, conn: sqlite3.Connection, stmts: Sequence[str]) -> None:
        """Run idempotent ALTER-style migrations, skipping already-applied
        ones. The skip is logged at DEBUG (init_db runs per request on some
        routes) so a genuinely broken statement — which raises the same
        OperationalError as a duplicate column — at least leaves a trace,
        unlike the silent per-store `pass` loops this replaces."""
        for stmt in stmts:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                _log.debug("[db-catalogue] migration skipped on %r (already applied?): %s", self, exc)

    def _post_init(self, conn: sqlite3.Connection) -> None:
        """Optional post-commit startup work (data backfills). Default: none."""


class AsyncStoreBase(PathBound):
    """Base for the aiosqlite-backed users.db domain stores.

    Unlike :class:`BaseCatalogue`, these stores do NOT own their schema:
    the whole users.db family shares one file whose tables/migrations are
    orchestrated by ``backend.server.db.init_db()`` at startup. Each domain
    method opens its own connection via :meth:`_db` — exactly the per-call
    transaction shape the old free functions had, minus the
    ``path: Path = DB_PATH`` threading.

    (ServersStore, the one synchronous domain store, inherits
    :class:`PathBound` directly — it must never grow aiosqlite methods.)
    """

    @asynccontextmanager
    async def _db(self, *, row_factory: bool = False) -> AsyncIterator[aiosqlite.Connection]:
        """The one place a users.db domain connection is opened — connection
        -level policy lands here, not at N call sites (a foreign_keys pragma
        stays pending a data audit). ``row_factory=True`` sets aiosqlite.Row
        for dict-shaped reads.

        busy_timeout: a raid-night burst of parser uploads means concurrent
        writers on users.db (token last-used touches, attendance upserts).
        Without a busy handler a deferred-transaction write upgrade can fail
        with "database is locked" the moment another writer has committed —
        seen live 2026-09-12 as 500s on /attendance/ingest. 10s queues
        writers instead."""
        import aiosqlite  # deferred: sync-only consumers never pay the import

        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            if row_factory:
                db.row_factory = aiosqlite.Row
            yield db


# ---------------------------------------------------------------------------
# Postgres bases — the migrated families (users / parses / census / zones /
# raids). One database, one schema per family; connections come from
# backend.pg (pooled under the app lifespan, direct otherwise) and select
# their family schema via search_path at checkout, so every query in the
# .sql sidecars stays unqualified. Schema DDL is owned by db/migrations/
# via backend.pg_migrate — stores never create tables.
# ---------------------------------------------------------------------------


class SchemaBound:
    """Schema-name identity for Postgres stores — the migrated analog of
    :class:`PathBound`. Tests re-point ``store.schema`` at leased scratch
    schemas exactly as they re-pointed ``store.path`` at tmp files. No
    ``__fspath__`` (nothing to open as a file) and no exists-degrade:
    migrations run before the app serves, so a missing relation is a real
    fault, never a soft empty result."""

    def __init__(self, schema: str) -> None:
        self.schema = schema

    def __repr__(self) -> str:  # pragma: no cover — debugging nicety
        return f"<{type(self).__name__} schema={self.schema}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and type(other) is type(self) and other.schema == self.schema

    def __hash__(self) -> int:
        return hash((type(self), self.schema))

    def clear_caches(self) -> None:
        """Reset per-instance caches — the uniform catalogue surface
        (mirrors :meth:`PathBound.clear_caches`). Default: no caches;
        subclasses holding caches override."""


class PgStoreBase(SchemaBound):
    """Base for the async (formerly aiosqlite) users-family domain stores.

    ``_db()`` keeps its name and contextmanager shape so store methods port
    with minimal churn; rows are ALWAYS dict-shaped (psycopg dict_row — the
    ``row_factory`` kwarg survives for signature compatibility and is
    ignored). psycopg commits on clean ``async with`` exit and rolls back
    on exception, so the explicit ``await db.commit()`` calls inside store
    methods remain correct (and make the commit point explicit)."""

    @asynccontextmanager
    async def _db(self, *, row_factory: bool = True) -> AsyncIterator[Any]:
        from backend import pg  # deferred: sync-only consumers never pay the import

        async with pg.aconnection() as conn:
            await conn.execute(pg.search_path_sql(self.schema))
            yield conn


class PgConnProxy:
    """A caller-owned Postgres connection scoped to one family schema.

    Mimics the sqlite3.Connection surface the catalogue callers actually
    use — ``execute``/``executemany``/``commit``/``rollback``/``close``,
    plus the sqlite transaction-scope ``with`` shape (commit on clean
    exit, rollback on exception, THEN return to the pool — sqlite's
    ``with`` kept the handle open, but no converted caller reuses it).
    ``close()`` returns the connection to the pool rather than killing a
    pooled connection. Rows are dicts (dict_row)."""

    def __init__(self, schema: str) -> None:
        from backend import pg

        self._pg = pg
        self._conn: Any = pg.getconn()
        self._conn.execute(pg.search_path_sql(schema))

    def execute(self, sql: str, params: Any = None) -> Any:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: Any) -> Any:
        cur = self._conn.cursor()
        cur.executemany(sql, params_seq)
        return cur

    def cursor(self) -> Any:
        return self._conn.cursor()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            # End any implicit read transaction before returning to the pool
            # — otherwise psycopg_pool logs a rollback WARNING per checkout.
            # Writers commit explicitly; close-without-commit rolls back,
            # exactly like sqlite3.Connection.close() did.
            try:
                conn.rollback()
            except Exception:  # pragma: no cover — broken conn; pool discards
                pass
            self._pg.putconn(conn)

    def __enter__(self) -> PgConnProxy:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            if self._conn is not None:
                if exc_type is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
        finally:
            self.close()


class PgCatalogue(SchemaBound):
    """Sync Postgres analogue of :class:`BaseCatalogue` for the
    caller-owns-connection families (parses, census, zones, raids) and the
    Phase-2 catalogue mirrors (items, spells, recipes).

    ``init_db()`` KEEPS ITS NAME but creates nothing — schema DDL is owned
    by db/migrations/. It returns an open :class:`PgConnProxy` scoped to
    ``self.schema``, preserving the ``conn = store.init_db(); …;
    conn.close()`` caller shape across ~60 sites."""

    #: Table probed by :meth:`ready` — the catalogue's primary table.
    #: Subclasses that serve route-level "is this catalogue loaded?"
    #: checks set it; the default disables the probe (always ready).
    READY_TABLE: ClassVar[str | None] = None

    def __init__(self, schema: str) -> None:
        super().__init__(schema)
        self._ready: bool | None = None

    def init_db(self) -> PgConnProxy:
        return PgConnProxy(self.schema)

    @staticmethod
    def fetchval(cur: Any) -> Any:
        """First column of the first row (or None) — the dict-row
        replacement for sqlite's positional ``fetchone()[0]``."""
        row = cur.fetchone()
        if row is None:
            return None
        return next(iter(row.values()))

    # ── Read helpers (the BaseCatalogue trio, pooled) ────────────────────────

    def _fetchall(self, sql: str, params: Sequence | Mapping = ()) -> list[dict]:
        """One read query on a pooled schema-scoped connection. Rows are
        dicts. Unlike the SQLite base there is no missing-file soft-empty:
        migrations run before the app serves, so errors are real faults.
        Route-level degradation goes through :meth:`ready` instead."""
        from backend import pg  # deferred: SQLite-only consumers never pay the import

        with pg.connection() as conn:
            conn.execute(pg.search_path_sql(self.schema))
            return conn.execute(sql, params or None).fetchall()

    def _fetchone(self, sql: str, params: Sequence | Mapping = ()) -> dict | None:
        """Single-row variant of :meth:`_fetchall`."""
        from backend import pg

        with pg.connection() as conn:
            conn.execute(pg.search_path_sql(self.schema))
            return conn.execute(sql, params or None).fetchone()

    def _find_exact_then_like(self, exact_sql: str, like_sql: str, name: str) -> list[dict]:
        """The shared name-search protocol: exact lowercased match first,
        then a LIKE fallback with user wildcards escaped (BE-006) — both
        queries on ONE connection."""
        from backend import pg

        with pg.connection() as conn:
            conn.execute(pg.search_path_sql(self.schema))
            rows = conn.execute(exact_sql, (name.lower(),)).fetchall()
            if not rows:
                rows = conn.execute(like_sql, (f"%{like_escape(name.lower())}%",)).fetchall()
            return rows

    # ── Load-state probe (replaces the SQLite ``DB_PATH.exists()`` guards) ──

    def ready(self) -> bool:
        """True once :attr:`READY_TABLE` holds at least one row. An empty
        or not-yet-migrated schema reads as not-ready, so routes keep the
        503 degradation the file-exists guards used to give. The True
        result is cached (reference data never empties at runtime);
        not-ready re-probes every call until the load lands."""
        if self.READY_TABLE is None:
            return True
        if self._ready:
            return True
        import psycopg

        try:
            row = self._fetchone(f'SELECT 1 AS one FROM "{self.READY_TABLE}" LIMIT 1')
        except psycopg.errors.UndefinedTable:
            return False
        self._ready = row is not None
        return bool(self._ready)

    def clear_caches(self) -> None:
        super().clear_caches()
        self._ready = None

    # ── `_meta` (download provenance / resume offsets), PG flavour ──────────

    def get_meta(self, conn: Any, key: str) -> str | None:
        row = conn.execute("SELECT value FROM _meta WHERE key = %s", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, conn: Any, key: str, value: str) -> None:
        """Upsert ``(key, value)``. Commits immediately — meta writes are
        one-shot and don't compose into larger transactions (matches the
        zones-family module-level helper's semantics)."""
        conn.execute(
            "INSERT INTO _meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
