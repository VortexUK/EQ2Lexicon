"""
GET /api/rankings/filters  — the smart-dropdown tree (scopes -> zones -> bosses).
GET /api/rankings          — a ranked board for one (size, zone, boss, metric[, class]).

Computed-on-read over the existing parses tables (no separate ranking store).
Boss kills are detected with parses.boss.is_boss, mirror-grouped to their
primary upload, then ranked. Soft-deleted parses still rank (the leaderboard
ignores hidden_at); only a hard purge removes them. See
docs/superpowers/specs/2026-05-25-eq2logs-rankings-design.md.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from parses import db as parses_db
from parses.boss import is_boss
from web.auth_deps import require_user_session as _require_user
from web.cache import TTLCache
from web.limiter import limiter
from web.routes.parses import _PLAYER_COUNT_SQL, _group_into_fights

router = APIRouter(tags=["rankings"])

# Raid spans 12 and 24; the table's Size column shows the real count.
_SCOPES: dict[str, tuple[int, int]] = {"group": (2, 6), "raid": (7, 24)}
_SCOPE_LABELS = {"group": "Group", "raid": "Raid"}
_METRIC_FIELD = {"dps": "encdps", "hps": "enchps"}  # speed handled separately

# Short-lived cache of the expensive load+group step (boards are cheap on top).
rankings_cache: TTLCache = TTLCache(ttl=60, max_age=600, name="rankings", maxsize=4)
_KILLS_KEY = "primary_boss_kills"


def _percentile(rank: int, n: int) -> int:
    """Rank-based percentile, 1 = best. Best is always 100; n=4 -> 100/75/50/25."""
    if n <= 0:
        return 0
    return round(100 * (n - rank + 1) / n)


def _scope_for(player_count: int) -> str | None:
    for scope, (lo, hi) in _SCOPES.items():
        if lo <= player_count <= hi:
            return scope
    return None


def _is_player_combatant(c: dict) -> bool:
    name = (c.get("name") or "").strip()
    return bool(c.get("ally")) and bool(name) and " " not in name and name != "Unknown"


def _build_character_board(
    kills: list[dict], *, size: str, zone: str, boss: str, metric: str
) -> tuple[list[dict], list[str]]:
    """Per-character best for Damage/Healing. Returns (rows sorted by score
    desc, sorted class list). Percentile is computed within each class."""
    field = _METRIC_FIELD.get(metric)
    if field is None:
        raise ValueError(f"Unsupported metric for character board: {metric!r}")
    best: dict[str, dict] = {}  # name.lower() -> entry
    for k in kills:
        if k["scope"] != size or k["zone"] != zone or k["title"] != boss:
            continue
        for c in k["combatants"]:
            if not _is_player_combatant(c) or not c.get("cls"):
                continue
            score = c.get(field) or 0.0
            key = c["name"].strip().lower()
            cur = best.get(key)
            if cur is None or score > cur["score"]:
                best[key] = {
                    "kind": "character",
                    "name": c["name"].strip(),
                    "guild_name": c.get("guild_name"),
                    "level": c.get("level"),
                    "cls": c["cls"],
                    "score": score,
                    "encounter_id": k["id"],
                    "size": k["player_count"],
                    "started_at": k["started_at"],
                }
    entries = list(best.values())
    by_cls: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_cls[e["cls"]].append(e)
    for cls_rows in by_cls.values():
        cls_rows.sort(key=lambda e: e["score"], reverse=True)
        n = len(cls_rows)
        for i, e in enumerate(cls_rows):
            e["percentile"] = _percentile(i + 1, n)
    entries.sort(key=lambda e: e["score"], reverse=True)
    return entries, sorted(by_cls.keys())


def _build_speed_board(kills: list[dict], *, size: str, zone: str, boss: str) -> list[dict]:
    """Per-guild fastest clear. Returns rows sorted by time asc with percentile."""
    best: dict[str, dict] = {}  # guild.lower() -> entry
    for k in kills:
        if k["scope"] != size or k["zone"] != zone or k["title"] != boss:
            continue
        guild = (k.get("guild_name") or "").strip()
        if not guild:
            continue
        cur = best.get(guild.lower())
        if cur is None or k["duration_s"] < cur["duration_s"]:
            best[guild.lower()] = {
                "kind": "guild",
                "guild_name": guild,
                "duration_s": k["duration_s"],
                "encounter_id": k["id"],
                "size": k["player_count"],
                "started_at": k["started_at"],
            }
    rows = sorted(best.values(), key=lambda e: e["duration_s"])
    n = len(rows)
    for i, e in enumerate(rows):
        e["percentile"] = _percentile(i + 1, n)
    return rows


def _build_filters(kills: list[dict]) -> dict:
    """Scope -> zone -> boss tree for the dropdowns, populated from the data."""
    tree: dict[str, dict[str, set]] = {"raid": defaultdict(set), "group": defaultdict(set)}
    for k in kills:
        scope = k.get("scope")
        if scope not in tree:
            continue
        tree[scope][k.get("zone") or "(unknown zone)"].add(k["title"])
    return {
        "scopes": [
            {
                "key": scope,
                "label": _SCOPE_LABELS[scope],
                "zones": [{"zone": z, "bosses": sorted(bosses)} for z, bosses in sorted(zones.items())],
            }
            for scope, zones in tree.items()
            if zones
        ]
    }


def _load_primary_boss_kills() -> list[dict]:
    """Load winning boss-kill encounters, mirror-group them, and return one
    'primary' (longest) upload per fight with its combatants attached. Ignores
    hidden_at so soft-deleted parses still rank. Called from an executor by the
    async endpoints."""
    if not parses_db.DB_PATH.exists():
        return []
    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT e.id, e.title, e.zone, e.guild_name, e.uploaded_by,
                   e.started_at, e.duration_s, e.success_level,
                   ({_PLAYER_COUNT_SQL}) AS player_count
            FROM encounters e
            WHERE e.success_level = 1
            ORDER BY e.started_at DESC
            """
        ).fetchall()
        encs = [dict(r) for r in rows if is_boss(r["title"])]
        kills: list[dict] = []
        for g in _group_into_fights(encs):
            scope = _scope_for(g.get("player_count") or 0)
            if scope is None:
                continue
            kills.append(
                {
                    "id": g["id"],
                    "title": g["title"],
                    "zone": g["zone"],
                    "guild_name": g.get("guild_name"),
                    "started_at": g["started_at"],
                    "duration_s": g["duration_s"],
                    "player_count": g.get("player_count") or 0,
                    "scope": scope,
                    "combatants": parses_db.get_combatants_for_encounter(conn, g["id"]),
                }
            )
        return kills
    finally:
        conn.close()


def _cached_kills() -> list[dict]:
    cached = rankings_cache.get(_KILLS_KEY)
    if cached is not None:
        return cached
    kills = _load_primary_boss_kills()
    rankings_cache.set(_KILLS_KEY, kills)
    return kills


class RankingRow(BaseModel):
    kind: str  # "character" | "guild"
    encounter_id: int
    percentile: int
    size: int
    started_at: int
    # character rows
    name: str | None = None
    guild_name: str | None = None
    level: int | None = None
    cls: str | None = None
    score: float | None = None
    # guild rows (Speed)
    duration_s: int | None = None


class RankingsResponse(BaseModel):
    rows: list[RankingRow]
    classes: list[str]
    total: int


@router.get("/rankings/filters")
@limiter.limit("60/minute")
async def get_ranking_filters(request: Request) -> dict:
    _require_user(request)
    loop = asyncio.get_event_loop()
    kills = await loop.run_in_executor(None, _cached_kills)
    return _build_filters(kills)


@router.get("/rankings", response_model=RankingsResponse)
@limiter.limit("60/minute")
async def get_rankings(
    request: Request,
    size: str,
    zone: str,
    boss: str,
    metric: str,
    class_name: str | None = Query(None, alias="class"),
) -> RankingsResponse:
    _require_user(request)
    if size not in _SCOPES:
        raise HTTPException(status_code=400, detail="size must be 'raid' or 'group'")
    if metric not in ("dps", "hps", "speed"):
        raise HTTPException(status_code=400, detail="metric must be 'dps', 'hps' or 'speed'")

    loop = asyncio.get_event_loop()
    kills = await loop.run_in_executor(None, _cached_kills)

    if metric == "speed":
        rows = _build_speed_board(kills, size=size, zone=zone, boss=boss)
        classes: list[str] = []
    else:
        rows, classes = _build_character_board(kills, size=size, zone=zone, boss=boss, metric=metric)
        if class_name:
            rows = [r for r in rows if r["cls"] == class_name]

    return RankingsResponse(
        rows=[RankingRow(**r) for r in rows],
        classes=classes,
        total=len(rows),
    )
