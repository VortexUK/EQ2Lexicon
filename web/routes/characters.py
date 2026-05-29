from __future__ import annotations

import aiosqlite
from fastapi import APIRouter, Request
from pydantic import BaseModel

from census.client import CensusClient
from web.cache import character_cache
from web.config import SERVICE_ID as _SERVICE_ID
from web.db import DB_PATH
from web.limiter import limiter
from web.server_context import current_world

router = APIRouter(tags=["characters"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CharNameResult(BaseModel):
    name: str
    cls: str | None = None
    level: int | None = None
    guild_name: str | None = None


class CharSearchResponse(BaseModel):
    results: list[CharNameResult]
    total: int
    source: str = "census"  # "census" | "local"


# ---------------------------------------------------------------------------
# Local fallback — claimed characters whose name starts with the query
# ---------------------------------------------------------------------------


async def _local_search(q: str) -> list[CharNameResult]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT DISTINCT character_name
            FROM character_claims
            WHERE LOWER(character_name) LIKE ?
              AND status IN ('approved', 'pending')
            ORDER BY character_name
            LIMIT 50
            """,
            (f"{q.lower()}%",),
        ) as cur:
            rows = await cur.fetchall()
    return [CharNameResult(name=r["character_name"]) for r in rows]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/characters/search", response_model=CharSearchResponse)
@limiter.limit("20/minute")
async def search_characters(request: Request, name: str = "") -> CharSearchResponse:
    """
    Search characters by name prefix.
    Queries the Census API for all characters on the configured world whose
    name starts with *name*.  Falls back to locally-registered claimed
    characters if Census is unavailable.
    Requires at least 2 characters.
    """
    q = name.strip()
    if len(q) < 2:
        return CharSearchResponse(results=[], total=0)
    if len(q) > 64:
        return CharSearchResponse(results=[], total=0)

    # CENSUS-CLIENT-LIFECYCLE: migrate to web.lib.census_lifecycle.shared_census_client (Phase 2c.2)
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        raw = await client.search_characters_by_name(q, current_world())

        if not raw:
            # Prefix search missed — try exact-name lookup (handles cases like "Exobroker"
            # where the prefix index doesn't return results for a complete name)
            try:
                brief = await client.get_character_brief(q, current_world())
                if brief:
                    raw = [brief]
            except Exception:
                pass
    except Exception:
        raw = []
    finally:
        await client.close()

    if raw:
        results = [CharNameResult(**r) for r in raw]
        return CharSearchResponse(results=results, total=len(results), source="census")

    # Census returned nothing or failed — fall back to local claims
    results = await _local_search(q)
    return CharSearchResponse(results=results, total=len(results), source="local")


# ---------------------------------------------------------------------------
# Bulk lookup — cache-only (no Census fallback)
# ---------------------------------------------------------------------------


class BulkLookupEntry(BaseModel):
    found: bool
    guild_name: str | None = None
    cls: str | None = None
    level: int | None = None


class BulkLookupResponse(BaseModel):
    results: dict[str, BulkLookupEntry]


@router.get("/characters/lookup", response_model=BulkLookupResponse)
@limiter.limit("60/minute")
async def lookup_characters(request: Request, names: str = "") -> BulkLookupResponse:
    """
    Bulk character lookup that reads ONLY from the in-memory character_cache.

    Designed for views like /parse/:id that need to know guild affiliation
    for many characters at once without burning 24+ Census API calls per
    page load. Cache misses simply return `found=False`; as users browse
    individual character pages the cache warms up and subsequent parse
    views become richer.

    Query: comma-separated `names`. Max 50 names per call.
    """
    raw = [n.strip() for n in names.split(",") if n.strip()]
    # Dedupe while preserving order so the response dict is stable.
    seen: set[str] = set()
    unique: list[str] = []
    for n in raw:
        lower = n.lower()
        if lower in seen:
            continue
        seen.add(lower)
        unique.append(n)
    unique = unique[:50]

    out: dict[str, BulkLookupEntry] = {}
    for name in unique:
        cache_key = f"{name.lower()}:{current_world().lower()}"
        cached = character_cache.get_stale(cache_key)[0]
        if cached is None:
            out[name] = BulkLookupEntry(found=False)
            continue
        # `cached` is a CharacterResponse — pull only the fields we surface
        out[name] = BulkLookupEntry(
            found=True,
            guild_name=getattr(cached, "guild_name", None),
            cls=getattr(cached, "cls", None),
            level=getattr(cached, "level", None),
        )
    return BulkLookupResponse(results=out)
