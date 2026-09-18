"""Manual Census refresh — the buttons on character/guild pages.

POST /api/character/{name}/refresh   — queue a refresh (session required)
GET  /api/character/{name}/refresh   — {queued, running, last_updated}
POST /api/guild/{name}/refresh
GET  /api/guild/{name}/refresh

Enqueues into backend/server/refresh_queue (one paced global worker); the
GET is what the page polls so a queued spinner survives reloads, and its
``last_updated`` (census_store.last_resolved_at) drives the "Census data
from N ago" line.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.census.store import store as census_store
from backend.server import refresh_queue
from backend.server.auth_deps import require_user_session
from backend.server.core.executor import run_sync
from backend.server.core.validation import validate_character_name, validate_guild_name
from backend.server.limiter import limiter
from backend.server.server_context import current_world

router = APIRouter(tags=["refresh"])

_REFUSAL_STATUS = {
    "census_down": 503,
    "queue_full": 503,
    "user_has_queued": 409,
    "entity_rate": 429,
}


def _last_resolved_character(name: str, world: str) -> int | None:
    if not census_store.path.exists():
        return None
    conn = census_store.init_db()
    try:
        rec = census_store.get_character(conn, name, world)
        return rec["last_resolved_at"] if rec else None
    finally:
        conn.close()


def _last_resolved_guild(name: str, world: str) -> int | None:
    if not census_store.path.exists():
        return None
    conn = census_store.init_db()
    try:
        rec = census_store.get_guild(conn, name, world)
        return rec["last_resolved_at"] if rec else None
    finally:
        conn.close()


def _enqueue(kind: str, name: str, request: Request) -> dict:
    user = require_user_session(request)
    world = current_world()
    try:
        return refresh_queue.enqueue(kind, name, world, str(user["id"]))
    except refresh_queue.Refused as exc:
        raise HTTPException(status_code=_REFUSAL_STATUS.get(exc.reason, 429), detail=exc.detail) from exc


@router.post("/character/{name}/refresh", status_code=202)
@limiter.limit("20/minute")
async def queue_character_refresh(request: Request, name: str) -> dict:
    valid = validate_character_name(name)
    if valid is None:
        raise HTTPException(status_code=400, detail="Character name is invalid.")
    return _enqueue("character", valid.capitalize(), request)


@router.get("/character/{name}/refresh")
@limiter.limit("60/minute")
async def character_refresh_status(request: Request, name: str) -> dict:
    valid = validate_character_name(name)
    if valid is None:
        raise HTTPException(status_code=400, detail="Character name is invalid.")
    world = current_world()
    state = refresh_queue.status("character", valid.capitalize(), world)
    last = await run_sync(_last_resolved_character, valid.capitalize(), world)
    return {**state, "last_updated": last}


@router.post("/guild/{name}/refresh", status_code=202)
@limiter.limit("20/minute")
async def queue_guild_refresh(request: Request, name: str) -> dict:
    valid = validate_guild_name(name)
    if valid is None:
        raise HTTPException(status_code=400, detail="Guild name is invalid.")
    return _enqueue("guild", valid, request)


@router.get("/guild/{name}/refresh")
@limiter.limit("60/minute")
async def guild_refresh_status(request: Request, name: str) -> dict:
    valid = validate_guild_name(name)
    if valid is None:
        raise HTTPException(status_code=400, detail="Guild name is invalid.")
    world = current_world()
    state = refresh_queue.status("guild", valid, world)
    last = await run_sync(_last_resolved_guild, valid, world)
    return {**state, "last_updated": last}
