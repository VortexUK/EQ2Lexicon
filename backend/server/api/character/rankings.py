"""GET /character/{name}/rankings — WCL-style per-character ranking summary.

Computed over the same primary-boss-kills dataset as /api/rankings (shared
60s TTL cache), grouped by (zone, scope). All percentiles are **within the
character's class** for that boss:

  * ``best_pct``  — rank percentile of the character's best parse among ALL
    parses of that class on that boss (fraction of the pool beaten × 100).
  * ``median_pct`` — median of the character's parses' percentiles.
  * All Stars ``points`` — 100 × (best score / class record): closeness to
    the class record, deliberately distinct from the rank-based best_pct.
    ``rank`` — position among same-class characters' bests on that boss.
    The zone header aggregates: points summed over the zone's bosses, rank
    recomputed among class peers on that sum.

Both metrics (dps + hps) are computed in one pass so the frontend toggle
never refetches. A character with zero ranked kills gets ``zones: []`` —
the character page uses that to hide the tab entirely.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.server.api.character import router
from backend.server.api.rankings import _cached_kills, _is_player_combatant
from backend.server.auth_deps import require_user_session as _require_user
from backend.server.core.executor import run_sync
from backend.server.core.validation import validate_character_name
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_METRICS: dict[str, str] = {"dps": "encdps", "hps": "enchps"}


class BossMetricStats(BaseModel):
    best_pct: int
    best_score: float
    median_pct: int
    encounter_id: int  # the best parse, for linking
    points: float  # All Stars: 100 × best / class record
    rank: int  # among same-class characters' bests
    out_of: int


class BossRankingRow(BaseModel):
    boss: str
    kills: int
    fastest_s: int
    fastest_encounter_id: int
    dps: BossMetricStats | None = None
    hps: BossMetricStats | None = None


class ZoneAllStars(BaseModel):
    points: float
    rank: int
    out_of: int


class ZoneRankings(BaseModel):
    zone: str
    scope: str  # "raid" | "group"
    bosses: list[BossRankingRow]
    dps_allstars: ZoneAllStars | None = None
    hps_allstars: ZoneAllStars | None = None


class CharacterRankingsResponse(BaseModel):
    name: str
    cls: str | None = None
    zones: list[ZoneRankings]


def _rank_pct(score: float, pool: list[float]) -> int:
    """Rank percentile: share of the pool this score beats, ×100. A pool of
    one (only your own parses) reads 100 — you hold the class record."""
    others = len(pool) - 1
    if others <= 0:
        return 100
    below = sum(1 for v in pool if v < score)
    return round(100 * below / others)


def _build_character_rankings(name: str, world: str) -> CharacterRankingsResponse:
    kills = _cached_kills(world)
    target = name.strip().lower()

    # One pass over the dataset. Keys are (zone, scope, boss).
    pools: dict[tuple, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: {m: defaultdict(list) for m in _METRICS}
    )  # key -> metric -> cls -> all parse scores
    bests: dict[tuple, dict[str, dict[str, dict[str, float]]]] = defaultdict(
        lambda: {m: defaultdict(dict) for m in _METRICS}
    )  # key -> metric -> cls -> char_lower -> best score
    # Target-character accumulators.
    t_scores: dict[tuple, dict[str, list[tuple[float, int]]]] = defaultdict(lambda: {m: [] for m in _METRICS})
    t_kills: dict[tuple, list[tuple[int, int]]] = defaultdict(list)  # key -> [(duration_s, encounter_id)]
    t_cls: dict[tuple, tuple[int, str]] = {}  # key -> (started_at, cls) latest
    latest_cls: tuple[int, str] | None = None

    for k in kills:
        key = (k["zone"], k["scope"], k["title"])
        for c in k["combatants"]:
            if not _is_player_combatant(c) or not c.get("cls"):
                continue
            cls = c["cls"]
            cname = c["name"].strip().lower()
            is_target = cname == target
            for metric, field in _METRICS.items():
                score = c.get(field) or 0.0
                if score <= 0:
                    continue
                pools[key][metric][cls].append(score)
                cur = bests[key][metric][cls].get(cname)
                if cur is None or score > cur:
                    bests[key][metric][cls][cname] = score
                if is_target:
                    t_scores[key][metric].append((score, k["id"]))
            if is_target:
                t_kills[key].append((k["duration_s"], k["id"]))
                stamped = (k["started_at"], cls)
                if key not in t_cls or stamped[0] >= t_cls[key][0]:
                    t_cls[key] = stamped
                if latest_cls is None or stamped[0] >= latest_cls[0]:
                    latest_cls = stamped

    if not t_kills:
        return CharacterRankingsResponse(name=name, cls=None, zones=[])

    # Per-boss rows, bucketed by (zone, scope) section.
    sections: dict[tuple[str, str], list[BossRankingRow]] = defaultdict(list)

    for key, fights in sorted(t_kills.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        zone, scope, boss = key
        cls = t_cls[key][1]
        fastest_s, fastest_id = min(fights)
        row = BossRankingRow(boss=boss, kills=len(fights), fastest_s=fastest_s, fastest_encounter_id=fastest_id)
        for metric in _METRICS:
            scores = t_scores[key][metric]
            if not scores:
                continue
            pool = pools[key][metric][cls]
            best_score, best_id = max(scores)
            class_bests = bests[key][metric][cls]
            record = max(class_bests.values())
            stats = BossMetricStats(
                best_pct=_rank_pct(best_score, pool),
                best_score=best_score,
                median_pct=round(median(_rank_pct(s, pool) for s, _ in scores)),
                encounter_id=best_id,
                points=round(100 * best_score / record, 2) if record else 0.0,
                rank=1 + sum(1 for v in class_bests.values() if v > best_score),
                out_of=len(class_bests),
            )
            setattr(row, metric, stats)
        sections[(zone, scope)].append(row)

    zones: list[ZoneRankings] = []
    for (zone, scope), rows in sections.items():
        z = ZoneRankings(zone=zone, scope=scope, bosses=rows)
        # The target's class for this zone: their most recent parse's class
        # among the zone's bosses.
        cls = max(t_cls[k] for k in t_cls if k[0] == zone and k[1] == scope)[1]
        for metric in _METRICS:
            # Zone All Stars: sum each class peer's per-boss points over ALL
            # of the zone's bosses (not just the ones the target killed), so
            # peers with broader coverage rank ahead — a missing boss simply
            # contributes 0 points.
            totals: dict[str, float] = defaultdict(float)
            for bkey, per_metric in bests.items():
                if bkey[0] != zone or bkey[1] != scope:
                    continue
                class_bests = per_metric[metric][cls]
                if not class_bests:
                    continue
                record = max(class_bests.values())
                for peer, peer_best in class_bests.items():
                    totals[peer] += 100 * peer_best / record if record else 0.0
            mine = totals.get(target)
            if mine is None:
                continue
            allstars = ZoneAllStars(
                points=round(mine, 2),
                rank=1 + sum(1 for v in totals.values() if v > mine),
                out_of=len(totals),
            )
            setattr(z, f"{metric}_allstars", allstars)
        zones.append(z)

    # Raid sections first, then dungeons; alphabetical within.
    zones.sort(key=lambda z: (z.scope != "raid", z.zone.lower()))
    return CharacterRankingsResponse(name=name, cls=latest_cls[1] if latest_cls else None, zones=zones)


@router.get("/character/{name}/rankings", response_model=CharacterRankingsResponse)
@limiter.limit("60/minute")
async def get_character_rankings(request: Request, name: str) -> CharacterRankingsResponse:
    _require_user(request)
    valid = validate_character_name(name)
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid character name")
    # Resolve world in the async handler — contextvars don't cross into the
    # executor thread (same rule as /api/rankings).
    world = current_world()
    return await run_sync(_build_character_rankings, valid, world)
