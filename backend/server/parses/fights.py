"""Fights: the mirror-group an upload belongs to, decided at ingest.

Several raiders upload the same pull. The parses list, the rankings and the
retention sweep all need "which uploads are one fight, and which upload
represents it". That grouping used to be re-derived from scratch on every
rankings rebuild (every winning upload, every roster, a greedy pass). It is
now decided ONCE, when the upload lands, against the handful of existing
fights that share its world, boss and guild inside the mirror window — the
same rules as the list grouper in backend/server/api/parses/list.py:

  * a mirror comes from a DIFFERENT uploader (one raider's two uploads are
    two fights, even on the same boss inside the window);
  * some existing member started within ``PARSE_MIRROR_WINDOW_S``;
  * the new upload's top-N players appear somewhere in the fight's canonical
    upload and vice versa (N = 3 when either side is raid-sized, else 2).

The fight row carries two primaries, recomputed whenever a member changes:
``primary_encounter_id`` is the longest visible upload (the list's canonical
row) and ``primary_winning_encounter_id`` the longest visible, verified,
winning, not ranking-barred upload (the rankings kill). Ties go to the earliest start, matching
the grouper's chronological "promote only when strictly longer".

The full grouper remains the bulk path: :func:`backfill_world` groups every
ungrouped upload of a world with it (the one-time migration fill, and the
safety net for any upload an attach failed on), so both paths share one rule.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.server.constants import PARSE_MIRROR_WINDOW_S
from backend.server.parses.boss import boss_key
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)
_log = logging.getLogger(__name__)

#: Above this many ungrouped uploads :func:`backfill_world` uses the bulk
#: grouper (one roster prefetch) instead of one attach per upload.
BULK_THRESHOLD = 50


def guild_key(guild_name: str | None) -> str:
    """The guild component of a fight key: '' for unguilded uploads."""
    return guild_name or ""


def _canonical(members: list[dict]) -> dict:
    """Longest upload; earliest start, then lowest id, on a tie."""
    return max(members, key=lambda m: (m["duration_s"], -m["started_at"], -m["id"]))


def _primaries(members: list[dict]) -> tuple[int | None, int | None]:
    visible = [m for m in members if m.get("hidden_at") is None]
    primary = _canonical(visible)["id"] if visible else None
    # The ranking primary: visible, verified, winning, and not stamped as
    # barred (the spell-exploit window) — a barred fight ranks nothing.
    winning = [
        m
        for m in visible
        if m.get("success_level") == 1 and (m.get("uploader_verified") or 0) == 1 and m.get("ranking_barred_at") is None
    ]
    primary_winning = _canonical(winning)["id"] if winning else None
    return primary, primary_winning


def rosters(conn: Any, encounter_ids: list[int]) -> dict[int, list[str]]:
    """Ordered player rosters (encDPS DESC) for the containment gate."""
    out: dict[int, list[str]] = {eid: [] for eid in encounter_ids}
    if not encounter_ids:
        return out
    for row in conn.execute(_SQL["player_rosters_bulk"], (list(encounter_ids),)):
        out.setdefault(row["encounter_id"], []).append(row["name"])
    return out


def mutually_contained(rosters_by_id: dict[int, list[str]], a: int, b: int, n: int) -> bool:
    """Each side's top-N players appear somewhere in the other's roster."""
    ra, rb = rosters_by_id.get(a, []), rosters_by_id.get(b, [])
    return set(ra[:n]).issubset(rb) and set(rb[:n]).issubset(ra)


def members(conn: Any, fight_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(_SQL["select_fight_members"], (fight_id,)).fetchall()]


def get_fight(conn: Any, fight_id: int) -> dict | None:
    row = conn.execute(_SQL["select_fight"], (fight_id,)).fetchone()
    return dict(row) if row else None


def refresh_fight(conn: Any, fight_id: int) -> dict | None:
    """Recompute a fight's window, primaries and count from its members.
    An empty fight is deleted. Returns the refreshed row (``member_ids``
    included) or None when it was deleted. Caller owns the transaction."""
    rows = members(conn, fight_id)
    if not rows:
        conn.execute(_SQL["delete_fight"], (fight_id,))
        return None
    primary, primary_winning = _primaries(rows)
    first = min(m["started_at"] for m in rows)
    last = max(m["started_at"] for m in rows)
    conn.execute(
        _SQL["update_fight"],
        (first, last, primary, primary_winning, len(rows), int(time.time()), fight_id),
    )
    return {
        "id": fight_id,
        "first_started_at": first,
        "last_started_at": last,
        "primary_encounter_id": primary,
        "primary_winning_encounter_id": primary_winning,
        "upload_count": len(rows),
        "member_ids": [m["id"] for m in rows],
    }


def attach_encounter(conn: Any, encounter_id: int) -> int | None:
    """Group one upload: join the first candidate fight that passes every
    gate, else open a new fight. Idempotent — an already-grouped upload
    keeps its fight. Returns the fight id (None for an unknown encounter).
    Caller owns the transaction (ingest commits after its last statement)."""
    row = conn.execute(_SQL["select_encounter_for_attach"], (encounter_id,)).fetchone()
    if row is None:
        return None
    e = dict(row)
    if e["fight_id"] is not None:
        return e["fight_id"]
    world, title_key, guild = e["world"], boss_key(e["title"]), guild_key(e.get("guild_name"))
    window = PARSE_MIRROR_WINDOW_S
    uploader = e.get("uploaded_by") or "local"
    candidates = conn.execute(
        _SQL["select_candidate_fights"],
        (world, title_key, guild, e["started_at"] - window, e["started_at"] + window),
    ).fetchall()
    for f in candidates:
        rows = members(conn, f["id"])
        if not rows:
            continue
        if any((m.get("uploaded_by") or "local") == uploader for m in rows):
            continue
        if not any(abs(m["started_at"] - e["started_at"]) <= window for m in rows):
            continue
        canon = _canonical(rows)
        n = 3 if max(canon.get("player_count") or 0, e.get("player_count") or 0) >= 7 else 2
        if not mutually_contained(rosters(conn, [e["id"], canon["id"]]), e["id"], canon["id"], n):
            continue
        conn.execute(_SQL["set_encounter_fight"], (f["id"], e["id"]))
        refresh_fight(conn, f["id"])
        return f["id"]
    fight_id = _open_fight(conn, world, title_key, guild, e["started_at"])
    conn.execute(_SQL["set_encounter_fight"], (fight_id, e["id"]))
    refresh_fight(conn, fight_id)
    return fight_id


def _open_fight(conn: Any, world: str, title_key: str, guild: str, started_at: int) -> int:
    row = conn.execute(
        _SQL["insert_fight"], (world, title_key, guild, started_at, started_at, int(time.time()))
    ).fetchone()
    return int(next(iter(row.values())))


def detach_encounter(conn: Any, encounter_id: int) -> int | None:
    """Take an upload out of its fight and refresh what it left behind.
    Returns the fight id it left (None when it had none)."""
    row = conn.execute(_SQL["select_encounter_fight_id"], (encounter_id,)).fetchone()
    fight_id = row["fight_id"] if row else None
    if fight_id is None:
        return None
    conn.execute(_SQL["set_encounter_fight"], (None, encounter_id))
    refresh_fight(conn, fight_id)
    return fight_id


def reattach_encounter(conn: Any, encounter_id: int) -> int | None:
    """For an upload whose key changed (the guild backfilled after ingest):
    leave the old fight, group again."""
    detach_encounter(conn, encounter_id)
    return attach_encounter(conn, encounter_id)


def fight_id_of(conn: Any, encounter_id: int) -> int | None:
    row = conn.execute(_SQL["select_encounter_fight_id"], (encounter_id,)).fetchone()
    return row["fight_id"] if row else None


def aged_fights(conn: Any, world: str, before: int) -> list[dict]:
    """Fights whose newest upload started before ``before`` (retention)."""
    return [dict(r) for r in conn.execute(_SQL["select_aged_fights"], (world, before)).fetchall()]


def worlds_with_ungrouped(conn: Any) -> list[str]:
    return [r["world"] for r in conn.execute(_SQL["select_worlds_with_ungrouped"]).fetchall()]


#: How long a ``wait=True`` caller polls for another session's backfill.
BACKFILL_WAIT_S = 20 * 60.0
_BACKFILL_POLL_S = 5.0


def _ungrouped_count(conn: Any, world: str) -> int:
    row = conn.execute(_SQL["count_ungrouped"], (world,)).fetchone()
    return int(row["n"]) if row else 0


def backfill_world(conn: Any, world: str, *, wait: bool = False) -> int:
    """Group every ungrouped upload of ``world``. A small set goes through
    :func:`attach_encounter` so stragglers join existing fights; a large set
    (the one-time migration fill) runs the full grouper once with its bulk
    roster prefetch and writes the fights it produces. Commits. Returns the
    number of uploads grouped by THIS call.

    One backfill per world runs at a time (advisory lock): two processes (a
    deploy overlap, the prewarm and the retention sweep) must not group the
    same rows into two sets of fights. A caller that just needs the rows
    grouped before it reads (the rankings loader) passes ``wait=True`` and
    polls until the other session finishes; everyone else skips and picks
    the rows up next time."""
    pending = _ungrouped_count(conn, world)
    if pending == 0:
        return 0
    deadline = time.monotonic() + BACKFILL_WAIT_S
    while True:
        got = conn.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (f"fights-backfill:{world}",)
        ).fetchone()
        if got and got["ok"]:
            break
        if not wait:
            _log.info("[fights] backfill for world=%s already running elsewhere — skipping", world)
            return 0
        conn.rollback()  # the probe opened a transaction; don't camp it while polling
        time.sleep(_BACKFILL_POLL_S)
        pending = _ungrouped_count(conn, world)
        if pending == 0:
            _log.info("[fights] backfill for world=%s finished elsewhere", world)
            return 0
        if time.monotonic() > deadline:
            _log.warning(
                "[fights] backfill for world=%s still running elsewhere after %.0fs — reading as is",
                world,
                BACKFILL_WAIT_S,
            )
            return 0
    if pending <= BULK_THRESHOLD:
        ids = [r["id"] for r in conn.execute(_SQL["select_ungrouped_ids"], (world, BULK_THRESHOLD)).fetchall()]
        for eid in ids:
            attach_encounter(conn, eid)
        conn.commit()
        return len(ids)
    return _backfill_bulk(conn, world)


def _backfill_bulk(conn: Any, world: str) -> int:
    # The grouper lives in the API layer; import lazily (same pattern as
    # the retention sweep) to keep the parses package free of that cycle.
    from backend.server.api.parses.list import (  # noqa: PLC0415
        _classify_now,
        _group_into_fights,
        encounters_needing_classification,
    )

    t0 = time.monotonic()
    rows = [dict(r) for r in conn.execute(_SQL["select_ungrouped_for_bulk"], (world,)).fetchall()]
    # Classify first so player_count and the rosters mean the same thing
    # they will at attach time.
    needy = encounters_needing_classification(conn, [r["id"] for r in rows])
    for r in rows:
        if r["id"] in needy and _classify_now(conn, r["id"], r.get("zone")):
            refreshed = conn.execute(
                "SELECT COUNT(*) AS n FROM combatants WHERE encounter_id = %s AND is_player = 1", (r["id"],)
            ).fetchone()
            r["player_count"] = int(refreshed["n"]) if refreshed else 0
    # Same key the incremental path uses: normalised title, '' guild.
    for r in rows:
        r["title"] = boss_key(r["title"])
        r["guild_name"] = guild_key(r.get("guild_name"))
    groups = _group_into_fights(rows, conn)
    written = 0
    for i, g in enumerate(groups, 1):
        member_ids = [u["id"] for u in g["uploads"]]
        starts = [u["started_at"] for u in g["uploads"]]
        fight_id = _open_fight(conn, world, g["title"], g["guild_name"], min(starts))
        conn.execute(_SQL["set_fight_for_encounters"], (fight_id, member_ids))
        refresh_fight(conn, fight_id)
        written += len(member_ids)
        if i % 500 == 0:
            conn.commit()
    conn.commit()
    _log.info(
        "[fights] backfilled world=%s uploads=%d fights=%d in %.1fs", world, written, len(groups), time.monotonic() - t0
    )
    return written
