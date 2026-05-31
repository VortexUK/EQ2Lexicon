from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.server import server_context
from backend.server.api.claim import invalidate_user_claim_cache_all_worlds
from backend.server.api.role_requests import RoleRequestEntry
from backend.server.auth_deps import KNOWN_ROLES
from backend.server.auth_deps import require_admin as _require_admin
from backend.server.constants import ADMIN_PARSE_LIST_MAX_LIMIT
from backend.server.core.audit_log import audit_log
from backend.server.core.executor import run_sync
from backend.server.db import (
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
    review_and_grant_role,
    review_claim,
    review_role_request,
    revoke_role,
    set_default_server_sync,
    set_user_access,
    upsert_server_settings_sync,
)
from backend.server.parses import db as parses_db
from backend.server.server_context import current_world

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
    # Soft warnings the plugin (v0.1.15+) attached at upload time —
    # currently just "folder_hint_mismatch". None when the parse had no
    # warnings; admin UI renders a ⚠ chip when non-empty.
    client_warnings: list[str] | None = None


class TamperReportItem(BaseModel):
    """One row from the audit channel. Returned by GET /admin/tamper-reports
    so the admin UI can render the working set with reason chip + encounter
    summary + uploader info."""

    id: int
    world: str
    act_encid: str
    title: str
    zone: str | None = None
    started_at: int
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    # Reason code from the plugin's X-Lexicon-Tamper-Reason header. Stored
    # verbatim — future plugin versions can emit codes we don't yet
    # recognise and they still surface here as an opaque string.
    reason: str
    reported_at: int
    uploader_logger_name: str
    uploader_discord_id: str
    uploader_discord_name: str
    guild_name: str | None = None
    # NULL when pending review (the working-set state).
    acknowledged_at: int | None = None
    acknowledged_by: str | None = None


class TamperReportListResponse(BaseModel):
    results: list[TamperReportItem]
    pending_count: int  # always the count of unacknowledged (regardless of filter)


class AcknowledgeResponse(BaseModel):
    acknowledged: bool


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
    invalidate_user_claim_cache_all_worlds(result["discord_id"])
    audit_log(
        "claim_approved",
        actor=admin["id"],
        claim_id=claim_id,
        character=result["character_name"],
        discord_id=result["discord_id"],
    )
    return ClaimDetail(**result)


@router.delete("/admin/claims/{claim_id}", status_code=200)
async def remove_claim(claim_id: int, request: Request) -> dict:
    """Permanently delete a claim record."""
    _require_admin(request)
    claim = await get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    await delete_claim(claim_id)
    invalidate_user_claim_cache_all_worlds(claim["discord_id"])
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
    invalidate_user_claim_cache_all_worlds(result["discord_id"])
    audit_log(
        "claim_rejected",
        actor=admin["id"],
        claim_id=claim_id,
        character=result["character_name"],
        discord_id=result["discord_id"],
        note=(body.note or "")[:80],
    )
    return ClaimDetail(**result)


@router.delete("/admin/users/{discord_id}/claims", status_code=200)
async def remove_all_user_claims(discord_id: str, request: Request) -> dict:
    """Permanently delete every claim record for a user."""
    admin = _require_admin(request)
    count = await delete_claims_for_user(discord_id)
    invalidate_user_claim_cache_all_worlds(discord_id)
    audit_log(
        "claims_purged_for_user",
        actor=admin["id"],
        discord_id=discord_id,
        count=count,
    )
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
    if inserted:
        audit_log(
            "role_granted",
            actor=admin["id"],
            role=role,
            discord_id=discord_id,
        )
    # Bust the public /api/supporters cache so the new badge appears
    # without waiting for a process restart. Only fires for the
    # supporter role since the cache only tracks that one — keeps
    # contributor grants from doing useless work.
    if role == "supporter":
        from backend.server.api.supporters import invalidate as _invalidate_supporters

        _invalidate_supporters()
    return {"ok": True, "granted": inserted}


@router.delete("/admin/users/{discord_id}/roles/{role}", status_code=200)
async def revoke_user_role(discord_id: str, role: str, request: Request) -> dict:
    """Revoke a role from a user. 404 when the user didn't have the role."""
    admin = _require_admin(request)
    if role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {role!r}. Known roles: {sorted(KNOWN_ROLES)}",
        )
    removed = await revoke_role(discord_id, role)
    if not removed:
        raise HTTPException(status_code=404, detail="User does not have this role")
    audit_log(
        "role_revoked",
        actor=admin["id"],
        role=role,
        discord_id=discord_id,
    )
    if role == "supporter":
        from backend.server.api.supporters import invalidate as _invalidate_supporters

        _invalidate_supporters()
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

    # Single atomic transaction: mark approved + insert user_roles row.
    # A process crash between the two writes can't leave a phantom-approved
    # row whose grant never landed.
    reviewed = await review_and_grant_role(request_id, "approved", admin["id"], body.note)
    if reviewed is None:
        # Lost to a concurrent admin (race). Surface as 409 rather than 200.
        raise HTTPException(status_code=409, detail="Request was reviewed by someone else")
    audit_log(
        "role_request_approved",
        actor=admin["id"],
        request_id=request_id,
        role=reviewed["role"],
        discord_id=reviewed["discord_id"],
    )
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
    audit_log(
        "role_request_rejected",
        actor=admin["id"],
        request_id=request_id,
        role=reviewed["role"],
        discord_id=reviewed["discord_id"],
        note=(body.note or "")[:80],
    )
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
    limit = max(1, min(limit, ADMIN_PARSE_LIST_MAX_LIMIT))
    world = current_world()

    def _query() -> list[dict]:
        if not parses_db.DB_PATH.exists():
            return []
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            return parses_db.list_encounters_for_admin(conn, search=search, limit=limit, world=world)
        finally:
            conn.close()

    rows = await run_sync(_query)
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
            client_warnings=_decode_client_warnings(r.get("client_warnings")),
        )
        for r in rows
    ]


def _decode_client_warnings(raw: str | None) -> list[str] | None:
    """Decode the JSON-encoded ``encounters.client_warnings`` column for
    response shaping. Returns None for NULL / empty / unparseable values
    so the frontend can use a single ``warnings?.length`` check to decide
    whether to render the chip.

    Storage-side sanitisation (see ``_insert_encounter_rows_sync``) keeps
    the values to a known list-of-strings shape, so decode failures here
    really do mean "the column is corrupt" — fail closed by returning
    None rather than surfacing garbage to the admin UI.
    """
    if not raw:
        return None
    import json

    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, list):
        return None
    result = [str(x) for x in decoded if isinstance(x, str) and x]
    return result or None


# ---------------------------------------------------------------------------
# Tamper-report audit panel
# ---------------------------------------------------------------------------


@router.get("/admin/tamper-reports", response_model=TamperReportListResponse)
async def list_tamper_reports_admin(
    request: Request,
    status: Literal["pending", "ack", "all"] = "pending",
    reason: str | None = None,
    limit: int = 200,
) -> TamperReportListResponse:
    """Audit channel for plugin-detected tamper attempts (see
    web/routes/parses/tamper_report.py).

    Defaults to ``status="pending"`` — the admin's working set of
    unreviewed reports. ``reason`` filters to one specific code
    (``title_enemy_mismatch`` / ``stale_encounter`` /
    ``recent_import_activity``). The ``pending_count`` field on the
    response is ALWAYS the count of unacknowledged reports regardless
    of which filter the admin is currently viewing, so the panel can
    show "N pending" in its header without a second request.

    Scoped to the active server via ``current_world()``; pass
    ``status="all"`` + ``reason=None`` to see everything for this world.
    """
    _require_admin(request)
    limit = max(1, min(limit, ADMIN_PARSE_LIST_MAX_LIMIT))
    world = current_world()

    def _query() -> tuple[list[dict], int]:
        if not parses_db.DB_PATH.exists():
            return [], 0
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            rows = parses_db.list_tamper_reports(
                conn,
                world=world,
                reason=reason,
                status=status,
                limit=limit,
            )
            pending = parses_db.count_pending_tamper_reports(conn, world=world)
            return rows, pending
        finally:
            conn.close()

    rows, pending_count = await run_sync(_query)
    return TamperReportListResponse(
        results=[
            TamperReportItem(
                id=r["id"],
                world=r["world"],
                act_encid=r["act_encid"],
                title=r["title"],
                zone=r["zone"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                duration_s=r["duration_s"],
                total_damage=r["total_damage"],
                encdps=r["encdps"],
                reason=r["reason"],
                reported_at=r["reported_at"],
                uploader_logger_name=r["uploader_logger_name"] or "",
                uploader_discord_id=r["uploader_discord_id"] or "",
                uploader_discord_name=r["uploader_discord_name"] or "",
                guild_name=r["guild_name"],
                acknowledged_at=r["acknowledged_at"],
                acknowledged_by=r["acknowledged_by"],
            )
            for r in rows
        ],
        pending_count=pending_count,
    )


@router.post(
    "/admin/tamper-reports/{report_id}/acknowledge",
    response_model=AcknowledgeResponse,
)
async def acknowledge_tamper_report(
    report_id: int,
    request: Request,
) -> AcknowledgeResponse:
    """Mark a tamper report as reviewed. One-way — no unacknowledge.
    Returns ``acknowledged=True`` if a pending row was flipped, False if
    the report didn't exist OR was already acknowledged.

    Acknowledge stamps the admin's Discord id, mirroring how role-grant
    audit rows record the actor.
    """
    user = _require_admin(request)
    actor_id = str(user.get("id") or "")
    now_unix = int(datetime.now().timestamp())

    def _ack() -> bool:
        if not parses_db.DB_PATH.exists():
            return False
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            return parses_db.acknowledge_tamper_report(
                conn,
                report_id,
                acknowledged_at=now_unix,
                acknowledged_by=actor_id,
            )
        finally:
            conn.close()

    flipped = await run_sync(_ack)
    if flipped:
        audit_log(
            "tamper_report.acknowledge",
            actor=actor_id,
            report_id=report_id,
        )
    return AcknowledgeResponse(acknowledged=flipped)


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
    admin = _require_admin(request)

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
    audit_log(
        "server_settings_updated",
        actor=admin["id"],
        world=world,
        max_level=body.max_level,
        xpac=body.current_xpac,
        launch_dt=body.launch_dt,
        is_default=body.is_default,
    )
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
    from backend.eq2db import zones as zones_db

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
    invalidate_user_claim_cache_all_worlds(discord_id)
    audit_log(
        "user_kicked",
        actor=admin["id"],
        discord_id=discord_id,
        claims_deleted=count,
    )
    return {"ok": True, "claims_deleted": count}
