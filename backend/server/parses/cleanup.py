"""Retention sweeps for parses, keyed on the fight time (``started_at``).

  * Trash (non-boss, see ``boss.is_boss``) is hard-deleted ``RETENTION_DAYS``
    after the fight.
  * Named (boss) fights keep only the primary upload after the same window:
    the canonical (longest) AND the longest winning upload are both kept, so
    no rankings link breaks.
  * Kept fights' ``attack_types`` + ``damage_types`` are pruned on a zone-tier
    schedule (``DETAIL_RETENTION_*_DAYS``, by ``_classify_zone``) and
    ``detail_pruned_at`` is stamped. ``encounters`` + ``combatants`` live
    forever. Trash is gone before the shortest tier, so the prune needs no
    is_boss check.

Soft-deleted (``hidden_at``) rows are never touched. ``now`` /
``retention_days`` are injectable for tests.
"""

from __future__ import annotations

import logging
import os
import time

from backend.server.parses import fights
from backend.server.parses.boss import is_boss
from backend.server.parses.db import store as parses_db

_log = logging.getLogger(__name__)

RETENTION_DAYS = int(os.getenv("PARSE_RETENTION_DAYS", "3"))

#: Tiered attack_types/damage_types retention by zone category.
DETAIL_RETENTION_RAID_DAYS = int(os.getenv("PARSE_DETAIL_RETENTION_RAID_DAYS", "30"))
DETAIL_RETENTION_DUNGEON_DAYS = int(os.getenv("PARSE_DETAIL_RETENTION_DUNGEON_DAYS", "14"))
DETAIL_RETENTION_OTHER_DAYS = int(os.getenv("PARSE_DETAIL_RETENTION_OTHER_DAYS", "7"))

_DAY_S = 86_400

#: Prune in chunks so one DELETE never holds a giant transaction.
_PRUNE_CHUNK = 200


def run_parse_cleanup(now: int | None = None, retention_days: int | None = None) -> dict[str, int]:
    """Delete aged trash, collapse aged boss mirror-groups to their primary,
    then drop overdue breakdown detail per the retention tiers.

    Returns ``{"trash_deleted": n, "dup_uploads_deleted": m,
    "detail_pruned": k}``. Safe to call repeatedly (idempotent once nothing
    is past the cutoffs)."""
    now = int(time.time()) if now is None else now
    days = RETENTION_DAYS if retention_days is None else retention_days
    cutoff = now - days * _DAY_S

    trash_deleted = 0
    dup_deleted = 0
    conn = parses_db.init_db()
    try:
        worlds = [r["world"] for r in conn.execute("SELECT DISTINCT world FROM encounters").fetchall()]
        for world in worlds:
            # Anything not yet grouped (an attach that failed) gets its fight
            # first, so the collapse below never sees an orphan.
            fights.backfill_world(conn, world)
            rows = conn.execute(
                "SELECT id, title FROM encounters WHERE world = %s AND started_at < %s", (world, cutoff)
            ).fetchall()
            # Trash -> delete outright (the store hook refreshes its fight).
            for r in rows:
                if not is_boss(r["title"]) and parses_db.delete_encounter(conn, r["id"]):
                    trash_deleted += 1
            # Boss fights: keep the list canonical AND the rankings primary so
            # no leaderboard link breaks; hidden uploads are never deleted.
            for f in fights.aged_fights(conn, world, cutoff):
                keep = {f["primary_encounter_id"], f["primary_winning_encounter_id"]}
                members = fights.members(conn, f["id"])
                # A ranking-barred fight has no winning primary on the row, but
                # the bar is reversible: keep its longest winning upload too.
                wins = [m for m in members if m.get("success_level") == 1 and m.get("hidden_at") is None]
                if wins:
                    keep.add(max(wins, key=lambda m: (m["duration_s"], -m["started_at"], -m["id"]))["id"])
                for m in members:
                    if m["id"] in keep or m.get("hidden_at") is not None:
                        continue
                    if parses_db.delete_encounter(conn, m["id"]):
                        dup_deleted += 1

        detail_pruned = _sweep_detail_retention(conn, now)
    finally:
        conn.close()

    if trash_deleted or dup_deleted or detail_pruned:
        _log.info(
            "[parse-cleanup] swept trash_deleted=%s dup_uploads_deleted=%s detail_pruned=%s (retention=%sd)",
            trash_deleted,
            dup_deleted,
            detail_pruned,
            days,
        )
    return {"trash_deleted": trash_deleted, "dup_uploads_deleted": dup_deleted, "detail_pruned": detail_pruned}


def detail_retention_days(zone: str | None) -> int:
    """The breakdown-retention tier (in days) for a parse's zone — curated
    raid zones keep full detail longest, curated group instances less,
    everything else least. Import-light wrapper over the parses list's
    ``_classify_zone`` so the copy script and tests share one rule."""
    from backend.server.api.parses.list import _classify_zone  # noqa: PLC0415

    category = _classify_zone(zone)
    if category == "raid":
        return DETAIL_RETENTION_RAID_DAYS
    if category == "dungeon":
        return DETAIL_RETENTION_DUNGEON_DAYS
    return DETAIL_RETENTION_OTHER_DAYS


def _sweep_detail_retention(conn, now: int) -> int:
    """Drop attack_types/damage_types for every kept encounter whose tier
    cutoff has passed; stamp ``detail_pruned_at``. Returns encounters pruned.

    Candidates = detail not yet pruned AND older than the SHORTEST tier —
    cheap via the partial idx_encounters_detail_prune index. Each candidate
    is then held against its own zone's tier, so a 10-day-old curated raid
    kill survives while a 10-day-old uncurated fight is pruned.
    """
    shortest = min(DETAIL_RETENTION_RAID_DAYS, DETAIL_RETENTION_DUNGEON_DAYS, DETAIL_RETENTION_OTHER_DAYS)
    # One fetch: the candidate payload is three small columns and the partial
    # index keeps the set to "kept fights whose shortest tier has passed but
    # whose own tier may not have" — a few hundred rows in steady state.
    rows = parses_db.select_detail_prune_candidates(conn, older_than=now - shortest * _DAY_S, limit=100_000)
    due = [r["id"] for r in rows if r["started_at"] < now - detail_retention_days(r.get("zone")) * _DAY_S]

    pruned = 0
    for i in range(0, len(due), _PRUNE_CHUNK):
        chunk = due[i : i + _PRUNE_CHUNK]
        parses_db.prune_encounter_detail(conn, chunk, pruned_at=now)
        conn.commit()
        pruned += len(chunk)
    return pruned


# Purge tombstones in ingest_log and tamper reports have their own windows,
# independent of the parse retention tiers.
TOMBSTONE_RETENTION_DAYS = 30
TAMPER_REPORT_RETENTION_DAYS = 180
TAMPER_REPORT_ACKED_RETENTION_DAYS = 90


def run_tombstone_sweeps(now: int | None = None) -> dict[str, int]:
    """Expire ingest_log purge tombstones and old tamper reports. Returns
    ``{"tombstones_expired": n, "tamper_reports_expired": m}``."""
    now = int(time.time()) if now is None else now
    conn = parses_db.init_db()
    try:
        tombstones = parses_db.expire_ingest_tombstones(conn, now - TOMBSTONE_RETENTION_DAYS * _DAY_S)
        tamper = parses_db.expire_tamper_reports(
            conn,
            before_any=now - TAMPER_REPORT_RETENTION_DAYS * _DAY_S,
            before_ack=now - TAMPER_REPORT_ACKED_RETENTION_DAYS * _DAY_S,
        )
        conn.commit()
    finally:
        conn.close()
    return {"tombstones_expired": tombstones, "tamper_reports_expired": tamper}
