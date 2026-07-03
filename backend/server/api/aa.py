from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.census import store as census_store
from backend.census.store import StoreRecord
from backend.eq2db.spells import find_by_crc
from backend.image.aa_tree import detect_tree_type, load_tree_index, tree_max_points, tree_node_costs
from backend.server.cache import aa_cache
from backend.server.constants import CHARACTER_STALE_S
from backend.server.core.cache_keys import aa_cache_key
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.executor import run_sync
from backend.server.server_context import current_server, current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["aa"])

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "AAs"
_TREES_DIR = _DATA_DIR / "trees"
_LIMITS = _DATA_DIR / "aa_limits.json"

_TYPE_ORDER = {
    "class": 0,
    "subclass": 1,
    "shadows": 2,
    "heroic": 3,
    "tradeskill": 4,
    "tradeskill_general": 5,
    "warder": 6,
    "prestige": 7,
    "dragon": 8,
}

# Tradeskill AA tree types — counted and capped SEPARATELY from adventure AAs
# (their own pool, earned from crafting, not bounded by the adventure xpac cap).
_TRADESKILL_TYPES = frozenset({"tradeskill", "tradeskill_general"})

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AAConfigResponse(BaseModel):
    xpac: str
    aa_cap: int  # adventure AA cap (tradeskill excluded)
    tradeskill_aa_cap: int = 0  # Σ max points of the unlocked tradeskill trees
    unlocked_tree_types: list[str]


class AANodeResponse(BaseModel):
    node_id: int
    name: str
    description: str
    classification: str
    xcoord: int
    ycoord: int
    icon_id: int
    backdrop_id: int
    maxtier: int
    pointspertier: int
    points_to_unlock: int
    title: str = ""
    spellcrc: int = 0


class AATreeResponse(BaseModel):
    tree_id: int
    tree_name: str
    tree_type: str
    nodes: list[AANodeResponse]


class CharAATree(BaseModel):
    tree_id: int
    tree_type: str
    tree_name: str
    spent: dict[str, int]  # node_id (str) → tier
    total_spent: int


class CharAAProfile(BaseModel):
    name: str
    trees: list[CharAATree]


class CharAAsResponse(BaseModel):
    character_name: str
    total_spent: int
    trees: list[CharAATree]
    profiles: list[CharAAProfile] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/aa/config", response_model=AAConfigResponse)
async def get_aa_config() -> AAConfigResponse:
    """Return the current xpac's AA cap and which tree types are unlocked."""
    xpac = current_server().current_xpac or ""
    if not _LIMITS.exists():
        return AAConfigResponse(xpac=xpac, aa_cap=0, tradeskill_aa_cap=0, unlocked_tree_types=[])
    limits = json.loads(_LIMITS.read_text(encoding="utf-8"))
    entry = limits.get(xpac, {})
    unlocked = entry.get("unlocked_trees", [])
    # Tradeskill cap = the total the unlocked tradeskill trees add up to
    # (Σ maxtier × pointspertier), derived from the tree data rather than
    # hardcoded. EoF (tradeskill only) → 45; Age of Discovery+ (both) → 116.
    unlocked_ts = _TRADESKILL_TYPES & set(unlocked)
    tradeskill_cap = sum(
        tree_max_points(tid) for tid, info in load_tree_index().items() if info.get("type") in unlocked_ts
    )
    return AAConfigResponse(
        xpac=xpac,
        aa_cap=entry.get("aa_cap", 0),
        tradeskill_aa_cap=tradeskill_cap,
        unlocked_tree_types=unlocked,
    )


@lru_cache(maxsize=128)
def _load_tree_for_response(tree_id: int) -> AATreeResponse | None:
    """Parse + build the AATreeResponse for a single tree id.

    Returns None when the file is missing or has no AA data.

    Invalidation: tree JSON is static reference data on disk; rebuild via a
    process restart. If the data/AAs/trees/ files ever become hot-editable,
    add a sibling _load_tree_for_response.cache_clear() on the mutation
    path — see the canonical pattern at web/routes/rankings.py:195-203
    (invalidate_zones_cache).
    """
    path = _TREES_DIR / f"{tree_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    aa_list = data.get("alternateadvancement_list") or []
    if not aa_list:
        return None
    tree = aa_list[0]
    tree_type = detect_tree_type(data)
    nodes: list[AANodeResponse] = []
    for n in tree.get("alternateadvancementnode_list") or []:
        icon = n.get("icon") or {}
        nodes.append(
            AANodeResponse(
                node_id=int(n["nodeid"]),
                name=str(n.get("name", "")),
                description=str(n.get("description", "")),
                classification=str(n.get("classification", "")),
                xcoord=int(n["xcoord"]),
                ycoord=int(n["ycoord"]),
                icon_id=int(icon.get("id", 0)),
                backdrop_id=int(icon.get("backdrop", -1)),
                maxtier=int(n.get("maxtier", 1)),
                pointspertier=int(n.get("pointspertier", 1)),
                points_to_unlock=int(n.get("pointsspentintreetounlock", 0)),
                title=str(n.get("title", "")),
                spellcrc=int(n.get("spellcrc", 0)),
            )
        )
    return AATreeResponse(
        tree_id=tree_id,
        tree_name=tree.get("name", str(tree_id)),
        tree_type=tree_type,
        nodes=nodes,
    )


@router.get("/aa/tree/{tree_id}", response_model=AATreeResponse)
async def get_aa_tree(tree_id: int) -> AATreeResponse:
    """Return the full node data for an AA tree."""
    result = _load_tree_for_response(tree_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"AA tree {tree_id} not found")
    return result


def _build_trees(aa_list) -> list[CharAATree]:
    """Convert a list of NodeAA objects into CharAATree list, sorted by tree type."""
    by_tree: dict[int, dict[int, int]] = {}
    for aa in aa_list:
        by_tree.setdefault(aa.tree_id, {})[aa.node_id] = aa.tier
    tree_index = load_tree_index()
    result = []
    for tid, spent in by_tree.items():
        info = tree_index.get(tid, {})
        costs = tree_node_costs(tid)
        result.append(
            CharAATree(
                tree_id=tid,
                tree_type=info.get("type", "unknown"),
                tree_name=info.get("name", str(tid)),
                spent={str(k): v for k, v in spent.items()},
                # Points spent = Σ tier × pointspertier (some nodes cost 2/tier).
                total_spent=sum(tier * costs.get(node_id, 1) for node_id, tier in spent.items()),
            )
        )
    result.sort(key=lambda t: _TYPE_ORDER.get(t.tree_type, 99))
    return result


def _aas_response_from_census(char_aas) -> CharAAsResponse:
    """Build a CharAAsResponse from a Census CharacterAAs model object."""
    return CharAAsResponse(
        character_name=char_aas.character_name,
        # Point-accurate: tier × pointspertier per node (see _build_trees).
        total_spent=sum(aa.tier * tree_node_costs(aa.tree_id).get(aa.node_id, 1) for aa in char_aas.aa_list),
        trees=_build_trees(char_aas.aa_list),
        profiles=[CharAAProfile(name=p.name, trees=_build_trees(p.aa_list)) for p in char_aas.profiles],
    )


async def _bg_refresh_aas(name: str, cache_key: str) -> None:
    """Background task: silently re-fetch a character's AAs, persist to
    census_store, and update the in-memory cache."""
    try:
        world = current_world()
        async with shared_census_client() as client:
            char_aas = await client.get_character_aas(name, world)
        if char_aas is not None:
            result = _aas_response_from_census(char_aas)
            now = int(time.time())

            def _write() -> None:
                conn = census_store.init_db(census_store.DB_PATH)
                try:
                    census_store.upsert_character_aas(conn, name, world, result.model_dump(), now=now)
                finally:
                    conn.close()

            await run_sync(_write)
            aa_cache.set(cache_key, result)
    except Exception as exc:
        _log.warning("[cache] Background AA refresh failed for %s: %s", name, exc)


@router.get("/character/{name}/aas", response_model=CharAAsResponse)
async def get_character_aas(name: str) -> CharAAsResponse:
    """Serve last-known AA data instantly from census_store; refresh from
    Census only in the background. Mirrors the character read path so AA
    data survives container restarts the same way.

    Spawned within the request context: asyncio.create_task copies the
    contextvar, so current_world() inside the task resolves to THIS
    request's server even after the middleware resets it post-response.
    """
    world = current_world()
    cache_key = aa_cache_key(name, world)
    now = int(time.time())

    # 1) Hot in-memory copy.
    cached, is_stale = aa_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_bg_refresh_aas(name, cache_key))
        return cached

    # 2) Durable store — serve known-good data without a Census round-trip.
    def _read() -> StoreRecord | None:
        conn = census_store.init_db(census_store.DB_PATH)
        try:
            return census_store.get_character_aas(conn, name, world)
        finally:
            conn.close()

    rec = await run_sync(_read)
    if rec is not None:
        stale = (now - rec["last_resolved_at"]) > CHARACTER_STALE_S
        if stale:
            asyncio.create_task(_bg_refresh_aas(name, cache_key))
        resp = CharAAsResponse(**rec["data"])
        aa_cache.set(cache_key, resp)
        return resp

    # 3) Never seen — try one live fetch.
    from backend.server import census_health

    if census_health.is_down():
        _log.debug("[aa] Skipping live fetch — census_health=down (name=%s)", name)
        raise HTTPException(
            status_code=503,
            detail=f"'{name}' AA data not cached yet and Census is unavailable. Try again shortly.",
        )
    try:
        async with shared_census_client() as client:
            char_aas = await client.get_character_aas(name, world)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=f"'{name}' AA data not cached yet and Census is unavailable. Try again shortly.",
        )
    if char_aas is None:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found")

    result = _aas_response_from_census(char_aas)

    def _write() -> None:
        conn = census_store.init_db(census_store.DB_PATH)
        try:
            census_store.upsert_character_aas(conn, name, world, result.model_dump(), now=now)
        finally:
            conn.close()

    await run_sync(_write)
    aa_cache.set(cache_key, result)
    return result


class SpellEffect(BaseModel):
    description: str
    indentation: int = 0


class SpellEffectsResponse(BaseModel):
    effects: list[SpellEffect]
    matched_tier: int | None = None  # tier row actually found (may differ from requested)
    requested_tier: int | None = None  # tier that was requested


@router.get("/aa/spell/{spellcrc}", response_model=SpellEffectsResponse)
async def get_spell_effects(spellcrc: int, tier: int = 0) -> SpellEffectsResponse:
    """Return the effect lines for an AA node's spell from the local spells DB.

    Pass ?tier=N to get effects for the character's actual spent rank.
    tier=0 (default) falls back to the highest available tier.
    find_by_crc is lru_cache'd so repeated lookups are free.
    """
    row = await asyncio.to_thread(find_by_crc, spellcrc, tier or None)

    effects: list[dict] = []
    matched_tier: int | None = None
    if row:
        matched_tier = row.get("tier")
        if row.get("effects"):
            try:
                effects = json.loads(row.get("effects", "[]"))
            except Exception as exc:
                _log.warning("[aa] Failed to parse effects JSON for crc=%s tier=%s: %s", spellcrc, tier, exc)
                effects = []

    return SpellEffectsResponse(
        effects=[SpellEffect(**e) for e in effects],
        matched_tier=matched_tier,
        requested_tier=tier or None,
    )
