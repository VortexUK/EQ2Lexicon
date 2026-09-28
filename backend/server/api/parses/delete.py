"""DELETE /parses/* — batch / single encounter deletion (+ unhide).

Soft-delete (hidden_at set) is the default for boss kills; admins can
purge=true for a hard delete. Auth: admin sees all; officer of an encounter's
guild or the original uploader can soft-delete their own.

There is deliberately NO filter-based bulk delete any more. The former
``DELETE /parses?guild=`` route matched every parse a guild had ever
uploaded while the page's confirm dialog quoted only the rows visible under
the current filters — on 2026-09-27 that wiped a guild's whole history from a
"clear this view" click. Every deletion now names its ids explicitly, and
each id is authorised on its own row.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from fastapi import HTTPException, Request

from backend.server.api.parses import router
from backend.server.api.parses.list import _uploader_discord_id, invalidate_parses_list_cache
from backend.server.api.parses.models import DeleteParsesResponse
from backend.server.auth_deps import (
    is_admin as _is_admin,
)
from backend.server.auth_deps import (
    require_user_session as _require_user,
)
from backend.server.constants import PARSE_BATCH_MAX_IDS
from backend.server.core.audit_log import audit_log
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser
from backend.server.db.guild_settings import store as guild_settings_db
from backend.server.limiter import limiter
from backend.server.parses.boss import is_boss
from backend.server.parses.db import store as parses_db
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)


async def _can_delete_encounter(user: SessionUser, enc: dict, *, guild_ok: dict[str, bool] | None = None) -> bool:
    """Authorise deletion of one encounter row (must carry `guild_name` and
    `source_dsn`). Any of: admin, the original uploader, or an officer of the
    encounter's guild — the last only while the guild's leader allows it
    (``guild_settings.officers_can_delete_parses``; unhide shares the rule).
    Never trusts the caller for guild/uploader — both come from the stored
    row.

    ``guild_ok`` is an optional per-request memo of the officer verdict keyed
    by guild name: a batch of 200 ids from one guild then costs one roster
    lookup and one settings read, not 200."""
    if _is_admin(user) or _uploader_discord_id(enc.get("source_dsn")) == user["id"]:
        return True
    gname = enc.get("guild_name")
    if not gname:
        return False
    if guild_ok is not None and gname in guild_ok:
        return guild_ok[gname]
    from backend.server.api.guild import _officer_chars

    verdict = bool(await _officer_chars(user["id"], gname))
    if verdict:
        flags = await guild_settings_db.officers_can_delete_parses(current_world(), [gname])
        verdict = flags.get(gname, True)
    if guild_ok is not None:
        guild_ok[gname] = verdict
    return verdict


def _fetch_encounter_auth_rows(ids: list[int], world: str) -> list[dict]:
    """Fetch the (id, guild_name, source_dsn, title, hidden_at) rows needed to
    authorise a delete, scoped to *world* so a cross-server id returns nothing.
    Runs in an executor."""
    conn = parses_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, guild_name, source_dsn, title, hidden_at FROM encounters WHERE id IN ({placeholders}) AND world = ?",
            [*ids, world],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _apply_delete(conn: sqlite3.Connection, enc: dict, *, purge: bool, hidden_at: int, hidden_by: str) -> bool:
    """Hard-purge wins; otherwise boss kills are soft-deleted (preserve any
    ranking) and trash is hard-deleted. Caller has already authorised + (for
    purge) checked admin. ``hidden_by`` stamps who hid the row for the
    admin sanitize view."""
    if purge or not is_boss(enc.get("title")):
        return parses_db.delete_encounter(conn, enc["id"])
    return parses_db.soft_delete_encounter(conn, enc["id"], hidden_at, hidden_by)


def _parse_batch_ids(ids: str) -> list[int]:
    """The comma-separated ``ids`` query param of the batch routes → deduped
    ints, capped at PARSE_BATCH_MAX_IDS. 400 on a non-integer token or an
    empty list."""
    id_list: list[int] = []
    for tok in ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            id_list.append(int(tok))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid encounter id: {tok!r}") from None
    id_list = list(dict.fromkeys(id_list))[:PARSE_BATCH_MAX_IDS]  # dedupe, cap fan-out
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must not be empty")
    return id_list


@router.delete("/parses/batch", response_model=DeleteParsesResponse)
@limiter.limit("30/minute")
async def delete_parses_batch(
    request: Request,
    ids: str,
    purge: bool = False,
) -> DeleteParsesResponse:
    """Delete an explicit set of encounter ids — the uploads that make up one
    multi-uploader fight on /parses. `ids` is comma-separated.

    Each id is authorised independently with the same rule as single-delete,
    so an officer of the fight's guild (or an admin) can remove EVERY raider's
    upload of the encounter in one action, while a non-privileged caller only
    removes the ids they're entitled to. Ids the caller can't delete are
    skipped rather than failing the whole request; a 403 is returned only when
    none are permitted.

    `purge=true` (admin only) hard-deletes even boss-kill encounters, removing
    them from leaderboards. Without purge, boss kills are soft-deleted
    (hidden_at set) to preserve their ranking entry.

    A guild-wide delete from /parses arrives as several of these requests
    (the page chunks the VISIBLE upload ids at PARSE_BATCH_MAX_IDS per call),
    so one audit line per request is the expected shape.

    Defined before /parses/{encounter_id} so the literal path wins the route
    match.
    """
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge parses")

    id_list = _parse_batch_ids(ids)
    rows = await run_sync(_fetch_encounter_auth_rows, id_list, current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="No matching parses")

    guild_ok: dict[str, bool] = {}
    allowed_rows = [enc for enc in rows if await _can_delete_encounter(user, enc, guild_ok=guild_ok)]
    if not allowed_rows:
        raise HTTPException(status_code=403, detail="Not authorised to delete these parses")

    now = int(time.time())

    def _delete_many() -> int:
        conn = parses_db.init_db()
        try:
            return sum(
                1 for enc in allowed_rows if _apply_delete(conn, enc, purge=purge, hidden_at=now, hidden_by=user["id"])
            )
        finally:
            conn.close()

    n = await run_sync(_delete_many)
    if n:
        invalidate_parses_list_cache()  # the cached /parses pages now lie
    audit_log(
        "parse_batch_deleted",
        actor=user["id"],
        count=n,
        ids=",".join(str(i) for i in id_list[:20]) + (" …" if len(id_list) > 20 else ""),
        guilds=",".join(sorted({enc["guild_name"] for enc in allowed_rows if enc.get("guild_name")})),
        purged=purge,
    )
    return DeleteParsesResponse(deleted=n)


@router.delete("/parses/{encounter_id}", response_model=DeleteParsesResponse)
@limiter.limit("30/minute")
async def delete_parse(
    request: Request,
    encounter_id: int,
    purge: bool = False,
) -> DeleteParsesResponse:
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge a parse")

    # Look up the row so we can authorise against its real guild_name and
    # source_dsn — never trust the caller for either.  Also enforces
    # per-server isolation: an id from another world returns no rows → 404.
    rows = await run_sync(_fetch_encounter_auth_rows, [encounter_id], current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="Parse not found")

    if not await _can_delete_encounter(user, rows[0]):
        raise HTTPException(status_code=403, detail="Not authorised to delete this parse")

    enc = rows[0]
    now = int(time.time())

    def _delete_sync() -> bool:
        conn = parses_db.init_db()
        try:
            return _apply_delete(conn, enc, purge=purge, hidden_at=now, hidden_by=user["id"])
        finally:
            conn.close()

    removed = await run_sync(_delete_sync)
    if removed:
        invalidate_parses_list_cache()  # the cached /parses pages now lie
        audit_log(
            "parse_deleted",
            actor=user["id"],
            encounter_id=encounter_id,
            title=enc["title"] if enc else "<unknown>",
            purged=purge,
        )
    return DeleteParsesResponse(deleted=1 if removed else 0)


@router.post("/parses/batch/unhide")
@limiter.limit("30/minute")
async def unhide_parses_batch(request: Request, ids: str) -> dict:
    """Restore an explicit set of soft-deleted encounters in one action — the
    undo for a mistaken guild-wide delete (the 2026-09-27 incident hid a
    guild's whole boss history). Same per-id authorisation as the batch
    delete: admin, the uploader, or an officer of the encounter's guild
    (subject to the guild's officer-delete switch); ids the caller may not
    touch are skipped, 403 only when none are permitted. Already-visible
    rows count as untouched. Defined before /parses/{encounter_id}/unhide
    so the literal path wins."""
    user = _require_user(request)
    id_list = _parse_batch_ids(ids)
    rows = await run_sync(_fetch_encounter_auth_rows, id_list, current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="No matching parses")

    guild_ok: dict[str, bool] = {}
    allowed_rows = [enc for enc in rows if await _can_delete_encounter(user, enc, guild_ok=guild_ok)]
    if not allowed_rows:
        raise HTTPException(status_code=403, detail="Not authorised to unhide these parses")

    def _unhide_many() -> int:
        conn = parses_db.init_db()
        try:
            return sum(1 for enc in allowed_rows if parses_db.unhide_encounter(conn, enc["id"]))
        finally:
            conn.close()

    n = await run_sync(_unhide_many)
    if n:
        invalidate_parses_list_cache()  # the restored rows must show at once
    audit_log(
        "parse_batch_unhidden",
        actor=user["id"],
        count=n,
        ids=",".join(str(i) for i in id_list[:20]) + (" …" if len(id_list) > 20 else ""),
        guilds=",".join(sorted({enc["guild_name"] for enc in allowed_rows if enc.get("guild_name")})),
    )
    return {"unhidden": n}


@router.post("/parses/{encounter_id}/unhide")
@limiter.limit("30/minute")
async def unhide_parse(request: Request, encounter_id: int) -> dict:
    """Clear a soft-delete: the parse reappears on /parses and its ranking
    stays intact (it never left — soft-delete preserves the row). Same
    authorisation rule as hiding: admin, the original uploader, or an
    officer of the encounter's guild. 404 for unknown ids, `unhidden:
    false` when the row was already visible."""
    user = _require_user(request)
    rows = await run_sync(_fetch_encounter_auth_rows, [encounter_id], current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="Parse not found")
    if not await _can_delete_encounter(user, rows[0]):
        raise HTTPException(status_code=403, detail="Not authorised to unhide this parse")

    def _unhide_sync() -> bool:
        conn = parses_db.init_db()
        try:
            return parses_db.unhide_encounter(conn, encounter_id)
        finally:
            conn.close()

    restored = await run_sync(_unhide_sync)
    if restored:
        invalidate_parses_list_cache()  # the restored row must show at once
        audit_log(
            "parse_unhidden",
            actor=user["id"],
            encounter_id=encounter_id,
            title=rows[0]["title"],
        )
    return {"unhidden": restored}
