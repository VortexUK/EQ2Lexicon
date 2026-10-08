"""users-schema raid-attendance store (psycopg).

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

Mirrors the guild_settings/raid_schedule domain pattern: pooled per-call
connections via ``PgStoreBase._db()``; tests re-point ``store.schema`` via
ALL_STORES.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime

from psycopg.types.json import Json

from backend.db_catalogue import PgStoreBase
from backend.server.db import SCHEMA
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

#: Snapshots within this gap of an existing session merge into it.
MERGE_GAP_S = 3 * 3600
#: A merge may never stretch a session past this span (runaway guard).
MAX_SESSION_SPAN_S = 16 * 3600
#: Evening rollover: session_day = date(started_at - 6h UTC).
ROLLOVER_S = 6 * 3600
#: A merge only widens the session window when the snapshot shows a real
#: raid in progress (at least this many raid members). Anything smaller
#: (an overnight parser uploading online guildies, a stray 6-man) still
#: merges its OBSERVATIONS but cannot stretch a finished raid — otherwise
#: a chain of online-only merges walks a session up to the runaway cap.
MIN_RAID_FOR_WINDOW = 12


def session_day_for(started_at: int) -> str:
    return datetime.fromtimestamp(started_at - ROLLOVER_S, tz=UTC).date().isoformat()


class AttendanceStore(PgStoreBase):
    """users-schema `attendance` domain. Schema DDL is owned by
    db/migrations/0001_users.sql; methods check out pooled connections
    scoped to ``self.schema``."""

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

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
        raid_points = [m["first_seen"] for m in raid_members] + [m["last_seen"] for m in raid_members]
        all_points = raid_points + [p for m in online_guildies for p in (m["first_seen"], m["last_seen"])]
        obs_start = min(all_points) if all_points else sent_at
        obs_end = max(all_points) if all_points else sent_at
        # The session WINDOW follows the raid — online guildies at 3am are
        # night owls, not raiders. Online-only snapshots fall back to their
        # observation window for session CREATION only.
        win_start = min(raid_points) if raid_points else obs_start
        win_end = max(raid_points) if raid_points else obs_end
        extend = len(raid_members) >= MIN_RAID_FOR_WINDOW

        async with self._db(row_factory=True) as db:
            try:
                # psycopg's implicit transaction opens at this first execute.
                # The advisory xact lock (auto-released at commit/rollback)
                # serialises concurrent snapshot folds per (world, guild)
                # — two uploaders can't both miss the other's freshly inserted
                # session and double-create.
                await db.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"attendance:{world.lower()}:{guild_name.lower()}",),
                )
                # Merge matching uses the full observation window so overnight
                # online rows still land in the right session…
                row = await (
                    await db.execute(
                        _SQL["select_overlapping_session"],
                        (world, guild_name, obs_end + MERGE_GAP_S, obs_start - MERGE_GAP_S),
                    )
                ).fetchone()

                merged = False
                if row is not None:
                    if extend:
                        new_span = max(row["ended_at"], win_end) - min(row["started_at"], win_start)
                        merged = new_span <= MAX_SESSION_SPAN_S
                    else:
                        # …but only a real raid may widen the window, so a
                        # non-extending merge can never blow the span cap.
                        merged = True

                if merged and row is not None:
                    session_id = row["id"]
                    session_day = row["session_day"]
                    # zones/uploaders are jsonb — psycopg hands back the
                    # parsed list/dict directly, no json.loads round-trip.
                    zone_set = set(row["zones"] or []) | set(zones)
                    uploaders = row["uploaders"] or {}
                    uploaders[discord_id] = sent_at
                    await db.execute(
                        _SQL["merge_session_window"],
                        (
                            win_start if extend else row["started_at"],
                            win_end if extend else row["ended_at"],
                            Json(sorted(zone_set)),
                            Json(uploaders),
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
                    seq = seq_row["seq"] if seq_row else 0  # aggregate always returns a row
                    cur = await db.execute(
                        _SQL["insert_session"],
                        (
                            world,
                            guild_name,
                            session_day,
                            seq,
                            win_start,
                            win_end,
                            Json(sorted(set(zones))),
                            1 if scheduled else 0,
                            team_index,
                            Json({discord_id: sent_at}),
                        ),
                    )
                    _row = await cur.fetchone()
                    session_id = _row["id"] if _row else None

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
        async with self._read() as db:
            async with await db.execute(
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
        async with self._read() as db:
            async with await db.execute(
                _SQL["select_sessions"], (world, guild_name, before_id, before_id, limit)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def get_session(self, session_id: int) -> dict | None:
        async with self._read() as db:
            async with await db.execute(_SQL["select_session"], (session_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def observations_for_session(self, session_id: int) -> list[dict]:
        async with self._read() as db:
            async with await db.execute(_SQL["select_observations"], (session_id,)) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def observations_for_sessions(self, session_ids: list[int]) -> dict[int, list[dict]]:
        if not session_ids:
            return {}
        out: dict[int, list[dict]] = {sid: [] for sid in session_ids}
        async with self._read() as db:
            async with await db.execute(_SQL["select_observations_many"], (session_ids,)) as cur:
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
        async with self._read() as db:
            async with await db.execute(_SQL["select_overrides"], (session_id,)) as cur:
                return {r["character_name"].lower(): dict(r) for r in await cur.fetchall()}

    async def overrides_for_sessions(self, session_ids: list[int]) -> dict[int, dict[str, dict]]:
        if not session_ids:
            return {}
        out: dict[int, dict[str, dict]] = {sid: {} for sid in session_ids}
        async with self._read() as db:
            async with await db.execute(_SQL["select_overrides_many"], (session_ids,)) as cur:
                for r in await cur.fetchall():
                    out[r["session_id"]][r["character_name"].lower()] = dict(r)
        return out

    async def set_session_window(self, session_id: int, started_at: int, ended_at: int) -> bool:
        """Officer correction of the session's own start/end (runaway-merge
        cleanup). session_day/seq stay frozen — grouping never moves."""
        async with self._db() as db:
            cur = await db.execute(_SQL["update_session_window"], (started_at, ended_at, session_id))
            await db.commit()
            return cur.rowcount > 0

    # ── Officer timelines ────────────────────────────────────────────────────

    async def set_segments(self, session_id: int, character_name: str, segments: list[dict], set_by: str) -> None:
        """Replace one character's manual timeline for one session. Segments
        are pre-validated dicts {category, started_at, ended_at}; an empty
        list clears the manual timeline (the derived one comes back)."""
        async with self._db() as db:
            await db.execute(_SQL["delete_segments_for_character"], (session_id, character_name))
            for seg in segments:
                await db.execute(
                    _SQL["insert_segment"],
                    (session_id, character_name, seg["category"], seg["started_at"], seg["ended_at"], set_by),
                )
            await db.commit()

    async def segments_for_session(self, session_id: int) -> dict[str, list[dict]]:
        """{character_name_lower: [{character_name, category, started_at,
        ended_at, set_by}]} ordered by started_at."""
        out: dict[str, list[dict]] = {}
        async with self._read() as db:
            async with await db.execute(_SQL["select_segments"], (session_id,)) as cur:
                for r in await cur.fetchall():
                    out.setdefault(r["character_name"].lower(), []).append(dict(r))
        return out

    async def segments_for_sessions(self, session_ids: list[int]) -> dict[int, dict[str, list[dict]]]:
        if not session_ids:
            return {}
        out: dict[int, dict[str, list[dict]]] = {sid: {} for sid in session_ids}
        async with self._read() as db:
            async with await db.execute(_SQL["select_segments_many"], (session_ids,)) as cur:
                for r in await cur.fetchall():
                    out[r["session_id"]].setdefault(r["character_name"].lower(), []).append(dict(r))
        return out

    async def remove_character(self, session_id: int, character_name: str) -> bool:
        """Officer row removal for junk names: the character's raid/online
        observations, any override, and any manual timeline go together.
        Voice rows (discord-id keyed) are untouched. True when anything
        was deleted."""
        async with self._db() as db:
            cur1 = await db.execute(_SQL["delete_character_observations"], (session_id, character_name))
            cur2 = await db.execute(_SQL["delete_override"], (session_id, character_name))
            cur3 = await db.execute(_SQL["delete_segments_for_character"], (session_id, character_name))
            await db.commit()
            return cur1.rowcount > 0 or cur2.rowcount > 0 or cur3.rowcount > 0

    async def delete_session(self, session_id: int) -> bool:
        async with self._db() as db:
            await db.execute(_SQL["delete_observations_for_session"], (session_id,))
            await db.execute(_SQL["delete_overrides_for_session"], (session_id,))
            await db.execute(_SQL["delete_segments_for_session"], (session_id,))
            cur = await db.execute(_SQL["delete_session"], (session_id,))
            await db.commit()
            return cur.rowcount > 0

    async def prune_voice_observations(self, *, older_than_days: int, now: int | None = None) -> int:
        """Delete voice-channel observations (Discord ids of channel members,
        site users or not) whose last_seen is older than the retention
        window. Returns the row count. Run by the retention loop."""
        cutoff = int(now if now is not None else time.time()) - older_than_days * 86400
        async with self._db() as db:
            cur = await db.execute(_SQL["delete_stale_voice_observations"], (cutoff,))
            await db.commit()
            return cur.rowcount


# The shared default instance — every runtime consumer goes through this.
store = AttendanceStore()
