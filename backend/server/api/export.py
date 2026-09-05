"""Read-only export API for third-party tools (v1) — issue #219 (Warboard).

Purpose-built, versioned endpoints under /api/export/v1/* so external
consumers never couple to the frontend-shaped internals:

  GET /api/export/v1/filters               — valid sizes/zones/bosses/classes
  GET /api/export/v1/rankings              — per-character ranking rows
  GET /api/export/v1/parses/{ids}/abilities — ability breakdowns (<=20 ids)

Safety model:
  * Auth = bearer API token (the same tokens the ACT plugin mints) PLUS the
    admin-granted 'api' role — read access is opt-in per account, revocable
    two ways, and every call is attributable. Site admins pass.
  * Data mirrors what any logged-in site user can already see: the curated
    rankings dataset (which deliberately keeps soft-hidden parses — that is
    the point of soft-delete) and parse ability tables. Uploader identities
    (source_dsn discord ids) are never included.
  * Rate limits keyed by token; responses are versioned pydantic models —
    the stable contract. Breaking changes mean /v2/, never a mutation here.
"""

from __future__ import annotations

import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.server.api.parses.list import _encounter_detail_sync
from backend.server.api.rankings import (
    _apply_percentiles,
    _build_character_board,
    _build_filters,
    _cached_kills,
    _is_player_combatant,
)
from backend.server.auth_deps import is_admin, require_user_session_or_token
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser, TokenUser
from backend.server.db import has_role
from backend.server.limiter import limiter, upload_rate_key
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["export"])

_MAX_ABILITY_IDS = 20
_TOP_ATTACKS = 50


async def _require_api_consumer(request: Request) -> SessionUser | TokenUser:
    """Bearer token + the admin-granted 'api' role (admins pass)."""
    user = await require_user_session_or_token(request)
    if is_admin(cast("SessionUser", user)):
        return user
    if not await has_role(str(user["id"]), "api"):
        raise HTTPException(
            status_code=403,
            detail="Read-API access requires the 'api' role — ask the site admin (see issue #219).",
        )
    return user


# ---------------------------------------------------------------------------
# v1 response models — the stable contract
# ---------------------------------------------------------------------------


class ExportRankingRow(BaseModel):
    rank: int
    name: str
    guild_name: str | None = None
    cls: str | None = None
    level: int | None = None
    ilvl: float | None = None
    value: float  # dps or hps depending on the metric requested
    percentile: int | None = None
    encounter_id: int
    player_count: int | None = None
    started_at: int


class ExportRankingsResponse(BaseModel):
    schema_version: int = 1
    world: str
    size: str
    zone: str
    boss: str
    metric: str
    rows: list[ExportRankingRow]


class ExportAbility(BaseModel):
    name: str
    victim: str | None = None
    swing_type: int | None = None
    damage: int
    hits: int
    crit_hits: int
    swings: int
    max_hit: int
    dps: float | None = None
    crit_perc: float | None = None
    resist: str | None = None


class ExportPlayer(BaseModel):
    name: str
    cls: str | None = None
    level: int | None = None
    ilvl: float | None = None
    guild_name: str | None = None
    damage: int
    encdps: float | None = None
    healed: int
    enchps: float | None = None
    deaths: int
    abilities: list[ExportAbility]
    heals: list[ExportAbility]


class ExportParseAbilities(BaseModel):
    id: int
    title: str
    zone: str | None = None
    started_at: int
    duration_s: int
    success_level: int
    players: list[ExportPlayer]


class ExportAbilitiesResponse(BaseModel):
    schema_version: int = 1
    world: str
    parses: list[ExportParseAbilities]
    missing: list[int]  # requested ids with no encounter on this world


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/export/v1/filters")
@limiter.limit("30/hour", key_func=upload_rate_key)
async def export_filters(request: Request) -> dict:
    """Valid sizes/zones/bosses (+ classes per board) for /rankings params."""
    await _require_api_consumer(request)
    world = current_world()
    kills = await run_sync(_cached_kills, world)
    return {"schema_version": 1, "world": world, **_build_filters(kills)}


@router.get("/export/v1/rankings", response_model=ExportRankingsResponse)
@limiter.limit("300/hour", key_func=upload_rate_key)
async def export_rankings(
    request: Request,
    size: str,
    zone: str,
    boss: str,
    metric: str = "dps",
    class_name: str | None = Query(None, alias="class"),
) -> ExportRankingsResponse:
    """Per-character best-parse board for one boss — the same curated
    primary-kill dataset the site ranks on."""
    await _require_api_consumer(request)
    if metric not in ("dps", "hps"):
        raise HTTPException(status_code=400, detail="metric must be 'dps' or 'hps'")

    world = current_world()
    kills = await run_sync(_cached_kills, world)
    rows, _classes = _build_character_board(kills, size=size, zone=zone, boss=boss, metric=metric)
    if class_name:
        rows = [r for r in rows if r["cls"] == class_name]
    _apply_percentiles(rows, score_key="score", higher_better=True)

    return ExportRankingsResponse(
        world=world,
        size=size,
        zone=zone,
        boss=boss,
        metric=metric,
        rows=[
            ExportRankingRow(
                rank=i + 1,
                name=r["name"],
                guild_name=r.get("guild_name"),
                cls=r.get("cls"),
                level=r.get("level"),
                ilvl=r.get("ilvl"),
                value=r["score"],
                percentile=r.get("percentile"),
                encounter_id=r["encounter_id"],
                player_count=r.get("size"),
                started_at=r["started_at"],
            )
            for i, r in enumerate(rows)
        ],
    )


@router.get("/export/v1/parses/{ids}/abilities", response_model=ExportAbilitiesResponse)
@limiter.limit("300/hour", key_func=upload_rate_key)
async def export_parse_abilities(request: Request, ids: str) -> ExportAbilitiesResponse:
    """Ability breakdowns for up to 20 comma-separated encounter ids.

    Player combatants only; per player the top damage abilities and heals
    (up to 50 each). Ids that don't exist on this world land in `missing`
    rather than failing the batch."""
    await _require_api_consumer(request)

    id_list: list[int] = []
    for tok in ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            id_list.append(int(tok))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid encounter id: {tok!r}") from None
    id_list = list(dict.fromkeys(id_list))
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must not be empty")
    if len(id_list) > _MAX_ABILITY_IDS:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_ABILITY_IDS} ids per call")

    world = current_world()
    parses: list[ExportParseAbilities] = []
    missing: list[int] = []
    for enc_id in id_list:
        enc = await run_sync(_encounter_detail_sync, enc_id, _TOP_ATTACKS, world)
        if enc is None:
            missing.append(enc_id)
            continue
        players = [
            ExportPlayer(
                name=c["name"],
                cls=c.get("cls"),
                level=c.get("level"),
                ilvl=c.get("ilvl"),
                guild_name=c.get("guild_name"),
                damage=c.get("damage") or 0,
                encdps=c.get("encdps"),
                healed=c.get("healed") or 0,
                enchps=c.get("enchps"),
                deaths=c.get("deaths") or 0,
                abilities=[_ability(a) for a in c.get("top_attacks", [])],
                heals=[_ability(a) for a in c.get("top_heals", [])],
            )
            # Same predicate the rankings dataset uses — a ranked character
            # must always appear here (the strict is_player column leaves
            # small parses' allies unconfirmed).
            for c in enc.get("combatants", [])
            if _is_player_combatant(c)
        ]
        parses.append(
            ExportParseAbilities(
                id=enc["id"],
                title=enc["title"],
                zone=enc.get("zone"),
                started_at=enc["started_at"],
                duration_s=enc["duration_s"],
                success_level=enc.get("success_level") or 0,
                players=players,
            )
        )
    return ExportAbilitiesResponse(world=world, parses=parses, missing=missing)


def _ability(row: dict) -> ExportAbility:
    return ExportAbility(
        name=row.get("attack_name") or row.get("name") or "?",
        victim=row.get("victim"),
        swing_type=row.get("swing_type"),
        damage=row.get("damage") or 0,
        hits=row.get("hits") or 0,
        crit_hits=row.get("crit_hits") or 0,
        swings=row.get("swings") or 0,
        max_hit=row.get("max_hit") or 0,
        dps=row.get("dps"),
        crit_perc=row.get("crit_perc"),
        resist=row.get("resist"),
    )
