from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from parses import db as parses_db
from web import server_context
from web.auth_deps import KNOWN_ROLES
from web.auth_deps import require_admin as _require_admin
from web.cache import claim_cache
from web.db import (
    delete_claim,
    delete_claims_for_user,
    get_claim_by_id,
    get_role_request,
    get_server_by_world_sync,
    grant_role,
    list_all_users,
    list_claims,
    list_role_assignments,
    list_role_requests,
    list_servers_sync,
    review_claim,
    review_role_request,
    revoke_role,
    set_default_server_sync,
    set_user_access,
    upsert_server_settings_sync,
)
from web.routes.claim import _refresh_claim_cache
from web.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ClaimDetail(BaseModel):
    id: int
    discord_id: str
    discord_name: str | None = None  # NULL when user row missing (LEFT JOIN)
    discord_username: str | None = None
    avatar: str | None = None
    character_name: str
    status: str
    requested_at: int
    reviewed_at: int | None = None
    reviewed_by: str | None = None
    note: str | None = None


class RejectRequest(BaseModel):
    note: str | None = None


class UserItem(BaseModel):
    discord_id: str
    discord_name: str | None = None
    discord_username: str | None = None
    avatar: str | None = None
    first_seen: int
    last_seen: int
    access_status: str
    claim_count: int = 0
    # DB-granted roles (e.g. 'contributor'). Doesn't include 'admin' (env-
    # driven) or 'officer' (dynamic). Joined in via list_role_assignments so
    # this stays a single round-trip to /admin/users.
    roles: list[str] = []


class AdminParseItem(BaseModel):
    id: int
    title: str
    zone: str | None = None
    guild_name: str | None = None
    uploaded_by: str | None = None
    started_at: int
    duration_s: int
    success_level: int
    player_count: int
    hidden: bool


class ServerItem(BaseModel):
    world: str
    subdomain: str
    display_name: str
    max_level: int
    current_xpac: str | None = None
    launch_dt: str | None = None
    is_default: bool = False


class ServerSettingsUpdate(BaseModel):
    max_level: Annotated[int, Field(gt=0)]
    current_xpac: str | None = None
    launch_dt: str | None = None
    is_default: bool | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/claims", response_model=list[ClaimDetail])
async def list_all_claims(
    request: Request,
    status: Literal["pending", "approved", "rejected"] | None = None,
) -> list[ClaimDetail]:
    """
    List character claims for the active server, optionally filtered by status.
    Scoped to current_world() so an admin on varsoon.* sees only Varsoon claims.
    Pending claims are sorted oldest-first (queue order).
    """
    _require_admin(request)
    claims = await list_claims(status=status, world=current_world())
    return [ClaimDetail(**c) for c in claims]


@router.post("/admin/claims/{claim_id}/approve", response_model=ClaimDetail)
async def approve_claim(claim_id: int, request: Request) -> ClaimDetail:
    """Approve a pending claim.  Supersedes any existing approved claim for the user."""
    admin = _require_admin(request)
    result = await review_claim(claim_id, "approved", admin["id"])
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    claim_cache.delete(f"claims:{result['discord_id']}")
    asyncio.create_task(_refresh_claim_cache(result["discord_id"]))
    return ClaimDetail(**result)


@router.delete("/admin/claims/{claim_id}", status_code=200)
async def remove_claim(claim_id: int, request: Request) -> dict:
    """Permanently delete a claim record."""
    _require_admin(request)
    claim = await get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    await delete_claim(claim_id)
    claim_cache.delete(f"claims:{claim['discord_id']}")
    asyncio.create_task(_refresh_claim_cache(claim["discord_id"]))
    return {"ok": True}


@router.post("/admin/claims/{claim_id}/reject", response_model=ClaimDetail)
async def reject_claim(
    claim_id: int,
    body: RejectRequest,
    request: Request,
) -> ClaimDetail:
    """Reject a pending claim, optionally with a note explaining why."""
    admin = _require_admin(request)
    result = await review_claim(claim_id, "rejected", admin["id"], note=body.note)
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    claim_cache.delete(f"claims:{result['discord_id']}")
    asyncio.create_task(_refresh_claim_cache(result["discord_id"]))
    return ClaimDetail(**result)


@router.delete("/admin/users/{discord_id}/claims", status_code=200)
async def remove_all_user_claims(discord_id: str, request: Request) -> dict:
    """Permanently delete every claim record for a user."""
    _require_admin(request)
    count = await delete_claims_for_user(discord_id)
    claim_cache.delete(f"claims:{discord_id}")
    asyncio.create_task(_refresh_claim_cache(discord_id))
    return {"ok": True, "deleted": count}


@router.get("/admin/users", response_model=list[UserItem])
async def list_users(request: Request) -> list[UserItem]:
    """List all users with access status, claim counts, and DB-granted roles.
    Admin only."""
    _require_admin(request)
    rows = await list_all_users()
    role_map = await list_role_assignments()
    return [UserItem(**r, roles=role_map.get(r["discord_id"], [])) for r in rows]


# ---------------------------------------------------------------------------
# Role management
# ---------------------------------------------------------------------------
#
# TODO(future): self-service role requests. Admin-initiated only for now —
# see the matching TODO in web/db.py's user_roles schema for the proposed
# shape (a role_requests queue table mirroring character_claims).


@router.post("/admin/users/{discord_id}/roles/{role}", status_code=200)
async def grant_user_role(discord_id: str, role: str, request: Request) -> dict:
    """Grant a role to a user. Rejects unknown role names (typo guard).
    Idempotent — re-granting an existing role returns ok=True, granted=False."""
    admin = _require_admin(request)
    if role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {role!r}. Known roles: {sorted(KNOWN_ROLES)}",
        )
    inserted = await grant_role(discord_id, role, admin["id"])
    return {"ok": True, "granted": inserted}


@router.delete("/admin/users/{discord_id}/roles/{role}", status_code=200)
async def revoke_user_role(discord_id: str, role: str, request: Request) -> dict:
    """Revoke a role from a user. 404 when the user didn't have the role."""
    _require_admin(request)
    if role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {role!r}. Known roles: {sorted(KNOWN_ROLES)}",
        )
    removed = await revoke_role(discord_id, role)
    if not removed:
        raise HTTPException(status_code=404, detail="User does not have this role")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Role-request review queue
# ---------------------------------------------------------------------------
#
# The user-facing submit/withdraw endpoints live in web/routes/role_requests.py
# — here we just add the admin queue + approve/reject actions.
#
# Imported here so admin.py owns the entire admin REST surface; the user-side
# RoleRequestEntry shape happens to be identical so we reuse it rather than
# duplicate.


from web.routes.role_requests import RoleRequestEntry  # noqa: E402


class ReviewRoleRequest(BaseModel):
    """Body for reject (and optionally approve) — admin's response note."""

    note: str | None = None


@router.get("/admin/role-requests", response_model=list[RoleRequestEntry])
async def list_pending_role_requests(
    request: Request,
    status: Literal["pending", "approved", "rejected", "withdrawn"] | None = "pending",
) -> list[RoleRequestEntry]:
    """List role requests, defaulting to the pending queue. Pending sorts
    oldest-first (FIFO); resolved sort newest-first for audit browsing."""
    _require_admin(request)
    rows = await list_role_requests(status=status)
    return [RoleRequestEntry(**r) for r in rows]


@router.post("/admin/role-requests/{request_id}/approve", response_model=RoleRequestEntry)
async def approve_role_request(
    request_id: int,
    body: ReviewRoleRequest,
    request: Request,
) -> RoleRequestEntry:
    """Approve a pending request: marks it approved AND inserts the
    corresponding user_roles row in one logical step.

    Idempotency: if the user already happens to hold the role (e.g. admin
    granted it directly between submit + approve), the request still flips
    to approved — grant_role is INSERT OR IGNORE so the role row just stays
    put."""
    admin = _require_admin(request)
    existing = await get_role_request(request_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if existing["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {existing['status']}")

    # Mark approved first so a grant-side failure doesn't leave the queue with
    # a phantom-approved row; if grant_role raises, the request stays pending
    # for retry. (Single SQLite file — atomicity across two helpers is
    # best-effort, but ordering matters for the failure mode.)
    reviewed = await review_role_request(request_id, "approved", admin["id"], body.note)
    if reviewed is None:
        # Lost to a concurrent admin (race). Surface as 409 rather than 200.
        raise HTTPException(status_code=409, detail="Request was reviewed by someone else")
    await grant_role(existing["discord_id"], existing["role"], admin["id"])
    return RoleRequestEntry(**reviewed)


@router.post("/admin/role-requests/{request_id}/reject", response_model=RoleRequestEntry)
async def reject_role_request(
    request_id: int,
    body: ReviewRoleRequest,
    request: Request,
) -> RoleRequestEntry:
    """Reject a pending request with an optional explanation note."""
    admin = _require_admin(request)
    existing = await get_role_request(request_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if existing["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {existing['status']}")
    reviewed = await review_role_request(request_id, "rejected", admin["id"], body.note)
    if reviewed is None:
        raise HTTPException(status_code=409, detail="Request was reviewed by someone else")
    return RoleRequestEntry(**reviewed)


@router.get("/admin/parses", response_model=list[AdminParseItem])
async def list_parses_admin(
    request: Request,
    search: str | None = None,
    limit: int = 200,
) -> list[AdminParseItem]:
    """All parse encounters (including hidden/soft-deleted) for the sanitize
    view, scoped to the active server. Admin only.
    Hard-purge uses the existing DELETE /api/parses/{id}?purge=1 and
    /api/parses/batch?ids=...&purge=1."""
    _require_admin(request)
    limit = max(1, min(limit, 1000))
    world = current_world()

    def _query() -> list[dict]:
        if not parses_db.DB_PATH.exists():
            return []
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            return parses_db.list_encounters_for_admin(conn, search=search, limit=limit, world=world)
        finally:
            conn.close()

    rows = await asyncio.get_event_loop().run_in_executor(None, _query)
    return [
        AdminParseItem(
            id=r["id"],
            title=r["title"],
            zone=r["zone"],
            guild_name=r["guild_name"],
            uploaded_by=r["uploaded_by"],
            started_at=r["started_at"],
            duration_s=r["duration_s"],
            success_level=r["success_level"],
            player_count=r["player_count"],
            hidden=bool(r["hidden_at"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Server settings editor
# ---------------------------------------------------------------------------


@router.get("/admin/servers", response_model=list[ServerItem])
async def list_servers_admin(request: Request) -> list[ServerItem]:
    """List all registered servers with their current settings. Admin only."""
    _require_admin(request)
    rows = list_servers_sync()
    return [ServerItem(**r) for r in rows]


@router.put("/admin/servers/{world}", response_model=ServerItem)
async def update_server_settings(
    world: str,
    body: ServerSettingsUpdate,
    request: Request,
) -> ServerItem:
    """Update per-server settings (max_level, current_xpac, launch_dt).

    Returns 404 when ``world`` is not in the registry.
    Validates that ``max_level`` is a positive integer (handled by Pydantic).
    If ``launch_dt`` is provided, it is accepted as-is (ISO-8601 string);
    pass ``null`` to clear it.

    After writing, reloads the in-memory server registry so the change takes
    effect immediately without a restart."""
    _require_admin(request)

    # 404 when the world is not known
    known_worlds = {r["world"] for r in list_servers_sync()}
    if world not in known_worlds:
        raise HTTPException(status_code=404, detail=f"Server {world!r} not found")

    # Validate launch_dt if provided
    if body.launch_dt is not None:
        try:
            datetime.fromisoformat(body.launch_dt.rstrip("Z"))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"launch_dt is not a valid ISO-8601 date/datetime: {body.launch_dt!r}",
            ) from exc

    upsert_server_settings_sync(
        world,
        max_level=body.max_level,
        current_xpac=body.current_xpac,
        launch_dt=body.launch_dt,
    )
    # If the caller is explicitly setting this server as the default, flip it.
    # We never unset a default via is_default=False/None — you can only SET one.
    if body.is_default is True:
        set_default_server_sync(world)
    # Refresh the in-memory registry immediately so new requests see the change.
    server_context.load_registry()

    updated = get_server_by_world_sync(world)
    if updated is None:
        raise HTTPException(status_code=500, detail="Server row disappeared after upsert")
    return ServerItem(**updated)


@router.get("/admin/expansions")
async def list_expansions_admin(request: Request) -> list[dict]:
    """Return distinct expansions (newest first) for populating the admin xpac dropdown.

    Sourced from zones.db.  Returns [] (200) when zones.db is unavailable — never 500.
    """
    _require_admin(request)
    from census import zones_db

    return zones_db.list_expansions()


@router.post("/admin/users/{discord_id}/kick", status_code=200)
async def kick_user(discord_id: str, request: Request) -> dict:
    """
    Deny a user's access and permanently delete all their claims.
    Use this to fully remove a user's presence from the system.
    Admin cannot kick themselves.
    """
    admin = _require_admin(request)
    if discord_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot kick yourself")
    if not await set_user_access(discord_id, "denied"):
        raise HTTPException(status_code=404, detail="User not found")
    count = await delete_claims_for_user(discord_id)
    claim_cache.delete(f"claims:{discord_id}")
    asyncio.create_task(_refresh_claim_cache(discord_id))
    return {"ok": True, "claims_deleted": count}
