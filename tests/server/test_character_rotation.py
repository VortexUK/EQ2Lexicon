"""Tests for GET /api/character/{name}/rotation-data — simulator payload."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.eq2db.spells import Blocklist
from backend.server.api.character import CharacterResponse

_EMPTY_BLOCKLIST = Blocklist(frozenset(), [])


def _fake_char(name: str = "Sihtric", spell_ids: list[int] | None = None) -> CharacterResponse:
    return CharacterResponse(id="123", name=name, level=80, cls="Wizard", world="Varsoon", spell_ids=spell_ids or [])


def _row(
    spell_id: int,
    name: str,
    *,
    tier_name: str = "Master",
    spell_type: str = "spells",
    level: int = 70,
    given_by: str = "spellscroll",
    beneficial: int = 0,
    cast: float = 2.0,
    recast: float = 8.0,
    recovery: float = 5.0,  # raw spells.db value (10x inflated)
    effects: list[dict] | None = None,
    crc: int | None = None,
) -> dict:
    return {
        "id": spell_id,
        "name": name,
        "tier": 6,
        "tier_name": tier_name,
        "type": spell_type,
        "level": level,
        "given_by": given_by,
        "beneficial": beneficial,
        "crc": crc if crc is not None else spell_id,
        "cast_secs": cast,
        "recast_secs": recast,
        "recovery_secs": recovery,
        "target_type": "other",
        "icon_id": 252,
        "icon_backdrop": 315,
        "effects": json.dumps(effects or []),
        "passes_spellcheck": 1,
    }


_DMG_EFFECTS = [
    {"description": "Inflicts 335 - 623 heat damage on target", "indentation": 0},
    {"description": "Inflicts 201 heat damage on target every 2 seconds", "indentation": 0},
]


def _catalogue_patches(rows_by_id: dict[int, dict], meta: dict[str, dict] | None = None):
    from backend.server.api.character import rotation as mod

    return (
        patch.object(mod._spells, "find_by_ids", lambda ids: {i: rows_by_id[i] for i in ids if i in rows_by_id}),
        patch.object(mod._spells, "load_blocklist", lambda *a, **k: _EMPTY_BLOCKLIST),
        patch.object(mod._items, "spell_meta_by_names", lambda names: meta or {}),
    )


async def _get(app, char: CharacterResponse, name: str = "Sihtric"):
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.rotation.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/character/{name}/rotation-data")


@pytest.mark.asyncio
async def test_rotation_data_shape_and_recovery_normalised(app):
    rows = {1: _row(1, "Dark Pyre VI", effects=_DMG_EFFECTS)}
    meta = {"Dark Pyre VI (Master)": {"spell_duration": 1000.0, "spell_power_cost": 211}}
    p1, p2, p3 = _catalogue_patches(rows, meta)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["character_name"] == "Sihtric" and body["cls"] == "Wizard"
    (a,) = body["abilities"]
    assert a["name"] == "Dark Pyre VI" and a["base_name"] == "Dark Pyre"
    assert a["cast_secs"] == 2.0 and a["recast_secs"] == 8.0
    assert a["recovery_secs"] == 0.5  # 5.0 in the DB, normalised /10
    assert a["duration_s"] == 10.0  # 1000 hundredths
    assert a["power_cost"] == 211
    kinds = [(c["kind"], c["duration_s"], c["duration_estimated"]) for c in a["components"]]
    # DoT inherits the spell duration from the items join — not estimated.
    assert kinds == [("hit", None, False), ("dot", 10.0, False)]
    assert all(c["target_scope"] == "single" and c["condition"] is None for c in a["components"])
    assert a["has_unparsed_damage"] is False


@pytest.mark.asyncio
async def test_rotation_excludes_aa_and_includes_single_tier(app):
    rows = {
        1: _row(1, "Fiery Blast", given_by="alternateadvancement", effects=_DMG_EFFECTS),
        2: _row(2, "Lone Utility", given_by="classtraining", effects=[]),  # single-tier, no damage
    }
    p1, p2, p3 = _catalogue_patches(rows)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1, 2]))
    names = [a["name"] for a in r.json()["abilities"]]
    assert names == ["Lone Utility"]  # AA excluded; single-tier hostile kept


@pytest.mark.asyncio
async def test_rotation_beneficial_gate(app):
    rows = {
        1: _row(1, "Permanent Buff", beneficial=1, effects=[]),  # no duration → dropped
        2: _row(2, "Temp Burst", beneficial=1, effects=[]),  # 30s duration → kept
        3: _row(3, "Hour Buff", beneficial=1, effects=[]),  # 3600s → dropped (permanent-shaped)
    }
    meta = {
        "Temp Burst (Master)": {"spell_duration": 3000.0, "spell_power_cost": None},  # 30s
        "Hour Buff (Master)": {"spell_duration": 360000.0, "spell_power_cost": None},  # 3600s
    }
    p1, p2, p3 = _catalogue_patches(rows, meta)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1, 2, 3]))
    names = [a["name"] for a in r.json()["abilities"]]
    assert names == ["Temp Burst"]


@pytest.mark.asyncio
async def test_rotation_unparsed_damage_flagged_and_dot_estimated(app):
    effects = [
        {"description": "Inflicts 100.0% of max health in crushing damage on target.", "indentation": 0},
        {"description": "Inflicts 50 magic damage on target every 3 seconds", "indentation": 0},
    ]
    rows = {1: _row(1, "Weird Nuke", effects=effects)}
    p1, p2, p3 = _catalogue_patches(rows)  # no items meta → no duration
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1]))
    (a,) = r.json()["abilities"]
    assert a["has_unparsed_damage"] is True
    (dot,) = a["components"]
    assert dot["kind"] == "dot" and dot["duration_s"] is None and dot["duration_estimated"] is True


@pytest.mark.asyncio
async def test_rotation_prefers_scaled_item_effect_text(app):
    """The spellscroll's effect_list (items.db) carries the properly
    scaled damage numbers; the spells.db spell-record text is unscaled
    for some spells (Smite Corruption '1 - 2'). When the items join has
    effects, they win — including base-indentation normalisation (scroll
    lines sit at indentation 1) and relative conditional attachment."""
    stale = [{"description": "Inflicts 1 - 2 divine damage on target instantly and every 4 seconds.", "indentation": 0}]
    rows = {1: _row(1, "Smite Corruption III", level=71, effects=stale)}
    meta = {
        "Smite Corruption III (Master)": {
            "spell_duration": 2400.0,
            "spell_power_cost": 100,
            "effects": [
                {
                    "description": "Inflicts 132 - 161 divine damage on target instantly and every 4 seconds.",
                    "indentation": 1,
                },
                {"description": "If target is undead", "indentation": 2},
                {"description": "Decreases WIS of target by 89.8.", "indentation": 1},
            ],
        }
    }
    p1, p2, p3 = _catalogue_patches(rows, meta)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1]))
    (a,) = r.json()["abilities"]
    hit, dot = a["components"]
    assert (hit["min_dmg"], hit["max_dmg"]) == (132.0, 161.0)
    assert dot["kind"] == "dot" and dot["interval_s"] == 4.0
    # Deeper "If" line one level below the (shifted) damage line attaches.
    assert hit["condition"] == "If target is undead"
    # Scaled numbers are no longer suspect.
    assert not hit["suspect_low_value"] and not dot["suspect_low_value"]
    # effect_lines mirror the item text the live tooltip shows.
    assert a["effect_lines"][0].startswith("Inflicts 132 - 161")


@pytest.mark.asyncio
async def test_rotation_suspect_low_damage_flagged(app):
    """Census unscaled-tooltip text ('Inflicts 1 - 2 divine damage' on a
    lv71 spell, e.g. Smite Corruption III) → component carries the number
    but is flagged suspect; sane values at the same level are not."""
    effects = [
        {"description": "Inflicts 1 - 2 divine damage on target instantly and every 4 seconds.", "indentation": 0}
    ]
    rows = {
        1: _row(1, "Smite Corruption III", level=71, effects=effects),
        2: _row(
            2,
            "Warring Deities V",
            level=77,
            effects=[
                {"description": "Inflicts 428 - 523 divine damage on target", "indentation": 0},
            ],
        ),
    }
    p1, p2, p3 = _catalogue_patches(rows)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1, 2]))
    by_name = {a["name"]: a for a in r.json()["abilities"]}
    smite = by_name["Smite Corruption III"]
    assert all(c["suspect_low_value"] for c in smite["components"])
    assert smite["components"][0]["max_dmg"] == 2.0  # number carried as-is
    warring = by_name["Warring Deities V"]
    assert all(not c["suspect_low_value"] for c in warring["components"])


@pytest.mark.asyncio
async def test_rotation_404_unknown_character(app):
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    mock_census = AsyncMock()
    mock_census.get_character = AsyncMock(return_value=None)
    with (
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch("backend.server.api.character.rotation.character_cache") as mock_cache,
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_census),
    ):
        mock_cache.get_stale.return_value = (None, False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Nobody/rotation-data")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_rotation_503_when_spells_db_missing(app):
    mock_db = MagicMock()
    mock_db.exists.return_value = False
    with patch("backend.server.api.character.rotation._SPELLS_DB", mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/rotation-data")
    assert r.status_code == 503
