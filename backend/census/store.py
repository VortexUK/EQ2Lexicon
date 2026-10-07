"""Persistent, deploy-surviving store of the last-known character + guild
lookups (the ``census`` Postgres schema). The web request path serves from
here (via the in-memory cache) and never blocks on Census; background
refreshes merge in fresh data "keep best known" — a sparse Census response
never nulls out good data.

All behaviour lives on :class:`CensusStore` (the catalogue convention — see
backend/db_catalogue.py): the shared module-level ``store`` instance is the
runtime entry point (consumers alias it ``census_store``); the get/upsert
helpers take an open conn (callers batch reads/writes per connection) and are
staticmethods. ``init_db()`` returns a pooled schema-scoped connection proxy;
schema DDL lives in db/migrations/0003_census.sql. ``data_json`` is jsonb —
psycopg hands back parsed dicts and writes go through ``Json()``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TypedDict

from psycopg.types.json import Json

from backend.db_catalogue import PgCatalogue
from backend.sql_loader import load_sql


class StoreRecord(TypedDict):
    """Envelope returned by ``get_character`` / ``get_guild`` / ``get_character_aas``.

    ``data`` is the original model_dump() dict stored as jsonb; the caller
    deserialises field-by-field as needed. ``last_resolved_at`` is a Unix
    timestamp of when Census last responded successfully for this entity.
    """

    data: dict[str, Any]
    last_resolved_at: int


class GuildHistorySnapshot(TypedDict, total=False):
    """The numbers one guild refresh contributes to ``guild_history`` — all
    optional because a sparse Census info blob leaves some unknown."""

    level: int | None
    members: int | None
    accounts: int | None
    achievement_count: int | None
    max_level_members: int | None
    distinct_classes: int | None


class GuildHistoryPoint(GuildHistorySnapshot):
    """One stored ``guild_history`` row: the snapshot plus its day key."""

    day: str
    captured_at: int


_log = logging.getLogger(__name__)

_SQL = load_sql(__file__)

#: Postgres schema the census family lives in (db/migrations/0003_census.sql).
SCHEMA = "census"


class CensusStore(PgCatalogue):
    """Read/write access to the census schema (last-known Census lookups)."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    # ── Characters ───────────────────────────────────────────────────────────

    @staticmethod
    def upsert_character(
        conn: Any,
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
                Json(data),
                resolved_ts,
                write_ts,
            ),
        )
        conn.commit()

    @staticmethod
    def get_character(conn: Any, name: str, world: str) -> StoreRecord | None:
        """Return {data, last_resolved_at} or None."""
        row = conn.execute(_SQL["select_character"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {"data": row["data_json"], "last_resolved_at": row["last_resolved_at"]}

    @staticmethod
    def get_characters(conn: Any, names: list[str], world: str) -> dict[str, dict]:
        """One round trip for many names: ``{name_lower: data}`` for every
        stored character among ``names`` on ``world`` (absent names omitted)."""
        if not names:
            return {}
        lowers = sorted({n.lower() for n in names})
        rows = conn.execute(_SQL["select_characters_bulk"], (world, lowers)).fetchall()
        return {row["name_lower"]: row["data_json"] for row in rows}

    @staticmethod
    def _like_prefix(prefix: str) -> str:
        """Escape LIKE wildcards in user input, then anchor as a prefix."""
        return prefix.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    @staticmethod
    def search_characters(conn: Any, prefix: str, world: str, limit: int = 20) -> list[dict]:
        """Name-prefix search over every character this server has seen
        (guild-roster merges pull whole guilds in) — the instant half of
        /characters/search. Returns [{name, level, guild_name, cls}].
        ``cls`` comes straight from the jsonb blob in SQL."""
        rows = conn.execute(
            _SQL["search_characters_by_prefix"], (world, CensusStore._like_prefix(prefix), limit)
        ).fetchall()
        return [{"name": r["name"], "level": r["level"], "guild_name": r["guild_name"], "cls": r["cls"]} for r in rows]

    @staticmethod
    def search_guilds(conn: Any, prefix: str, world: str, limit: int = 20) -> list[str]:
        """Name-prefix search over every guild this server has seen."""
        rows = conn.execute(
            _SQL["search_guilds_by_prefix"], (world, CensusStore._like_prefix(prefix), limit)
        ).fetchall()
        return [r["name"] for r in rows]

    # ── Guilds ───────────────────────────────────────────────────────────────

    @staticmethod
    def upsert_guild(conn: Any, name: str, world: str, data: dict, *, now: int | None = None) -> None:
        """Store the guild roster blob (member names+ranks + info). Always replaces —
        the roster list is reliable from Census regardless of member login recency."""
        ts = int(time.time()) if now is None else now
        conn.execute(
            _SQL["upsert_guild"],
            (name.lower(), world, name, Json(data), ts, ts),
        )
        conn.commit()

    @staticmethod
    def get_guild(conn: Any, name: str, world: str) -> StoreRecord | None:
        row = conn.execute(_SQL["select_guild"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {"data": row["data_json"], "last_resolved_at": row["last_resolved_at"]}

    # ── Guild history ────────────────────────────────────────────────────────

    @staticmethod
    def _utc_day(ts: int) -> str:
        """``YYYY-MM-DD`` of a Unix timestamp in UTC — the guild_history day key."""
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")

    @staticmethod
    def upsert_guild_history(
        conn: Any,
        name: str,
        world: str,
        snapshot: GuildHistorySnapshot,
        *,
        now: int | None = None,
        retention_days: int,
    ) -> None:
        """Write today's (UTC) history row for a guild, replacing an earlier
        capture from the same day, then prune this guild's rows older than
        ``retention_days``. One call per successful guild refresh."""
        ts = int(time.time()) if now is None else now
        key = (world, name.lower())
        conn.execute(
            _SQL["upsert_guild_history"],
            (
                *key,
                CensusStore._utc_day(ts),
                ts,
                snapshot.get("level"),
                snapshot.get("members"),
                snapshot.get("accounts"),
                snapshot.get("achievement_count"),
                snapshot.get("max_level_members"),
                snapshot.get("distinct_classes"),
            ),
        )
        conn.execute(_SQL["prune_guild_history"], (*key, CensusStore._utc_day(ts - retention_days * 86400)))
        conn.commit()

    @staticmethod
    def get_guild_history(
        conn: Any, name: str, world: str, days: int, *, now: int | None = None
    ) -> list[GuildHistoryPoint]:
        """The guild's daily rows from ``days`` days ago (UTC) to today, oldest
        first. Empty list for a guild with no history."""
        ts = int(time.time()) if now is None else now
        since = CensusStore._utc_day(ts - days * 86400)
        rows = conn.execute(_SQL["select_guild_history"], (world, name.lower(), since)).fetchall()
        return [
            {
                "day": r["day"],
                "captured_at": r["captured_at"],
                "level": r["level"],
                "members": r["members"],
                "accounts": r["accounts"],
                "achievement_count": r["achievement_count"],
                "max_level_members": r["max_level_members"],
                "distinct_classes": r["distinct_classes"],
            }
            for r in rows
        ]

    @staticmethod
    def latest_guild_member_counts(conn: Any, world: str, names_lower: Iterable[str]) -> dict[str, int]:
        """Latest-known member count for each requested guild, keyed by
        name_lower; guilds with no history rows are simply absent. One
        DISTINCT ON pass over guild_history. Feeds the recruiting browse
        cards."""
        wanted = set(names_lower)
        if not wanted:
            return {}
        out: dict[str, int] = {}
        for r in conn.execute(_SQL["select_latest_member_counts"], (world,)).fetchall():
            if r["name_lower"] in wanted and r["members"] is not None:
                out[r["name_lower"]] = int(r["members"])
        return out

    # ── Character AAs ────────────────────────────────────────────────────────

    @staticmethod
    def get_character_aas(conn: Any, name: str, world: str) -> StoreRecord | None:
        """Return the persisted CharAAsResponse dict (or None) for (name, world).

        The record carries the model_dump() of the response plus a
        last_resolved_at unix timestamp."""
        row = conn.execute(_SQL["select_character_aas"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {"data": row["data_json"], "last_resolved_at": row["last_resolved_at"]}

    @staticmethod
    def upsert_character_aas(
        conn: Any,
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
        conn.execute(_SQL["upsert_character_aas"], (name.lower(), world, Json(data), now))
        conn.commit()

    # ── Character gear sets ──────────────────────────────────────────────────

    @staticmethod
    def get_character_gear_sets(conn: Any, name: str, world: str) -> StoreRecord | None:
        """The persisted gear-sets response dict (or None) for (name, world)."""
        row = conn.execute(_SQL["select_character_gear_sets"], (name.lower(), world)).fetchone()
        if row is None:
            return None
        return {"data": row["data_json"], "last_resolved_at": row["last_resolved_at"]}

    @staticmethod
    def upsert_character_gear_sets(
        conn: Any,
        name: str,
        world: str,
        data: dict,
        *,
        now: int | None = None,
    ) -> None:
        """Insert or update the (name, world) gear-sets record. Always
        overwrites — like AAs there is no best-known merge; the Census
        response is authoritative."""
        if now is None:
            now = int(time.time())
        conn.execute(
            _SQL["upsert_character_gear_sets"],
            (name.lower(), world, Json(data), now),
        )
        conn.commit()


# The shared default instance — every runtime consumer goes through this.
store = CensusStore()
