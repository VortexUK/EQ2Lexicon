"""
GET /api/parses          — paginated list of recent encounters.
GET /api/parses/{id}     — encounter detail with combatants + top attacks each.

Reads from the local `data/parses/parses.db` populated by `parses.ingest`.
Sync DB helpers from `parses.db` are dispatched to a thread via
run_in_executor — same pattern as web/routes/recipes.py.

Auth: any authenticated session can read. Officer-only / guild-scoped
filtering is a Phase 3 concern (when uploads are added).
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from parses import db as parses_db
from web.limiter import limiter

router = APIRouter(tags=["parses"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ParseEncounterSummary(BaseModel):
    id: int
    act_encid: str
    title: str
    zone: str | None
    started_at: int  # unix seconds, UTC
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    combatant_count: int


class ParsesListResponse(BaseModel):
    results: list[ParseEncounterSummary]
    total: int


class AttackSummary(BaseModel):
    attack_name: str
    damage: int
    hits: int
    swings: int
    crit_perc: float
    max_hit: int


class CombatantSummary(BaseModel):
    id: int
    name: str
    ally: bool
    duration_s: int
    damage: int
    damage_perc: float
    dps: float
    encdps: float
    healed: int
    enchps: float
    deaths: int
    kills: int
    crit_hits: int
    crit_dam_perc: float
    damage_taken: int
    top_attacks: list[AttackSummary]


class ParseDetailResponse(BaseModel):
    id: int
    act_encid: str
    title: str
    zone: str | None
    started_at: int
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    combatants: list[CombatantSummary]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# Sync query helpers (run via run_in_executor)
# ---------------------------------------------------------------------------


def _list_encounters_sync(limit: int, zone: str | None) -> tuple[list[dict], int]:
    """Return (encounters_with_combatant_count, total_count)."""
    if not parses_db.DB_PATH.exists():
        return [], 0
    conn = parses_db.init_db()
    try:
        encounters = parses_db.recent_encounters(conn, limit=limit, zone=zone)
        # Attach combatant_count per encounter in a single query.
        if encounters:
            ids = tuple(e["id"] for e in encounters)
            placeholders = ",".join("?" * len(ids))
            counts = {
                row[0]: row[1]
                for row in conn.execute(
                    f"SELECT encounter_id, COUNT(*) FROM combatants "
                    f"WHERE encounter_id IN ({placeholders}) GROUP BY encounter_id",
                    ids,
                ).fetchall()
            }
            for e in encounters:
                e["combatant_count"] = counts.get(e["id"], 0)
        if zone:
            total = conn.execute("SELECT COUNT(*) FROM encounters WHERE zone = ?", (zone,)).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM encounters").fetchone()[0]
        return encounters, total
    finally:
        conn.close()


def _encounter_detail_sync(encounter_id: int, top_attacks_per_combatant: int) -> dict | None:
    """Return the encounter + its combatants + top attacks per combatant."""
    if not parses_db.DB_PATH.exists():
        return None
    conn = parses_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        enc_row = conn.execute("SELECT * FROM encounters WHERE id = ?", (encounter_id,)).fetchone()
        if enc_row is None:
            return None
        enc = dict(enc_row)

        combatants = parses_db.get_combatants_for_encounter(conn, enc["id"])
        for c in combatants:
            c["top_attacks"] = parses_db.get_top_attacks_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["ally"] = bool(c["ally"])
        enc["combatants"] = combatants
        return enc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/parses", response_model=ParsesListResponse)
@limiter.limit("30/minute")
async def list_parses(
    request: Request,
    limit: int = 25,
    zone: str | None = None,
) -> ParsesListResponse:
    _require_user(request)

    # Clamp limit so a hostile caller can't ask for millions.
    limit = max(1, min(limit, 100))

    loop = asyncio.get_event_loop()
    encounters, total = await loop.run_in_executor(None, _list_encounters_sync, limit, zone)

    results = [
        ParseEncounterSummary(
            id=e["id"],
            act_encid=e["act_encid"],
            title=e["title"],
            zone=e["zone"],
            started_at=e["started_at"],
            ended_at=e["ended_at"],
            duration_s=e["duration_s"],
            total_damage=e["total_damage"],
            encdps=e["encdps"],
            kills=e["kills"],
            deaths=e["deaths"],
            combatant_count=e.get("combatant_count", 0),
        )
        for e in encounters
    ]
    return ParsesListResponse(results=results, total=total)


@router.get("/parses/{encounter_id}", response_model=ParseDetailResponse)
@limiter.limit("60/minute")
async def get_parse(
    request: Request,
    encounter_id: int,
    top_attacks: int = 15,
) -> ParseDetailResponse:
    _require_user(request)

    top_attacks = max(1, min(top_attacks, 50))

    loop = asyncio.get_event_loop()
    enc = await loop.run_in_executor(None, _encounter_detail_sync, encounter_id, top_attacks)
    if enc is None:
        raise HTTPException(status_code=404, detail="Parse not found")

    combatants = [
        CombatantSummary(
            id=c["id"],
            name=c["name"],
            ally=c["ally"],
            duration_s=c["duration_s"],
            damage=c["damage"],
            damage_perc=c["damage_perc"],
            dps=c["dps"],
            encdps=c["encdps"],
            healed=c["healed"],
            enchps=c["enchps"],
            deaths=c["deaths"],
            kills=c["kills"],
            crit_hits=c["crit_hits"],
            crit_dam_perc=c["crit_dam_perc"],
            damage_taken=c["damage_taken"],
            top_attacks=[
                AttackSummary(
                    attack_name=a["attack_name"],
                    damage=a["damage"],
                    hits=a["hits"],
                    swings=a["swings"],
                    crit_perc=a["crit_perc"],
                    max_hit=a["max_hit"],
                )
                for a in c["top_attacks"]
            ],
        )
        for c in enc["combatants"]
    ]
    return ParseDetailResponse(
        id=enc["id"],
        act_encid=enc["act_encid"],
        title=enc["title"],
        zone=enc["zone"],
        started_at=enc["started_at"],
        ended_at=enc["ended_at"],
        duration_s=enc["duration_s"],
        total_damage=enc["total_damage"],
        encdps=enc["encdps"],
        kills=enc["kills"],
        deaths=enc["deaths"],
        combatants=combatants,
    )
