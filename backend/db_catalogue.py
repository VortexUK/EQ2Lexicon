"""Shared base classes for the Postgres catalogue/store families (one schema
per family, selected via ``SET search_path`` at checkout so the ``.sql``
sidecars stay unqualified). DDL is owned by ``db/migrations/`` — stores never
create tables.

Every data module exposes one catalogue/store class following the same
convention:

  * the schema name lives on the instance (``self.schema``); a shared
    module-level ``catalogue`` / ``store`` instance is the runtime entry
    point, and tests lease a scratch schema (``tests/fixtures/pg.py``) and
    re-point ``instance.schema``;
  * pure domain helpers are staticmethods with their full bodies in the
    class; DB reads are instance methods; conn-taking write helpers take
    the caller-owned connection (callers own the transaction).

The bases here:

  * :class:`SchemaBound` — schema-name identity + the uniform
    ``clear_caches`` surface.
  * :class:`PgStoreBase` — async domain stores (the users family);
    per-call connections via ``_db()``.
  * :class:`PgCatalogue` — sync catalogues/stores; ``init_db()`` returns a
    caller-owned :class:`PgConnProxy`, and the ``_fetchall`` /
    ``_fetchone`` / ``_find_exact_then_like`` read trio runs each query on
    a pooled schema-scoped connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, ClassVar

from backend.db_helpers import like_escape


class SchemaBound:
    """Schema-name identity shared by every Postgres store/catalogue.
    Tests re-point ``store.schema`` at leased scratch schemas. There is no
    exists-degrade: migrations run before the app serves, so a missing
    relation is a real fault, never a soft empty result."""

    def __init__(self, schema: str) -> None:
        self.schema = schema

    def __repr__(self) -> str:  # pragma: no cover — debugging nicety
        return f"<{type(self).__name__} schema={self.schema}>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and type(other) is type(self) and other.schema == self.schema

    def __hash__(self) -> int:
        return hash((type(self), self.schema))

    def clear_caches(self) -> None:
        """Reset per-instance caches — the uniform catalogue surface used
        by tests and build scripts. Default: no caches; subclasses holding
        caches override."""


class PgStoreBase(SchemaBound):
    """Base for the async users-family domain stores.

    ``_db()`` yields a per-call pooled connection scoped to ``self.schema``;
    rows are ALWAYS dict-shaped (psycopg dict_row — the ``row_factory``
    kwarg survives for signature compatibility and is ignored). psycopg
    commits on clean ``async with`` exit and rolls back on exception, so
    the explicit ``await db.commit()`` calls inside store methods remain
    correct (and make the commit point explicit)."""

    @asynccontextmanager
    async def _db(self, *, row_factory: bool = True, autocommit: bool = False) -> AsyncIterator[Any]:
        from backend import pg  # deferred: sync-only consumers never pay the import

        async with pg.aconnection(self.schema, autocommit=autocommit) as conn:
            yield conn

    def _read(self) -> Any:
        """``_db()`` for a method that only SELECTs: autocommit, so the call
        is one round trip instead of BEGIN + query + COMMIT. Never use it
        for a block that must be atomic across statements."""
        return self._db(autocommit=True)


class PgConnProxy:
    """A caller-owned Postgres connection scoped to one family schema.

    Mimics the connection surface the catalogue callers actually use —
    ``execute``/``executemany``/``commit``/``rollback``/``close``, plus the
    transaction-scope ``with`` shape (commit on clean exit, rollback on
    exception, THEN return to the pool). ``close()`` returns the connection
    to the pool rather than killing a pooled connection. Rows are dicts
    (dict_row)."""

    def __init__(self, schema: str) -> None:
        from backend import pg

        self._pg = pg
        self._conn: Any = pg.getconn(schema)

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
            # Writers commit explicitly; close-without-commit rolls back.
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
    """Base for the sync caller-owns-connection families (parses, census,
    zones, raids) and the reference catalogues (items, spells, recipes,
    classes, aas).

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
        replacement for positional ``fetchone()[0]``."""
        row = cur.fetchone()
        if row is None:
            return None
        return next(iter(row.values()))

    # ── Read helpers (one pooled schema-scoped connection per call) ─────────

    def _fetchall(self, sql: str, params: Sequence | Mapping = ()) -> list[dict]:
        """One read query on a pooled schema-scoped connection. Rows are
        dicts. There is no missing-file soft-empty: migrations run before
        the app serves, so errors are real faults. Route-level degradation
        goes through :meth:`ready` instead."""
        from backend import pg  # deferred: import-time consumers never pay for a DSN

        with pg.connection(self.schema, autocommit=True) as conn:
            return conn.execute(sql, params or None).fetchall()

    def _fetchone(self, sql: str, params: Sequence | Mapping = ()) -> dict | None:
        """Single-row variant of :meth:`_fetchall`."""
        from backend import pg

        with pg.connection(self.schema, autocommit=True) as conn:
            return conn.execute(sql, params or None).fetchone()

    def _find_exact_then_like(self, exact_sql: str, like_sql: str, name: str) -> list[dict]:
        """The shared name-search protocol: exact lowercased match first,
        then a LIKE fallback with user wildcards escaped — both
        queries on ONE connection."""
        from backend import pg

        with pg.connection(self.schema, autocommit=True) as conn:
            rows = conn.execute(exact_sql, (name.lower(),)).fetchall()
            if not rows:
                rows = conn.execute(like_sql, (f"%{like_escape(name.lower())}%",)).fetchall()
            return rows

    # ── Load-state probe (route-level "is this catalogue loaded?") ──────────

    def ready(self) -> bool:
        """True once :attr:`READY_TABLE` holds at least one row. An empty
        or not-yet-migrated schema reads as not-ready, so routes keep the
        503 degradation. The True result is cached (reference data never
        empties at runtime); not-ready re-probes every call until the load
        lands."""
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

    # ── `_meta` (download provenance / resume offsets) ───────────────────────

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
