"""
GET /api/classes — the static class catalogue (archetype, subclass, role,
colour, display order, icon URL). Public (non-sensitive reference data used by
pre-login pages). Served from classes.db (committed at data/classes/classes.db).
Cached in-memory (the data never changes at runtime).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.eq2db.classes import catalogue as classes_db
from backend.server.core.executor import run_sync

router = APIRouter(tags=["classes"])


class ClassResponse(BaseModel):
    name: str
    archetype: str
    subclass: str | None
    role: str
    colour: str
    display_order: int
    icon_url: str


_cache: list[ClassResponse] | None = None


def _rows() -> list[dict]:
    # Adventure classes only — crafter rows exist in classes.db for the
    # item-restriction lookup but aren't characters you create as in the
    # claim/character-picker UIs this endpoint feeds.
    return [r for r in classes_db.list_all() if r["archetype"] != "Crafter"]


@router.get("/classes", response_model=list[ClassResponse])
async def list_classes() -> list[ClassResponse]:
    global _cache
    if _cache is None:
        rows = await run_sync(_rows)
        _cache = [
            ClassResponse(
                name=r["name"],
                archetype=r["archetype"],
                subclass=r["subclass"],
                role=r["role"],
                colour=r["colour"],
                display_order=r["display_order"],
                icon_url=f"/class-icons/{r['icon_id']}.png",
            )
            for r in rows
        ]
    return _cache
