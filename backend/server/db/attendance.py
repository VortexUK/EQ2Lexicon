"""users.db raid-attendance store (async aiosqlite).

Canonical merged attendance per guild raid night. Multiple officers run the
parser simultaneously and each POSTs cumulative snapshots; ``apply_snapshot``
folds them into ONE session via time-gap clustering, and the observation
upsert (min first_seen / max last_seen) is commutative so uploader arrival
order never matters.

Session identity: ``(world, guild_name, session_day, seq)`` where
session_day = the ISO date of ``started_at - 6h`` (evening rollover, matching
the availability calendar's date currency) and ``seq`` disambiguates genuine
double-headers (>3h apart). The ``scheduled`` flag + ``team_index`` are
frozen at ingest so later schedule edits never rewrite history.

Mirrors the favorites/raid_schedule domain pattern: per-call connections via
``AsyncStoreBase._db()``; tests re-point ``store.path`` via ALL_STORES.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

#: Snapshots within this gap of an existing session merge into it.
MERGE_GAP_S = 3 * 3600
#: A merge may never stretch a session past this span (runaway guard).
MAX_SESSION_SPAN_S = 16 * 3600
#: Evening rollover: session_day = date(started_at - 6h UTC).
ROLLOVER_S = 6 * 3600


def session_day_for(started_at: int) -> str:
    return datetime.fromtimestamp(started_at - ROLLOVER_S, tz=UTC).date().isoformat()


class AttendanceStore(AsyncStoreBase):
    """users.db `attendance` domain. Schema/migrations are owned by the
    package orchestrator (backend.server.db.init_db)."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def apply_snapshot(
        self,
        world: str,
        guild_name: str,
        discord_id: str,
        sent_at: int,
        raid_members: list[dict],
        online_guildies: list[dict],
        zones: list[str],
        scheduled: bool,
        team_index: int | None,
    ) -> dict:
        """The whole find-or-create + merge transaction. Members are
        pre-validated dicts {name, first_seen, last_seen}. ``scheduled`` /
        ``team_index`` are the ROUTE's schedule probe for this snapshot's
        window (the store stays free of raid_live imports)."""
        win_points = [m["first_seen"] for m in raid_members + online_guildies]
        win_points += [m["last_seen"] for m in raid_members + online_guildies]
        win_start = min(win_points) if win_points else sent_at
        win_end = max(win_points) if win_points else sent_at

        async with self._db(row_factory=True) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(
                        _SQL["select_overlapping_session"],
                        (world, guild_name, win_end + MERGE_GAP_S, win_start - MERGE_GAP_S),
                    )
                ).fetchone()

                merged = False
                if row is not None:
                    new_span = max(row["ended_at"], win_end) - min(row["started_at"], win_start)
                    if new_span <= MAX_SESSION_SPAN_S:
                        merged = True

                if merged and row is not None:
                    session_id = row["id"]
                    session_day = row["session_day"]
                    zone_set = set(json.loads(row["zones"] or "[]")) | set(zones)
                    uploaders = json.loads(row["uploaders"] or "{}")
                    uploaders[discord_id] = sent_at
                    await db.execute(
                        _SQL["merge_session_window"],
                        (
                            win_start,
                            win_end,
                            json.dumps(sorted(zone_set)),
                            json.dumps(uploaders),
                            1 if scheduled else 0,
                            team_index,
                            session_id,
                        ),
                    )
                else:
                    session_day = session_day_for(win_start)
                    seq_row = await (
                        await db.execute(_SQL["select_max_seq"], (world, guild_name, session_day))
                    ).fetchone()
                    seq = seq_row[0] if seq_row else 0  # aggregate always returns a row
                    cur = await db.execute(
                        _SQL["insert_session"],
                        (
                            world,
                            guild_name,
                            session_day,
                            seq,
                            win_start,
                            win_end,
                            json.dumps(sorted(set(zones))),
                            1 if scheduled else 0,
                            team_index,
                            json.dumps({discord_id: sent_at}),
                        ),
                    )
                    session_id = cur.lastrowid

                for kind, members in (("raid", raid_members), ("online", online_guildies)):
                    for m in members:
                        await db.execute(
                            _SQL["upsert_observation"],
                            (session_id, m["name"], kind, m["first_seen"], m["last_seen"]),
                        )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return {"session_id": session_id, "session_day": session_day, "merged": merged}

    async def find_live_session(self, world: str, guild_name: str, at: int) -> dict | None:
        """The session a snapshot at time ``at`` would merge into — the
        voice poller's "is a raid happening right now?" probe. None when
        nothing is within the merge gap."""
        async with self._db(row_factory=True) as db:
            async with db.execute(
                _SQL["select_live_session"],
                (world, guild_name, at + MERGE_GAP_S, at - MERGE_GAP_S),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def record_voice(self, session_id: int, discord_ids: Sequence[str], seen_at: int) -> None:
        """Record who was in the raid voice channel at ``seen_at`` — one
        kind='voice' observation per Discord id (the character_name column
        carries the id, as the schema reserves). The commutative MIN/MAX
        upsert makes repeated ticks extend last_seen only."""
        if not discord_ids:
            return
        async with self._db() as db:
            for did in discord_ids:
                await db.execute(_SQL["upsert_observation"], (session_id, did, "voice", seen_at, seen_at))
            await db.commit()

    async def list_sessions(
        self, world: str, guild_name: str, *, limit: int = 50, before_id: int | None = None
    ) -> list[dict]:
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_sessions"], (world, guild_name, before_id, before_id, limit)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def get_session(self, session_id: int) -> dict | None:
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_session"], (session_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def observations_for_session(self, session_id: int) -> list[dict]:
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_observations"], (session_id,)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def observations_for_sessions(self, session_ids: list[int]) -> dict[int, list[dict]]:
        if not session_ids:
            return {}
        placeholders = ",".join("?" * len(session_ids))
        out: dict[int, list[dict]] = {sid: [] for sid in session_ids}
        async with self._db(row_factory=True) as db:
            sql = _SQL["select_observations_many"].format(placeholders=placeholders)
            async with db.execute(sql, session_ids) as cur:
                for r in await cur.fetchall():
                    out[r["session_id"]].append(dict(r))
        return out

    # ── Officer corrections ──────────────────────────────────────────────────

    async def set_override(self, session_id: int, character_name: str, category: str, set_by: str) -> None:
        """Pin a category onto one character for one session (the derivation
        applies it last). Upserts — re-correcting replaces."""
        async with self._db() as db:
            await db.execute(_SQL["upsert_override"], (session_id, character_name, category, set_by))
            await db.commit()

    async def clear_override(self, session_id: int, character_name: str) -> bool:
        """Remove a correction — the derived category comes back."""
        async with self._db() as db:
            cur = await db.execute(_SQL["delete_override"], (session_id, character_name))
            await db.commit()
            return cur.rowcount > 0

    async def overrides_for_session(self, session_id: int) -> dict[str, dict]:
        """{character_name_lower: {character_name, category, set_by, set_at}}."""
        async with self._db(row_factory=True) as db:
            async with db.execute(_SQL["select_overrides"], (session_id,)) as cur:
                return {r["character_name"].lower(): dict(r) for r in await cur.fetchall()}

    async def overrides_for_sessions(self, session_ids: list[int]) -> dict[int, dict[str, dict]]:
        if not session_ids:
            return {}
        placeholders = ",".join("?" * len(session_ids))
        out: dict[int, dict[str, dict]] = {sid: {} for sid in session_ids}
        async with self._db(row_factory=True) as db:
            sql = _SQL["select_overrides_many"].format(placeholders=placeholders)
            async with db.execute(sql, session_ids) as cur:
                for r in await cur.fetchall():
                    out[r["session_id"]][r["character_name"].lower()] = dict(r)
        return out

    async def remove_character(self, session_id: int, character_name: str) -> bool:
        """Officer row removal for junk names: the character's raid/online
        observations and any override go together. Voice rows (discord-id
        keyed) are untouched. True when anything was deleted."""
        async with self._db() as db:
            cur1 = await db.execute(_SQL["delete_character_observations"], (session_id, character_name))
            cur2 = await db.execute(_SQL["delete_override"], (session_id, character_name))
            await db.commit()
            return cur1.rowcount > 0 or cur2.rowcount > 0

    async def delete_session(self, session_id: int) -> bool:
        async with self._db() as db:
            await db.execute(_SQL["delete_observations_for_session"], (session_id,))
            await db.execute(_SQL["delete_overrides_for_session"], (session_id,))
            cur = await db.execute(_SQL["delete_session"], (session_id,))
            await db.commit()
            return cur.rowcount > 0


# The shared default instance — every runtime consumer goes through this.
store = AttendanceStore()
