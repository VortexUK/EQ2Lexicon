"""
GET /api/recipes/search  — paginated recipe search with optional filters.

Filters
-------
q           partial name match (case-insensitive)
tier        crafting tier: T1 – T14  (T1 = levels 1-9, T2 = 10-19, …)
            Determined by the fuel component prefix:
            Basic=T1, Glowing=T2, Smoldering=T3, Sparkling=T4, …
bench       raw bench key (e.g. "work_desk", "forge") or the display label
            (e.g. "Sage", "Armorer") — both are accepted.
class_name  adventure class name (lowercase) — matched against the
            classes_json column of the output item in the items DB.
            Uses ATTACH + subquery so the items DB is never fully loaded.
page        1-based page index (default 1)
"""

from __future__ import annotations

import json
import logging
import sqlite3

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.eq2db.classes import iter_adventure_class_names
from backend.eq2db.items import DB_PATH as ITEMS_DB_PATH
from backend.eq2db.recipes import DB_PATH as RECIPES_DB_PATH
from backend.server.core.executor import run_sync

_log = logging.getLogger(__name__)

router = APIRouter(tags=["recipes"])

# ---------------------------------------------------------------------------
# Crafting tier → fuel prefix  (T1 = levels 1-9, T2 = 10-19, …)
# ---------------------------------------------------------------------------

TIER_FUEL: dict[str, str] = {
    "T1": "Basic",
    "T2": "Glowing",
    "T3": "Smoldering",
    "T4": "Sparkling",
    "T5": "Scintillating",
    "T6": "Glimmering",
    "T7": "Lambent",
    "T8": "Luminous",
    "T9": "Ethereal",
    "T10": "Celestial",
    "T11": "Coruscating",
    "T12": "Exultant",
    "T13": "Thaumic",
    "T14": "Formless",
}

CRAFT_TIERS: list[str] = list(TIER_FUEL.keys())  # T1 … T14

# Reverse map: fuel prefix (lower) → tier label
_FUEL_PREFIX_TO_TIER: dict[str, str] = {prefix.lower(): tier for tier, prefix in TIER_FUEL.items()}

# ---------------------------------------------------------------------------
# Bench → display label mapping
# ---------------------------------------------------------------------------

BENCH_DISPLAY: dict[str, str] = {
    "work_bench": "Carpenter",
    "work_desk": "Sage",
    "chemistry_table": "Alchemist",
    "forge": "Armorer / Weaponsmith",
    "woodworking_table": "Woodworker",
    "sewing_table": "Tailor",
    "stove and keg": "Provisioner",
}

_LABEL_TO_BENCH: dict[str, str] = {v.lower(): k for k, v in BENCH_DISPLAY.items()}

# ---------------------------------------------------------------------------
# Adventure classes
# ---------------------------------------------------------------------------

# Sourced from census.classes_db.CLASS_SEED — single source of truth.
# BE-230: prevents the list drifting out of sync with the canonical class data.
_ADVENTURE_CLASSES = iter_adventure_class_names()

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class IngredientResponse(BaseModel):
    description: str
    quantity: int


class RecipeResult(BaseModel):
    id: int
    name: str
    bench: str | None = None
    bench_label: str | None = None
    craft_tier: str | None = None  # T1 … T14 derived from fuel prefix
    crafted_tier: str | None = None  # spell-scroll quality tier (Expert, etc.)
    primary_comp: str | None = None
    primary_qty: int | None = None
    secondary_comps: list[IngredientResponse] = []
    fuel_comp: str | None = None
    fuel_qty: int | None = None
    out_formed_id: int | None = None
    out_formed_count: int | None = None
    class_label: str | None = None
    craft_classes: list[str] = []  # tradeskill classes that can make this recipe


class RecipeSearchResponse(BaseModel):
    results: list[RecipeResult]
    total: int
    page: int
    per_page: int


class RecipeFiltersResponse(BaseModel):
    craft_tiers: list[str]
    benches: list[dict]
    adventure_classes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fuel_to_craft_tier(fuel: str | None) -> str | None:
    if not fuel:
        return None
    first_word = fuel.split()[0].lower()
    return _FUEL_PREFIX_TO_TIER.get(first_word)


def _bench_label(bench: str | None) -> str | None:
    if bench is None:
        return None
    return BENCH_DISPLAY.get(bench, bench.replace("_", " ").title())


def _resolve_bench_param(bench: str | None) -> str | None:
    if bench is None:
        return None
    if bench in BENCH_DISPLAY:
        return bench
    return _LABEL_TO_BENCH.get(bench.lower(), bench)


def _row_to_result(
    row: sqlite3.Row,
    class_label: str | None = None,
    craft_classes: list[str] | None = None,
) -> RecipeResult:
    try:
        sec = json.loads(row["secondary_comps"] or "[]")
    except Exception as exc:
        _log.warning("[recipes] Failed to parse secondary_comps for recipe id=%s: %s", row["id"], exc)
        sec = []

    # Prefer explicitly passed class_label; fall back to a column if present
    cl = class_label
    if cl is None:
        try:
            cl = row["class_label"]
        except IndexError:
            pass

    return RecipeResult(
        id=row["id"],
        name=row["name"],
        bench=row["bench"],
        bench_label=_bench_label(row["bench"]),
        craft_tier=_fuel_to_craft_tier(row["fuel_comp"]),
        crafted_tier=row["crafted_tier"],
        primary_comp=row["primary_comp"],
        primary_qty=row["primary_qty"],
        secondary_comps=[
            IngredientResponse(
                description=c.get("description", ""),
                quantity=c.get("quantity") or 1,
            )
            for c in sec
            if c.get("description")
        ],
        fuel_comp=row["fuel_comp"],
        fuel_qty=row["fuel_qty"],
        out_formed_id=row["out_formed_id"],
        out_formed_count=row["out_formed_count"],
        class_label=cl,
        craft_classes=craft_classes or [],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/recipes/filters", response_model=RecipeFiltersResponse)
async def get_recipe_filters() -> RecipeFiltersResponse:
    return RecipeFiltersResponse(
        craft_tiers=CRAFT_TIERS,
        benches=[{"key": k, "label": v} for k, v in BENCH_DISPLAY.items()],
        adventure_classes=_ADVENTURE_CLASSES,
    )


def _query_items_db(
    class_name: str | None,
    elaborate_ids: list[int] | None,
) -> tuple[list[int] | None, dict[int, str]]:
    """Synchronous items-DB lookup — called via run_in_executor.

    Returns:
        class_item_ids  – list of item IDs whose class_label matches class_name,
                          or None if class_name is not set.
        label_map       – {item_id: class_label} for the given elaborate_ids
                          (used to enrich result rows), empty if elaborate_ids
                          is None or items DB is absent.
    """
    if not ITEMS_DB_PATH.exists():
        return None, {}

    conn = sqlite3.connect(str(ITEMS_DB_PATH))
    conn.execute("PRAGMA query_only = ON")  # safety: never write
    try:
        class_item_ids: list[int] | None = None
        if class_name:
            rows = conn.execute(
                "SELECT id FROM items WHERE LOWER(class_label) LIKE ?",
                (f"%{class_name.lower()}%",),
            ).fetchall()
            class_item_ids = [r[0] for r in rows]

        label_map: dict[int, str] = {}
        if elaborate_ids:
            ph = ",".join("?" * len(elaborate_ids))
            lrows = conn.execute(
                f"SELECT id, class_label FROM items WHERE id IN ({ph})",
                elaborate_ids,
            ).fetchall()
            label_map = {r[0]: r[1] for r in lrows if r[1]}

        return class_item_ids, label_map
    finally:
        conn.close()


@router.get("/recipes/search", response_model=RecipeSearchResponse)
async def search_recipes(
    q: str | None = None,
    tier: str | None = None,  # T1 … T14
    bench: str | None = None,
    class_name: str | None = None,
    craft_class: str | None = None,  # tradeskill class, e.g. "Armorer" (via recipe_classes)
    page: int = 1,
) -> RecipeSearchResponse:
    per_page = 25

    if not RECIPES_DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Recipe database not available")

    items_db_available = ITEMS_DB_PATH.exists()

    if class_name and not items_db_available:
        raise HTTPException(
            status_code=503,
            detail="Items database not available for class filtering",
        )

    bench_key = _resolve_bench_param(bench)

    # ── Resolve class filter → item IDs (sync, off event loop) ────────────────
    # Done BEFORE building SQL so the result becomes a plain IN list.
    # No ATTACH — that causes SQLite file-lock contention with other routes
    # hitting the items DB concurrently under uvicorn.
    class_item_ids: list[int] | None = None
    if class_name and items_db_available:
        class_item_ids, _ = await run_sync(_query_items_db, class_name, None)
        if not class_item_ids:
            return RecipeSearchResponse(results=[], total=0, page=1, per_page=per_page)

    # ── Build WHERE clause ─────────────────────────────────────────────────────
    conditions: list[str] = []
    params: list = []

    if q:
        conditions.append("name_lower LIKE ?")
        params.append(f"%{q.lower()}%")

    if tier and tier.upper() in TIER_FUEL:
        fuel_prefix = TIER_FUEL[tier.upper()]
        conditions.append("fuel_comp LIKE ?")
        params.append(f"{fuel_prefix} %")

    if bench_key:
        conditions.append("bench = ?")
        params.append(bench_key)

    # Tradeskill-class filter via the recipe_classes mapping (same DB → subquery,
    # no ATTACH). A recipe taught by multiple classes' books matches each of them.
    if craft_class:
        conditions.append("id IN (SELECT recipe_id FROM recipe_classes WHERE class = ?)")
        params.append(craft_class)

    if class_item_ids is not None:
        # Use out_elaborate_id — that's the named-quality scroll output.
        # out_formed_id is the rare "perfect craft" bonus and often points
        # to a different item (fuel component, etc.).
        # Split into chunks of 900 to stay well under SQLite's variable limit.
        chunks = [class_item_ids[i : i + 900] for i in range(0, len(class_item_ids), 900)]
        chunk_clauses = []
        for chunk in chunks:
            ph = ",".join("?" * len(chunk))
            chunk_clauses.append(f"out_elaborate_id IN ({ph})")
            params.extend(chunk)
        conditions.append("(" + " OR ".join(chunk_clauses) + ")")

    # Require at least one filter
    if not conditions:
        return RecipeSearchResponse(results=[], total=0, page=1, per_page=per_page)

    where = " AND ".join(conditions)
    offset = (page - 1) * per_page

    # ── Recipes query (aiosqlite, no ATTACH) ───────────────────────────────────
    async with aiosqlite.connect(RECIPES_DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        count_sql = f"SELECT COUNT(DISTINCT id) FROM recipes WHERE {where}"
        async with db.execute(count_sql, params) as cur:
            count_row = await cur.fetchone()
            total = count_row[0] if count_row else 0

        select_sql = (
            f"SELECT id, name, bench, crafted_tier, "
            f"primary_comp, primary_qty, secondary_comps, "
            f"fuel_comp, fuel_qty, "
            f"out_formed_id, out_formed_count, out_elaborate_id "
            f"FROM recipes "
            f"WHERE {where} "
            f"GROUP BY id "
            f"ORDER BY name_lower ASC "
            f"LIMIT {per_page} OFFSET {offset}"
        )
        async with db.execute(select_sql, params) as cur:
            rows = await cur.fetchall()

        # Tradeskill class(es) for each result recipe — the accurate label
        # (the `bench` column is shared across classes, so it can't be used).
        class_by_recipe: dict[int, list[str]] = {}
        row_ids = [r["id"] for r in rows]
        if row_ids:
            ph = ",".join("?" * len(row_ids))
            async with db.execute(
                f"SELECT recipe_id, class FROM recipe_classes WHERE recipe_id IN ({ph}) ORDER BY class",
                row_ids,
            ) as cur:
                async for rid, cls in cur:
                    class_by_recipe.setdefault(rid, []).append(cls)

    # ── Enrich with class_label from items DB (sync, off event loop) ──────────
    elaborate_ids = [r["out_elaborate_id"] for r in rows if r["out_elaborate_id"]]
    label_map: dict[int, str] = {}
    if elaborate_ids and items_db_available:
        _, label_map = await run_sync(_query_items_db, None, elaborate_ids)

    results = [
        _row_to_result(
            r,
            class_label=label_map.get(r["out_elaborate_id"]),
            craft_classes=class_by_recipe.get(r["id"], []),
        )
        for r in rows
    ]

    return RecipeSearchResponse(
        results=results,
        total=total,
        page=page,
        per_page=per_page,
    )
