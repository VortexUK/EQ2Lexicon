"""Background retention sweep for parses.

Two rules, both keyed on the fight time (``encounters.started_at``):

  * **Trash** encounters (non-boss — title starts lowercase, see ``boss.is_boss``)
    are hard-deleted ``RETENTION_DAYS`` after the fight.
  * **Named** (boss) encounters are never deleted, but their *duplicate* raider
    uploads are cleared ``RETENTION_DAYS`` after the fight — only the primary
    (the longest-duration canonical, the upload rankings link to) is kept.

Hard-deleting the non-primary boss uploads is rankings-safe: rankings
(``rankings._load_primary_boss_kills``) already mirror-group via the same
``_group_into_fights`` and use only the primary upload, and leaderboard links
point at the primary's encounter id. To be safe against the rare case where a
fight's longest upload is not the winning one rankings link to, the sweep also
preserves the longest *winning* upload. Soft-deleted (``hidden_at``) rows are
never touched, so manual hides survive.

``run_parse_cleanup`` is invoked periodically from ``app.py``'s lifespan
(see ``_parse_cleanup_loop``); ``now`` / ``retention_days`` are injectable so
tests can drive an arbitrary clock without touching wall-time.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time

from backend.server.parses import db as parses_db
from backend.server.parses.boss import is_boss

_log = logging.getLogger(__name__)

RETENTION_DAYS = int(os.getenv("PARSE_RETENTION_DAYS", "3"))
_DAY_S = 86_400


def run_parse_cleanup(now: int | None = None, retention_days: int | None = None) -> dict[str, int]:
    """Delete aged trash and collapse aged boss mirror-groups to their primary.

    Returns ``{"trash_deleted": n, "dup_uploads_deleted": m}``. Safe to call
    repeatedly (idempotent once nothing is past the cutoff)."""
    if not parses_db.DB_PATH.exists():
        return {"trash_deleted": 0, "dup_uploads_deleted": 0}

    now = int(time.time()) if now is None else now
    days = RETENTION_DAYS if retention_days is None else retention_days
    cutoff = now - days * _DAY_S

    # The grouping helpers live in the API layer; importing them at module load
    # would invert the api→db layering and risk an import cycle (app.py imports
    # this module at startup). Import locally — same pattern rankings.py uses.
    from backend.server.api.parses.list import (  # noqa: PLC0415
        _PLAYER_COUNT_SQL,
        _ensure_classified,
        _group_into_fights,
    )

    # Same player_count projection list/rankings use, so grouping (and therefore
    # the chosen primary) matches exactly. `e` is the encounters alias.
    candidate_sql = (
        f"SELECT e.*, ({_PLAYER_COUNT_SQL}) AS player_count FROM encounters e WHERE e.world = ? AND e.started_at < ?"
    )

    trash_deleted = 0
    dup_deleted = 0
    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        worlds = [r[0] for r in conn.execute("SELECT DISTINCT world FROM encounters").fetchall()]
        for world in worlds:
            rows = [dict(r) for r in conn.execute(candidate_sql, (world, cutoff)).fetchall()]

            # Trash → delete outright; boss rows are deferred to mirror-grouping.
            boss_rows: list[dict] = []
            for r in rows:
                if is_boss(r.get("title")):
                    boss_rows.append(r)
                elif parses_db.delete_encounter(conn, r["id"]):
                    trash_deleted += 1

            if not boss_rows:
                continue

            # Classify pre-migration combatants (is_player NULL) so player_count
            # and the top-N gate see the right flags — mirrors rankings/list.
            for r in boss_rows:
                if _ensure_classified(conn, r["id"], r.get("zone")):
                    refreshed = conn.execute(
                        "SELECT COUNT(*) FROM combatants WHERE encounter_id = ? AND is_player = 1",
                        (r["id"],),
                    ).fetchone()
                    r["player_count"] = int(refreshed[0])

            for g in _group_into_fights(boss_rows, conn):
                # Keep the canonical (longest overall) AND the longest winning
                # upload (= the rankings primary) so no leaderboard link breaks.
                keep_ids = {g["id"]}
                winning = [u for u in g["uploads"] if u.get("success_level") == 1]
                if winning:
                    keep_ids.add(max(winning, key=lambda u: u["duration_s"])["id"])
                for u in g["uploads"]:
                    if u["id"] in keep_ids or u.get("hidden_at") is not None:
                        continue
                    if parses_db.delete_encounter(conn, u["id"]):
                        dup_deleted += 1
    finally:
        conn.close()

    if trash_deleted or dup_deleted:
        _log.info(
            "[parse-cleanup] swept trash_deleted=%s dup_uploads_deleted=%s (retention=%sd)",
            trash_deleted,
            dup_deleted,
            days,
        )
    return {"trash_deleted": trash_deleted, "dup_uploads_deleted": dup_deleted}
