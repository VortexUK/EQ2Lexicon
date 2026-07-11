"""Persistent, deploy-surviving store of the last-known character + guild
lookups. The web request path serves from here (via the in-memory cache) and
never blocks on Census; background refreshes merge in fresh data "keep best
known" — a sparse Census response never nulls out good data.

All behaviour lives on :class:`CensusStore` (the catalogue convention — see
backend/db_catalogue.py): the shared module-level ``store`` instance is the
runtime entry point (consumers alias it ``census_store``); the get/upsert
helpers take an open conn (callers batch reads/writes per connection) and are
staticmethods. Tests construct ``CensusStore(tmp_db)``. SQL lives in the sibling
store.sql (schema_* + DML blocks).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

from backend.db_catalogue import BaseCatalogue
from backend.db_helpers import resolve_db_path
from backend.sql_loader import load_sql


class StoreRecord(TypedDict):
    """Envelope returned by ``get_character`` / ``get_guild`` / ``get_character_aas``.

    ``data`` is the original model_dump() dict stored as JSON; the caller
    deserialises field-by-field as needed. ``last_resolved_at`` is a Unix
    timestamp of when Census last responded successfully for this entity.
    """

    data: dict[str, Any]
    last_resolved_at: int


_log = logging.getLogger(__name__)


DB_PATH: Path = resolve_db_path("DB_CENSUS_PATH", "census", "census.db")

_SQL = load_sql(__file__)

_MIGRATIONS: list[str] = []  # future schema bumps appended here


class CensusStore(BaseCatalogue):
    """Read/write access to one census.db file (last-known Census lookups)."""

    # The three tables declare no foreign keys — the old init set the pragma
    # as boilerplate, not because anything relied on cascade.
    FOREIGN_KEYS = False

    # census.db predates the shared _meta table and has no build provenance
    # to track — rows carry their own timestamps.
    CREATE_META = False

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_characters"])
        conn.execute(_SQL["schema_guilds"])
        conn.execute(_SQL["schema_character_aas"])
        self._apply_migrations(conn, _MIGRATIONS)

    # ── Characters ───────────────────────────────────────────────────────────

    @staticmethod
    def upsert_character(
        conn: sqlite3.Connection,
        name: str,
        world: str,
        data: dict,
        *,
        resolved: bool,
        now: int | None = None,
    ) -> None:
        """Merge-store a character (keep best-known).

        When ``resolved`` is False the call is a no-op (never overwrite a good row
        with a sparse one, never insert a sparse first-sight row).

        When True and a row already exists, the incoming blob is **overlaid** onto
        the stored one field-by-field: keys present in ``data`` refresh the stored
        values, keys ``data`` omits are preserved. This is what stops a guild-roster
        overview (name/level/class/deity only — no equipment/stats) from nulling an
        individually-resolved character's gear on the next roster refresh. A partial
        write (one that carries no ``equipment`` key, i.e. hasn't re-resolved the
        full profile) also leaves ``last_resolved_at`` untouched so the character
        view's staleness clock stays honest and still triggers a real refresh."""
        if not resolved:
            return
        write_ts = int(time.time()) if now is None else now
        resolved_ts = write_ts

        existing = CensusStore.get_character(conn, name, world)
        if existing is not None:
            incoming_is_full = "equipment" in data
            data = {**existing["data"], **data}
            if not incoming_is_full:
                resolved_ts = existing["last_resolved_at"]  # sparse overlay — don't advance freshness

        conn.execute(
            _SQL["upsert_character"],
            (
                name.lower(),
                world,
                name,
                data.get("level"),
                data.get("guild_name"),
                json.dumps(data),
                resolved_ts,
                write_ts,
            ),
        )
        conn.commit()

    @staticmethod
    def get_character(conn: sqlite3.Connection, name: str, world: str) -> StoreRecord | None:
        """Return {data, last_resolved_at} or None."""
        row = conn.execute(_SQL["select_character"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {"data": json.loads(row[0]), "last_resolved_at": row[1]}

    # ── Guilds ───────────────────────────────────────────────────────────────

    @staticmethod
    def upsert_guild(conn: sqlite3.Connection, name: str, world: str, data: dict, *, now: int | None = None) -> None:
        """Store the guild roster blob (member names+ranks + info). Always replaces —
        the roster list is reliable from Census regardless of member login recency."""
        ts = int(time.time()) if now is None else now
        conn.execute(
            _SQL["upsert_guild"],
            (name.lower(), world, name, json.dumps(data), ts, ts),
        )
        conn.commit()

    @staticmethod
    def get_guild(conn: sqlite3.Connection, name: str, world: str) -> StoreRecord | None:
        row = conn.execute(_SQL["select_guild"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {"data": json.loads(row[0]), "last_resolved_at": row[1]}

    # ── Character AAs ────────────────────────────────────────────────────────

    @staticmethod
    def get_character_aas(conn: sqlite3.Connection, name: str, world: str) -> StoreRecord | None:
        """Return the persisted CharAAsResponse dict (or None) for (name, world).

        The record carries the model_dump() of the response plus a
        last_resolved_at unix timestamp."""
        row = conn.execute(_SQL["select_character_aas"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {
            "data": json.loads(row[0]),
            "last_resolved_at": row[1],
        }

    @staticmethod
    def upsert_character_aas(
        conn: sqlite3.Connection,
        name: str,
        world: str,
        data: dict,
        *,
        now: int | None = None,
    ) -> None:
        """Insert or update the (name, world) AA record. Always overwrites — AAs
        have no 'best-known merge' equivalent because the Census response is
        authoritative."""
        if now is None:
            now = int(time.time())
        conn.execute(_SQL["upsert_character_aas"], (name.lower(), world, json.dumps(data), now))
        conn.commit()


# The shared default instance — every runtime consumer goes through this.
store = CensusStore()
