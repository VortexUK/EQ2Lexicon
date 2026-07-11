"""DELETE /parses/* — batch / single / bulk encounter deletion.

Soft-delete (hidden_at set) is the default; admin bulk-delete can purge=true
for a hard delete. Auth: admin sees all; officer of an encounter's guild
or the original uploader can soft-delete their own.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from fastapi import HTTPException, Request

from backend.server.api.parses import router
from backend.server.api.parses.list import _uploader_discord_id
from backend.server.api.parses.models import DeleteParsesResponse
from backend.server.auth_deps import (
    is_admin as _is_admin,
)
from backend.server.auth_deps import (
    require_user_session as _require_user,
)
from backend.server.core.audit_log import audit_log
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser
from backend.server.limiter import limiter
from backend.server.parses.boss import is_boss
from backend.server.parses.db import store as parses_db
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)


async def _can_delete_encounter(user: SessionUser, enc: dict) -> bool:
    """Authorise deletion of one encounter row (must carry `guild_name` and
    `source_dsn`). Any of: admin, the original uploader, or an officer of the
    encounter's guild. Never trusts the caller for guild/uploader — both come
    from the stored row."""
    if _is_admin(user) or _uploader_discord_id(enc.get("source_dsn")) == user["id"]:
        return True
    gname = enc.get("guild_name")
    if gname:
        from backend.server.api.guild import _officer_chars

        if await _officer_chars(user["id"], gname):
            return True
    return False


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


def _apply_delete(conn: sqlite3.Connection, enc: dict, *, purge: bool, hidden_at: int) -> bool:
    """Hard-purge wins; otherwise boss kills are soft-deleted (preserve any
    ranking) and trash is hard-deleted. Caller has already authorised + (for
    purge) checked admin."""
    if purge or not is_boss(enc.get("title")):
        return parses_db.delete_encounter(conn, enc["id"])
    return parses_db.soft_delete_encounter(conn, enc["id"], hidden_at)


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

    Defined before /parses/{encounter_id} so the literal path wins the route
    match.
    """
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge parses")

    id_list: list[int] = []
    for tok in ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            id_list.append(int(tok))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid encounter id: {tok!r}") from None
    id_list = list(dict.fromkeys(id_list))[:64]  # dedupe, cap fan-out
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must not be empty")

    rows = await run_sync(_fetch_encounter_auth_rows, id_list, current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="No matching parses")

    allowed_rows = [enc for enc in rows if await _can_delete_encounter(user, enc)]
    if not allowed_rows:
        raise HTTPException(status_code=403, detail="Not authorised to delete these parses")

    now = int(time.time())

    def _delete_many() -> int:
        conn = parses_db.init_db()
        try:
            return sum(1 for enc in allowed_rows if _apply_delete(conn, enc, purge=purge, hidden_at=now))
        finally:
            conn.close()

    n = await run_sync(_delete_many)
    audit_log(
        "parse_batch_deleted",
        actor=user["id"],
        count=n,
        ids=",".join(str(i) for i in id_list[:20]) + (" …" if len(id_list) > 20 else ""),
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
            return _apply_delete(conn, enc, purge=purge, hidden_at=now)
        finally:
            conn.close()

    removed = await run_sync(_delete_sync)
    if removed:
        audit_log(
            "parse_deleted",
            actor=user["id"],
            encounter_id=encounter_id,
            title=enc["title"] if enc else "<unknown>",
            purged=purge,
        )
    return DeleteParsesResponse(deleted=1 if removed else 0)


@router.delete("/parses", response_model=DeleteParsesResponse)
@limiter.limit("10/minute")
async def delete_parses_bulk(
    request: Request,
    guild: str,
    zone: str | None = None,
    date: str | None = None,  # YYYY-MM-DD in server local timezone
    uploader: str | None = None,
    purge: bool = False,
) -> DeleteParsesResponse:
    """Bulk delete by filter. `guild` is required — there is deliberately no
    "delete everything across all guilds" path. Permission: admin or officer
    of the named guild.

    Boss kills are soft-deleted (hidden_at set, ranking entry preserved);
    trash encounters are hard-deleted. `purge=true` (admin only) hard-deletes
    everything, including boss kills."""
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge parses")

    guild = guild.strip()
    if not guild:
        raise HTTPException(status_code=400, detail="guild parameter must not be empty")

    allowed = _is_admin(user)
    if not allowed:
        from backend.server.api.guild import _officer_chars

        if await _officer_chars(user["id"], guild):
            allowed = True
    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorised to delete parses for this guild")

    now = int(time.time())

    _world = current_world()

    def _delete_sync() -> int:
        conn = parses_db.init_db()
        try:
            matches = parses_db.find_encounters_by_filter(
                conn,
                guild_name=guild,
                zone=zone,
                date=date,
                uploaded_by=uploader,
                world=_world,
            )
            return sum(1 for enc in matches if _apply_delete(conn, enc, purge=purge, hidden_at=now))
        finally:
            conn.close()

    n = await run_sync(_delete_sync)
    audit_log(
        "parse_bulk_deleted",
        actor=user["id"],
        count=n,
        filter_guild=guild,
        filter_zone=zone or "*",
        filter_date=date or "*",
        filter_uploader=uploader or "*",
        purged=purge,
    )
    return DeleteParsesResponse(deleted=n)
