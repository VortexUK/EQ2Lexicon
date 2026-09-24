"""GET /character/{name}/rotation-data — the rotation simulator's payload.

Everything the frontend engine needs per owned ability: cast/recast/
recovery timing (spells.db), parsed damage components (effect-text parser
in backend.eq2db.spell_effects), spell duration + power cost + scaled
effect text (items.db spellscroll join — preferred over the spells.db
effect text, which is unscaled for some spells). Mirrors
character/spells.py plumbing: cache-first character resolution, census
fallback, sync DB reads via run_sync.

Recovery normalisation: spells.db ``recovery_secs`` is 10× inflated
(census units bug — see spell_effects.RECOVERY_DIVISOR); divided here so
the wire value is real seconds (0.5 for virtually every combat ability).
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.eq2db.items import catalogue as _items
from backend.eq2db.spell_effects import (
    RECOVERY_DIVISOR,
    SPELL_DURATION_DIVISOR,
    TEMP_BUFF_MAX_DURATION_S,
    is_suspect_low_damage,
    parse_effect_lines,
)
from backend.eq2db.spells import DB_PATH as _SPELLS_DB
from backend.eq2db.spells import catalogue as _spells
from backend.server.api.character import router
from backend.server.api.character.views import _build_char_response
from backend.server.cache import character_cache
from backend.server.core.cache_keys import char_cache_key
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.executor import run_sync
from backend.server.limiter import limiter
from backend.server.server_context import current_world


class DamageComponentResponse(BaseModel):
    kind: str  # 'hit' | 'dot'
    min_dmg: float
    max_dmg: float
    school: str
    target_scope: str = "single"  # 'single' | 'encounter' | 'aoe'
    condition: str | None = None  # e.g. "If target is undead"
    interval_s: float | None = None
    duration_s: float | None = None
    duration_estimated: bool = False
    # Census unscaled-tooltip disease: the damage text reads "1 - 2" on a
    # lv71 spell. The number is carried as-is but flagged so the UI can
    # badge it; calibration is the real fix.
    suspect_low_value: bool = False


class RotationAbilityResponse(BaseModel):
    name: str
    base_name: str
    crc: int | None = None
    tier_name: str
    level: int
    spell_type: str  # 'spells' | 'arts'
    beneficial: bool
    cast_secs: float
    recast_secs: float
    recovery_secs: float  # normalised to real seconds
    target_type: str | None = None
    icon_id: int | None = None
    icon_backdrop: int | None = None
    duration_s: float | None = None  # items.db spell duration, seconds
    power_cost: int | None = None  # carried for later; v1 engine ignores it
    components: list[DamageComponentResponse] = []
    effect_lines: list[str] = []
    has_unparsed_damage: bool = False


class CharacterRotationDataResponse(BaseModel):
    character_name: str
    cls: str | None = None
    level: int | None = None
    abilities: list[RotationAbilityResponse]


def _build_abilities_sync(spell_ids: list[int]) -> list[RotationAbilityResponse]:
    """SYNC (executor): resolve the rotation universe, parse effects and
    join the items.db spell meta — the two catalogue reads batched into
    one thread hop."""
    import json  # noqa: PLC0415

    rows = _spells.character_rotation_spells(spell_ids)
    rows.sort(key=lambda r: r.get("level") or 0)

    meta = _items.spell_meta_by_names([f"{r.get('name')} ({r.get('tier_name')})" for r in rows])

    out: list[RotationAbilityResponse] = []
    for r in rows:
        m = meta.get(f"{r.get('name')} ({r.get('tier_name')})") or {}

        # Effect text: prefer the spellscroll's (items.db) — it carries the
        # properly SCALED numbers the live tooltip shows; the spells.db
        # spell-record text is unscaled for some spells (Smite Corruption
        # reads "1 - 2" where the scroll says "132 - 161"). Scroll lines are
        # uniformly base-indented (typically 1) — shift to base 0 so the
        # parser's top-level/conditional semantics hold.
        item_effects = m.get("effects") or []
        if item_effects:
            depths = [
                (e or {}).get("indentation") or 0 for e in item_effects if ((e or {}).get("description") or "").strip()
            ]
            shift = min(depths) if depths else 0
            effects = [{**e, "indentation": ((e or {}).get("indentation") or 0) - shift} for e in item_effects]
        else:
            try:
                effects = json.loads(r.get("effects") or "[]")
            except (TypeError, ValueError):
                effects = []
        parsed = parse_effect_lines(effects)

        raw_duration = m.get("spell_duration")
        duration_s = (raw_duration / SPELL_DURATION_DIVISOR) if raw_duration else None
        beneficial = bool(r.get("beneficial"))

        # Permanent buffs are already baked into the character sheet stats;
        # only temp-buff-shaped beneficials belong in a rotation.
        if beneficial and not (duration_s and 0 < duration_s <= TEMP_BUFF_MAX_DURATION_S):
            continue

        # DoT components inherit the spell's duration; unknown durations
        # stay None and the engine applies its flagged fallback.
        components = []
        level = r.get("level") or 0
        for c in parsed["components"]:
            comp = dict(c)
            if comp.get("kind") == "dot" and comp.get("duration_s") is None:
                dot_dur = duration_s or parsed["lasts_for_s"]
                comp["duration_s"] = dot_dur
                comp["duration_estimated"] = dot_dur is None
            comp["suspect_low_value"] = is_suspect_low_damage(c.get("max_dmg") or 0.0, level)
            components.append(DamageComponentResponse.model_validate(comp))

        out.append(
            RotationAbilityResponse(
                name=r.get("name") or "",
                base_name=_spells.strip_roman(r.get("name") or ""),
                crc=r.get("crc"),
                tier_name=r.get("tier_name") or "Unknown",
                level=r.get("level") or 0,
                spell_type=r.get("type") or "",
                beneficial=beneficial,
                cast_secs=float(r.get("cast_secs") or 0.0),
                recast_secs=float(r.get("recast_secs") or 0.0),
                recovery_secs=float(r.get("recovery_secs") or 0.0) / RECOVERY_DIVISOR,
                target_type=r.get("target_type"),
                icon_id=r.get("icon_id"),
                icon_backdrop=r.get("icon_backdrop"),
                duration_s=duration_s,
                power_cost=m.get("spell_power_cost"),
                components=components,
                effect_lines=parsed["lines"],
                has_unparsed_damage=bool(parsed["unparsed_damage"]),
            )
        )
    return out


@router.get("/character/{name}/rotation-data", response_model=CharacterRotationDataResponse)
@limiter.limit("30/minute")
async def get_character_rotation_data(request: Request, name: str) -> CharacterRotationDataResponse:
    """The rotation simulator's per-character ability payload."""
    if not _SPELLS_DB.exists():
        raise HTTPException(status_code=503, detail="Spells database not available")

    cache_key = char_cache_key(name, current_world())
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        char = cached
    else:
        async with shared_census_client() as client:
            raw = await client.get_character(name, current_world())
        if raw is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {current_world()}")
        char = _build_char_response(raw)
        character_cache.set(cache_key, char)

    abilities = await run_sync(_build_abilities_sync, char.spell_ids or [])
    return CharacterRotationDataResponse(
        character_name=char.name,
        cls=char.cls,
        level=char.level,
        abilities=abilities,
    )
