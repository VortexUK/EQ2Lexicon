"""
GET /api/classes — the static class catalogue (archetype, subclass, role,
colour, display order, icon URL). Public (non-sensitive reference data used by
pre-login pages). Served from classes.db with an in-code CLASS_SEED fallback so
it works before the DB is built/copied to a fresh environment. Cached in-memory
(the data never changes at runtime).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.eq2db import classes as classes_db
from backend.eq2db.classes import CLASS_SEED
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
    rows = classes_db.list_all()
    if rows:
        return rows
    # DB not built/copied yet — fall back to the in-code seed.
    return [
        {
            "name": c.name,
            "archetype": c.archetype,
            "subclass": c.subclass,
            "role": c.role,
            "colour": c.colour,
            "display_order": i,
            "icon_id": c.icon_id,
        }
        for i, c in enumerate(CLASS_SEED)
    ]


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
