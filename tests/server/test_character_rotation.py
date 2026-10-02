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


async def _get(app, char: CharacterResponse, name: str = "Sihtric", aa_trees: list | None = None):
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.views.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch("backend.server.api.character.rotation._fetch_aa_trees", AsyncMock(return_value=aa_trees or [])),
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
async def test_proc_carrying_permanent_beneficial_becomes_passive(app):
    """The Mythical's Divine Light: a permanent beneficial with no damage
    components but a when_damaged proc — not castable, not dropped: it
    joins the passives as a proc stream."""
    rows = {
        1: _row(
            1,
            "Divine Light",
            beneficial=1,
            effects=[
                {
                    "description": "When damaged this spell will cast Shock of Light on target's attacker.",
                    "indentation": 0,
                },
                {"description": "Inflicts 1,866 - 2,281 divine damage on target.", "indentation": 1},
                {"description": "Reduces all damage done to the target by 8%.", "indentation": 0},
            ],
        )
    }
    p1, p2, p3 = _catalogue_patches(rows)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1]))
    body = r.json()
    assert body["abilities"] == []
    (p,) = body["passives"]
    assert p["name"] == "Divine Light" and p["source"] == "spell"
    (proc,) = p["procs"]
    assert proc["trigger"] == "when_damaged" and proc["name"] == "Shock of Light"
    assert proc["components"][0]["max_dmg"] == 2281.0


@pytest.mark.asyncio
async def test_partial_unscaled_component_flagged_relative(app):
    """Glacial Strike signature: a genuine hit line (61-67) next to an
    unscaled dot (10-11) that evades the level heuristic — the RELATIVE
    rule flags the tiny sibling, never the healthy one."""
    rows = {
        1: _row(
            1,
            "Frost Jab",
            effects=[
                {"description": "Inflicts 61 - 67 melee damage on target", "indentation": 0},
                {"description": "Inflicts 10 - 11 cold damage on target every 4 seconds.", "indentation": 0},
            ],
        )
    }
    p1, p2, p3 = _catalogue_patches(rows)
    with p1, p2, p3:
        r = await _get(app, _fake_char(spell_ids=[1]))
    (a,) = r.json()["abilities"]
    flags = {c["kind"]: c["suspect_low_value"] for c in a["components"]}
    assert flags == {"hit": False, "dot": True}


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


_BOLT_ROW = {
    "id": 251275318,
    "name": "Bolt of Power",
    "tier": 10,
    "tier_name": "Grandmaster",
    "type": "pcinnates",
    "level": 70,
    "given_by": "alternateadvancement",
    "beneficial": 1,
    "crc": 659315350,
    "cast_secs": 0.0,
    "recast_secs": 0.0,
    "recovery_secs": 0.0,
    "target_type": None,
    "icon_id": 111,
    "icon_backdrop": None,
    "effects": json.dumps(
        [
            {
                "description": "On any combat or spell hit this spell has a 50% chance to cast Bolt of Power on target of attack.",
                "indentation": 0,
            },
            {"description": "Interrupts target", "indentation": 1},
            {"description": "Inflicts 89 - 149 divine damage on target.", "indentation": 1},
        ]
    ),
    "passes_spellcheck": 1,
}

_SMITE_AA_ROW = {
    **_BOLT_ROW,
    "id": 999,
    "name": "Battle Leadership Strike",
    "type": "arts",
    "beneficial": 0,
    "crc": 777,
    "cast_secs": 1.0,
    "recast_secs": 20.0,
    "recovery_secs": 5.0,
    "effects": json.dumps([{"description": "Inflicts 400 - 600 crushing damage on target", "indentation": 0}]),
}


@pytest.mark.asyncio
async def test_rotation_aa_passives_and_castables(app):
    """Spent AA nodes resolve via aas.db spellcrc → spells.db at the spent
    rank: proc innates (Bolt of Power) surface as passives with parsed
    proc streams; hostile castable AAs join the ability palette."""
    from backend.server.api.character import rotation as mod

    tree = {
        "name": "Templar",
        "tree_type": "subclass",
        "nodes": [
            {"node_id": 100, "name": "Bolt of Power", "spellcrc": 659315350, "icon_id": 111},
            {"node_id": 101, "name": "Battle Leadership", "spellcrc": 777, "icon_id": 112},
            {"node_id": 102, "name": "Stat Only", "spellcrc": 4294967295, "icon_id": 113},
        ],
    }
    rows_by_crc = {(659315350, 10): _BOLT_ROW, (777, 3): _SMITE_AA_ROW}
    p1, p2, p3 = _catalogue_patches({})
    with (
        p1,
        p2,
        p3,
        patch.object(mod._aas, "get_tree", lambda tid: tree if tid == 49 else None),
        patch.object(mod._spells, "find_by_crc", lambda crc, tier=None: rows_by_crc.get((crc, tier))),
    ):
        r = await _get(app, _fake_char(spell_ids=[]), aa_trees=[(49, {"100": 10, "101": 3, "102": 5})])
    body = r.json()
    (passive,) = body["passives"]
    assert passive["name"] == "Bolt of Power" and passive["source"] == "aa" and passive["rank"] == 10
    (proc,) = passive["procs"]
    assert proc["trigger"] == "any_hit" and proc["chance_pct"] == 50.0
    assert proc["components"][0]["max_dmg"] == 149.0
    (castable,) = body["abilities"]
    assert castable["name"] == "Battle Leadership Strike" and castable["source"] == "aa" and castable["rank"] == 3
    assert castable["recovery_secs"] == 0.5  # same 10x normalisation


@pytest.mark.asyncio
async def test_rotation_aa_band_interpolation(app):
    """AA spell rows exist at level bands (70/100/110…); a level-80
    character's values are the linear interpolation 1/3 of the way from
    the 70 band to the 100 band (validated in-game: Bolt of Power)."""
    from backend.server.api.character import rotation as mod

    def band_row(level: int, lo: int, hi: int) -> dict:
        row = _row(
            900,
            "Exorcise",
            spell_type="spells",
            level=level,
            crc=888,
            effects=[{"description": f"Inflicts {lo} - {hi} divine damage on target", "indentation": 0}],
        )
        row["tier"] = 1
        return row

    bands = [band_row(70, 300, 600), band_row(100, 600, 900)]
    tree = {"name": "T", "tree_type": "subclass", "nodes": [{"node_id": 100, "name": "Exorcise", "spellcrc": 888}]}
    p1, p2, p3 = _catalogue_patches({})
    with (
        p1,
        p2,
        p3,
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_crc_bands", lambda crc, tier: bands if crc == 888 else []),
    ):
        r = await _get(app, _fake_char(spell_ids=[]), aa_trees=[(49, {"100": 1})])
    (a,) = r.json()["abilities"]
    (c,) = a["components"]
    # level 80 → 70 + (100-70)/3: min 300+(600-300)/3, max 600+(900-600)/3
    assert c["min_dmg"] == 400.0 and c["max_dmg"] == 700.0
    assert a["level"] == 70  # ability keeps its low-band spell level


@pytest.mark.asyncio
async def test_rotation_aa_band_interpolation_drifted_hi_text(app):
    """Real Exorcise shape: the lv70 band has the pulse wrapper ('Applies
    X instantly and every 6 seconds.') → hit + dot; the lv100 band lost
    the wrapper ('Applies Exorcise.' with the Inflicts line INDENTED) and
    parses to no components. Interpolation must fall back to the raw
    damage pair in the hi text and blend both lo components against it."""
    from backend.server.api.character import rotation as mod

    lo = _row(901, "Exorcise", level=70, crc=889, beneficial=1)
    lo["tier"] = 1
    lo["target_type"] = "self"
    lo["effects"] = json.dumps(
        [
            {"description": "Applies Exorcise instantly and every 6 seconds.", "indentation": 0},
            {"description": "Inflicts 269 - 448 divine damage on targets in Area of Effect.", "indentation": 1},
        ]
    )
    hi = _row(902, "Exorcise", level=100, crc=889, beneficial=1)
    hi["tier"] = 1
    hi["effects"] = json.dumps(
        [
            {"description": "Applies Exorcise.", "indentation": 0},
            {"description": "Inflicts 416 - 693 divine damage on targets in Area of Effect", "indentation": 1},
        ]
    )
    tree = {"name": "T", "tree_type": "subclass", "nodes": [{"node_id": 100, "name": "Exorcise", "spellcrc": 889}]}
    p1, p2, p3 = _catalogue_patches({})
    with (
        p1,
        p2,
        p3,
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_crc_bands", lambda crc, tier: [lo, hi] if crc == 889 else []),
    ):
        r = await _get(app, _fake_char(spell_ids=[]), aa_trees=[(49, {"100": 1})])
    (a,) = r.json()["abilities"]
    kinds = {c["kind"]: c for c in a["components"]}
    assert set(kinds) == {"hit", "dot"}
    # level 80 → 1/3 toward the hi pair: min 269+(416-269)/3=318, max 448+(693-448)/3≈529.7
    for c in kinds.values():
        assert c["min_dmg"] == pytest.approx(318.0)
        assert c["max_dmg"] == pytest.approx(529.6667, abs=0.01)
    # Self-target pulse with no duration ("Until Cancelled") ⇒ maintained.
    assert a["maintained"] is True
    assert kinds["dot"]["from_pulse"] is True


@pytest.mark.asyncio
async def test_rotation_aa_static_bases_replace_unscaled_census(app):
    """Rabies: census stores the lv-70 row unscaled ('Inflicts 1 disease
    damage...'); the curated static bases replace it, un-flag the suspect
    heuristic, and carry the Rabies II termination package."""
    from backend.server.api.character import rotation as mod

    row = _row(903, "Rabies", spell_type="spells", level=70, crc=890)
    row["tier"] = 1
    row["effects"] = json.dumps(
        [
            {"description": "Applies Rabies II on termination.  Lasts for 16.0 seconds.", "indentation": 0},
            {"description": "Inflicts 131 - 160 disease damage on target every 4 seconds.", "indentation": 1},
            {"description": "Inflicts 1 disease damage on target instantly and every 4 seconds.", "indentation": 0},
        ]
    )
    tree = {"name": "T", "tree_type": "subclass", "nodes": [{"node_id": 100, "name": "Rabies", "spellcrc": 890}]}
    p1, p2, p3 = _catalogue_patches({})
    with (
        p1,
        p2,
        p3,
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_crc_bands", lambda crc, tier: [row] if crc == 890 else []),
    ):
        r = await _get(app, _fake_char(spell_ids=[]), aa_trees=[(49, {"100": 1})])
    (a,) = r.json()["abilities"]
    comps = [(c["kind"], c["min_dmg"], c["max_dmg"]) for c in a["components"]]
    assert comps == [
        ("hit", 72.0, 84.2),
        ("dot", 57.1, 69.3),
        ("dot", 114.6, 139.0),
    ]
    assert all(not c["suspect_low_value"] for c in a["components"])
    assert all(c["duration_s"] == 16.0 and not c["duration_estimated"] for c in a["components"] if c["kind"] == "dot")
    assert a["has_unparsed_damage"] is False
    # The Rabies II termination HIT is a PROC payload (AM/3 — gear-swap
    # validated), modeled as a trigger_count=1 proc on the cast.
    (proc,) = a["procs"]
    assert proc["trigger"] == "termination" and proc["trigger_count"] == 1.0
    assert proc["components"][0]["min_dmg"] == 5.1


@pytest.mark.asyncio
async def test_rotation_lifeburn_static_base_with_per_hp(app):
    """Lifeburn: census bands are era-drifted; the static base carries the
    tooltip-reversed flat hit+dot AND the per-HP components (9/HP, ~25%
    of max health per application) — flat, un-flagged, 10s/1s ticks."""
    from backend.server.api.character import rotation as mod

    row = _row(903, "Lifeburn", spell_type="spells", level=70, crc=890)
    row["tier"] = 1
    row["effects"] = json.dumps(
        [
            {
                "description": "Inflicts 109 - 121 disease damage on target instantly and every second.",
                "indentation": 0,
            },
            {
                "description": "Inflicts an additional 18 points of disease damage to target "
                "for each health point consumed instantly and every second.",
                "indentation": 0,
            },
            {"description": "Must be hated by your current target.", "indentation": 0},
        ]
    )
    tree = {"name": "T", "tree_type": "subclass", "nodes": [{"node_id": 100, "name": "Lifeburn", "spellcrc": 890}]}
    p1, p2, p3 = _catalogue_patches({})
    with (
        p1,
        p2,
        p3,
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_crc_bands", lambda crc, tier: [row] if crc == 890 else []),
    ):
        r = await _get(app, _fake_char(spell_ids=[]), aa_trees=[(49, {"100": 1})])
    (a,) = r.json()["abilities"]
    comps = [(c["kind"], c["min_dmg"], c["max_dmg"], c["per_hp_rate"], c["hp_fraction"]) for c in a["components"]]
    assert comps == [
        ("hit", 29.3, 32.2, None, None),
        ("dot", 29.3, 32.2, None, None),
        ("hit", 0.0, 0.0, 9.0, 0.25),
        ("dot", 0.0, 0.0, 9.0, 0.25),
    ]
    # Zero-damage per-HP components are legitimate — never suspect-flagged.
    assert all(not c["suspect_low_value"] for c in a["components"])
    assert all(
        c["duration_s"] == 10.0 and c["interval_s"] == 1.0 and not c["duration_estimated"]
        for c in a["components"]
        if c["kind"] == "dot"
    )


@pytest.mark.asyncio
async def test_rotation_aa_dedupes_known_crcs(app):
    """An AA node whose spell is already in the owned-spell universe (same
    crc) must not appear twice."""
    from backend.server.api.character import rotation as mod

    rows = {1: _row(1, "Dark Pyre VI", effects=_DMG_EFFECTS, crc=555)}
    tree = {"name": "T", "tree_type": "subclass", "nodes": [{"node_id": 100, "name": "Dark Pyre", "spellcrc": 555}]}
    p1, p2, p3 = _catalogue_patches(rows)
    with (
        p1,
        p2,
        p3,
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_crc", lambda crc, tier=None: rows[1]),
    ):
        r = await _get(app, _fake_char(spell_ids=[1]), aa_trees=[(49, {"100": 5})])
    names = [a["name"] for a in r.json()["abilities"]]
    assert names == ["Dark Pyre VI"]


@pytest.mark.asyncio
async def test_character_buffs_uses_owned_ranks(app):
    """/api/simulator/character-buffs resolves a real character and
    returns their buffs at the OWNED rank/tier: two owned tiers of the
    same buff collapse to the highest tier, and the highest owned rank
    beats a lower rank of the same line."""
    from backend.server.api.character import rotation as mod

    def buff_row(sid: int, name: str, tier: int, tier_name: str, level: int) -> dict:
        r = _row(sid, name, tier_name=tier_name, level=level, beneficial=1)
        r["tier"] = tier
        r["target_type"] = "group"
        r["effects"] = json.dumps([{"description": "Increases Haste of group members (AE) by 41.2.", "indentation": 0}])
        return r

    rows = {
        1: buff_row(1, "Rousing Tune VI", 5, "Adept", 66),
        2: buff_row(2, "Rousing Tune VI", 9, "Master", 66),
        3: buff_row(3, "Rousing Tune V", 9, "Master", 56),
    }
    char = _fake_char(name="Adomia", spell_ids=[1, 2, 3])
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.views.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch.object(mod._spells, "find_by_ids", lambda ids: {i: rows[i] for i in ids if i in rows}),
        patch.object(mod._items, "spell_meta_by_names", lambda names: {}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/simulator/character-buffs?name=Adomia")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["character_name"] == "Adomia" and body["cls"] == "Wizard"
    (b,) = body["buffs"]
    assert b["name"] == "Rousing Tune VI" and b["tier_name"] == "Master"
    assert b["mods"] == {"hastePct": 41.2}


@pytest.mark.asyncio
async def test_character_buffs_applies_member_aa_adjustments(app):
    """A member's Enhance:/Focus: AA nodes adjust their buffs' timing:
    bard Focus shortens Cacophony of Blades' reuse by 30s; Enhance
    extends its duration by 3s."""
    from backend.server.api.character import rotation as mod

    cob = _row(1, "Cacophony of Blades II", tier_name="Master", level=76, beneficial=1, recast=60.0)
    cob["tier"] = 9
    cob["target_type"] = "group"
    cob["effects"] = json.dumps([{"description": "Increases Haste of group members (AE) by 63.6.", "indentation": 0}])
    meta = {"Cacophony of Blades II (Master)": {"spell_duration": 1200.0, "spell_power_cost": None}}

    focus_row = {
        "name": "Focus: Cacophony of Blades",
        "tier": 2,
        "tier_name": "",
        "level": 70,
        "type": "pcinnates",
        "beneficial": 1,
        "crc": 900,
        "cast_secs": 0.0,
        "recast_secs": 0.0,
        "recovery_secs": 0.0,
        "effects": json.dumps(
            [{"description": "Reduces the reuse time of Cacophony of Blades by 30 seconds.", "indentation": 0}]
        ),
    }
    enhance_row = {
        **focus_row,
        "name": "Enhance: Cacophony of Blades",
        "tier": 3,
        "crc": 901,
        "effects": json.dumps(
            [
                {
                    "description": "Increases duration of Cacophony of Blades and Peal Of Battle by 3 seconds.",
                    "indentation": 0,
                }
            ]
        ),
    }
    tree = {
        "name": "Troubador",
        "tree_type": "subclass",
        "nodes": [
            {"node_id": 1, "name": "Focus: Cacophony of Blades", "spellcrc": 900},
            {"node_id": 2, "name": "Enhance: Cacophony of Blades", "spellcrc": 901},
        ],
    }
    rows_by_crc = {(900, 2): focus_row, (901, 3): enhance_row}
    char = _fake_char(name="Adomia", spell_ids=[1])
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.views.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch(
            "backend.server.api.character.rotation._fetch_aa_trees",
            AsyncMock(return_value=[(49, {"1": 2, "2": 3})]),
        ),
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_ids", lambda ids: {1: cob}),
        patch.object(mod._spells, "find_by_crc", lambda crc, tier=None: rows_by_crc.get((crc, tier))),
        patch.object(mod._items, "spell_meta_by_names", lambda names: meta),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/simulator/character-buffs?name=Adomia")
    assert r.status_code == 200, r.text
    (b,) = r.json()["buffs"]
    assert b["recast_s"] == 30.0  # 60 - 30 (Focus)
    assert b["duration_s"] == 15.0  # 12 + 3 (Enhance)
    assert len(b["aa_adjustments"]) == 2


@pytest.mark.asyncio
async def test_character_buffs_includes_aa_granted_group_buffs(app):
    """Census spell lists OMIT AA-granted abilities (Sihtric's Fearless
    Morale: specced rank 1, absent from spell_list) — the member book
    must surface group-scope beneficials from SPENT AA nodes, at the
    era band's values (+2% group potency)."""
    from backend.server.api.character import rotation as mod

    fm_row = {
        "name": "Fearless Morale",
        "tier": 1,
        "tier_name": "Apprentice",
        "level": 70,
        "type": "spells",
        "beneficial": 1,
        "crc": 700,
        "target_type": "group",
        "icon_id": 1,
        "icon_backdrop": 2,
        "cast_secs": 2.0,
        "recast_secs": 10.0,
        "recovery_secs": 0.0,
        "effects": json.dumps(
            [
                {"description": "Increases Potency of group members (AE) by 2.0%.", "indentation": 0},
                {"description": "Makes group members (AE) immune to Fear effects", "indentation": 0},
            ]
        ),
    }
    tree = {
        "name": "Crusader",
        "tree_type": "class",
        "nodes": [{"node_id": 1, "name": "Fearless Morale", "spellcrc": 700, "maxtier": 1}],
    }
    char = _fake_char(name="Sihtric", spell_ids=[])
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.views.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch(
            "backend.server.api.character.rotation._fetch_aa_trees",
            AsyncMock(return_value=[(4, {"1": 1})]),
        ),
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_ids", lambda ids: {}),
        patch.object(mod._spells, "find_by_crc", lambda crc, tier=None: fm_row if crc == 700 else None),
        patch.object(mod._items, "spell_meta_by_names", lambda names: {}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/simulator/character-buffs?name=Sihtric")
    assert r.status_code == 200, r.text
    (b,) = r.json()["buffs"]
    assert b["name"] == "Fearless Morale"
    assert b["mods"] == {"potencyPct": 2.0}
    assert b["target_scope"] == "group"
    # AA "tiers" are ranks: no research-tier label; rank/max carried.
    assert b["tier_name"] == ""
    assert b["rank"] == 1 and b["max_rank"] == 1


@pytest.mark.asyncio
async def test_class_buffs_endpoint(app):
    """GET /api/simulator/class-buffs: scroll universe → beneficial
    group rows → parsed mods + procs + duration join."""
    from backend.server.api.character import rotation as mod

    song_row = _row(
        1,
        "Cacophony of Blades II",
        level=76,
        beneficial=1,
        recast=60.0,
        effects=[
            {"description": "Increases Haste of group members (AE) by 63.6.", "indentation": 0},
            {"description": "On a hit this spell will cast Blade Chime on target of attack.", "indentation": 0},
            {"description": "Inflicts 102 - 171 disease damage on target", "indentation": 1},
        ],
    )
    song_row["target_type"] = "group"
    meta = {"Cacophony of Blades II (Master)": {"spell_duration": 1200.0, "spell_power_cost": None}}
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch.object(mod._items, "class_spell_names", lambda cls: {"Cacophony of Blades II"}),
        patch.object(mod._spells, "beneficial_group_spells", lambda names, max_level: [song_row]),
        patch.object(mod._items, "spell_meta_by_names", lambda names: meta),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/simulator/class-buffs?cls=Dirge")
    assert r.status_code == 200, r.text
    (b,) = r.json()
    assert b["name"] == "Cacophony of Blades II" and b["target_scope"] == "group"
    assert b["mods"] == {"hastePct": 63.6}
    assert b["duration_s"] == 12.0 and b["recast_s"] == 60.0
    (proc,) = b["procs"]
    assert proc["trigger"] == "melee_hit" and proc["chance_pct"] == 100.0
    assert proc["components"][0]["max_dmg"] == 171.0


@pytest.mark.asyncio
async def test_class_buffs_filters_irrelevant(app):
    """Wards/heals/hate tools are dropped; stat raisers (even unmodeled
    ones like WIS) and damage procs stay."""
    from backend.server.api.character import rotation as mod

    def _buff(spell_id, name, effects):
        row = _row(spell_id, name, beneficial=1, effects=effects)
        row["target_type"] = "group"
        return row

    rows = [
        _buff(
            1,
            "Umbral Warding VI",
            [{"description": "Wards group members (AE) against 1,788 points of all damage.", "indentation": 0}],
        ),
        _buff(
            2,
            "Wisdom of Ancestors",
            [{"description": "Increases WIS of raid and group members (AE) by 53.5.", "indentation": 0}],
        ),
        _buff(
            3,
            "Dead Calm V",
            [
                {
                    "description": "On a combat hit this spell has a 12% chance to cast Crypt's Revenge on target of attack.",
                    "indentation": 0,
                },
                {"description": "Inflicts 237 - 396 disease damage on target", "indentation": 1},
            ],
        ),
        _buff(
            4,
            "Group Illusion: Gnoll",
            [{"description": "Shapechanges group members (AE) into a gnoll.", "indentation": 0}],
        ),
        # A heal riding a Max Health line and an armor buff riding
        # Mitigation — defensive rider stats never earn a row.
        _buff(
            6,
            "Prayer of Healing V",
            [
                {"description": "Heals group members (AE) for 876 - 1,071.", "indentation": 0},
                {"description": "Increases Max Health of group members (AE) by 642.9.", "indentation": 0},
            ],
        ),
        _buff(
            7,
            "Pledge of Armament V",
            [{"description": "Increases Mitigation of target vs physical damage by 552.", "indentation": 0}],
        ),
        # Allow-list semantics: STA-only and power regen never qualify;
        # a combined "STR and STA" line qualifies via STR.
        _buff(
            8,
            "Stamina Only",
            [{"description": "Increases STA of group members (AE) by 96.6.", "indentation": 0}],
        ),
        _buff(
            9,
            "Power Song",
            [{"description": "Increases Combat Power Regen of group members (AE) by 36.2.", "indentation": 0}],
        ),
        _buff(
            10,
            "Strength and Stamina",
            [{"description": "Increases STR and STA of group members (AE) by 41.0.", "indentation": 0}],
        ),
        _buff(
            11,
            "Resist Song",
            [{"description": "Increases Mitigation of group members (AE) vs noxious damage by 480.", "indentation": 0}],
        ),
        _buff(
            5,
            "Serene Sonata",
            [{"description": "Decreases Hate Gain of group members (AE) by 33.8.", "indentation": 0}],
        ),
    ]
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch.object(mod._items, "class_spell_names", lambda cls: {r["name"] for r in rows}),
        patch.object(mod._spells, "beneficial_group_spells", lambda names, max_level: rows),
        patch.object(mod._items, "spell_meta_by_names", lambda names: {}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/simulator/class-buffs?cls=Mystic")
    names = [b["name"] for b in r.json()]
    assert names == ["Wisdom of Ancestors", "Dead Calm V", "Strength and Stamina"]


@pytest.mark.asyncio
async def test_rotation_derived_modifiers(app):
    """Hidden modifiers auto-derive from worn gear + adorns: item
    base-damage lines, ACTIVE set-bonus speed effects and set procs, and
    class-tree AA base-damage lines. Set stat keys are ignored (census
    stats already include them)."""
    from backend.server.api.character import rotation as mod
    from backend.server.api.character.views import AdornSlotResponse, EquipmentSlotResponse

    char = _fake_char(spell_ids=[])
    char.equipment = [
        EquipmentSlotResponse(
            slot="Neck",
            name="Bloodthirsty Choker",
            item_id="123",
            adorn_slots=[AdornSlotResponse(color="white", adorn_id=str(400 + i)) for i in range(5)],
        ),
        EquipmentSlotResponse(slot="Ranged", name="Wand of Crystallized Plasma", item_id="124"),
        EquipmentSlotResponse(slot="Ear", name="Spooky Bone Hoop", item_id="125"),
        EquipmentSlotResponse(slot="Ear2", name="Bone Hoop of Spookiness", item_id="126"),
        EquipmentSlotResponse(slot="Waist", name="Sash of Secrets", item_id="127"),
    ]
    set_json = json.dumps(
        {
            "setbonus_list": [
                {
                    "requireditems": 3,
                    "effect": "Applies Blessing.",
                    "descriptiontag_1": "Increases Crit Chance of caster by 2.0.",
                },
                {
                    "requireditems": 5,
                    "basemodifier": 5.0,
                    "effect": "Applies Quickening of Nem Anhk.",
                    "descriptiontag_1": "Improves speed at which the cleric casts by 33%.",
                },
                {
                    "requireditems": 7,  # NOT active with 5 pieces
                    "descriptiontag_1": "Improves speed at which the cleric casts by 99%.",
                },
                {
                    "requireditems": 2,
                    "descriptiontag_1": "If target is vampire",
                    "descriptiontag_2": "Inflicts 2,861 divine damage on target.",
                    "descriptiontag_3": "On any combat or spell hit this spell may cast Abomination on target of attack.  Triggers about 1.8 times per minute.",
                },
            ]
        }
    )
    pact_tree = {
        "name": "Cleric",
        "tree_type": "class",
        "nodes": [{"node_id": 1, "name": "Pact of the Faithful", "spellcrc": 111}],
    }
    pact_row = _row(
        9,
        "Pact of the Faithful",
        beneficial=1,
        effects=[{"description": "Increases the base spell damage of the cleric by 10%.", "indentation": 0}],
    )
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    p1, p2, p3 = _catalogue_patches({})
    with (
        p1,
        p2,
        p3,
        patch("backend.server.api.character.views.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch(
            "backend.server.api.character.rotation._fetch_aa_trees",
            AsyncMock(return_value=[(3, {"1": 10})]),
        ),
        patch.object(mod._aas, "get_tree", lambda tid: pact_tree),
        patch.object(mod._spells, "find_by_crc", lambda crc, tier=None: pact_row if crc == 111 else None),
        patch.object(
            mod._items,
            "effect_lines_for_ids",
            lambda ids: [
                (123, "Bloodthirsty Choker", "Increases base damage of spells and combat arts by 25%.", 0),
                # Wand of Crystallized Plasma (real items.db text): the +8%
                # is nested under a rated proc → a TEMP buff window, never
                # folded into the always-on base_damage_bonus_pct.
                (124, "Wand of Crystallized Plasma", "When Equipped:", 0),
                (
                    124,
                    "Wand of Crystallized Plasma",
                    "On a spell cast this spell may cast Plasma Boost on caster.  "
                    "Lasts for 12.0 seconds.  Triggers about 1.8 times per minute.",
                    1,
                ),
                (124, "Wand of Crystallized Plasma", "Increases the base damage of hostile spells cast by 8%.", 2),
                (124, "Wand of Crystallized Plasma", "Cannot be modified except by direct means", 2),
                # School-specific flat damage (behaves like ability mod on
                # matching-school spells only).
                (125, "Spooky Bone Hoop", "When Equipped:", 0),
                (125, "Spooky Bone Hoop", "Increases disease damage done by spells by up to 30.", 1),
                # A SECOND item carrying the same named effect ("Disease
                # Cloud VI") — in-game these never stack: counted once.
                (126, "Bone Hoop of Spookiness", "When Equipped:", 0),
                (126, "Bone Hoop of Spookiness", "Increases disease damage done by spells by up to 30.", 1),
                # Scoped item timing cuts ("Arcane Recovery I" hostile
                # reuse; Nagol's Treasure all-spell cast time).
                (127, "Sash of Secrets", "When Equipped:", 0),
                (127, "Sash of Secrets", "Reduces reuse time of hostile spells by 1 percent.", 1),
                (128, "Nagol's Treasure", "When Equipped:", 0),
                (128, "Nagol's Treasure", "Reduces cast time of all spells by 5 percent.", 1),
            ],
        ),
        patch.object(
            mod._items,
            "named_effects_for_ids",
            lambda ids: {
                123: ["Vampiric Requiem"],
                124: ["Plasma Boost"],
                125: ["Disease Cloud VI"],
                126: ["Disease Cloud VI"],
                127: ["Arcane Recovery I"],
                128: ["Mental Breakdown IV"],
            },
        ),
        patch.object(
            mod._items,
            "set_bonus_rows_for_ids",
            lambda ids: [(400 + i, "Aegis of Nem Anhk", set_json) for i in range(5)],
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/rotation-data")
    assert r.status_code == 200, r.text
    d = r.json()["derived"]
    assert d["base_damage_bonus_pct"] == 35.0  # choker 25 + Pact 10 (wand's proc-nested 8% EXCLUDED)
    assert d["cast_speed_bonus_pct"] == 38.0  # set 33 (5pc active) + Nagol's all-spell 5
    # The wand's proc-granted +8% surfaces as a TEMP buff window instead.
    (pb,) = d["proc_buffs"]
    assert pb["name"] == "Plasma Boost" and pb["item"] == "Wand of Crystallized Plasma"
    assert pb["duration_s"] == 12.0 and pb["per_minute"] == 1.8 and pb["trigger"] == "spell_cast"
    assert pb["mods"] == {"baseDamagePct": 8.0}
    # The hoop's +30 disease is a SCHOOL-scoped flat, not base damage —
    # and the second item with the SAME effect name doesn't stack.
    assert d["school_damage_flat"] == {"disease": 30.0}
    # The sash's hostile-only reuse cut keeps its scope — it must NOT
    # land in the all-spell reuse bonus.
    assert d["reuse_bonus_pct"] == 0.0
    assert d["hostile_reuse_pct"] == 1.0
    kinds = sorted((s["kind"], s["name"]) for s in d["sources"])
    assert ("item", "Spooky Bone Hoop") in kinds
    assert ("item", "Bloodthirsty Choker") in kinds
    assert ("aa", "Pact of the Faithful") in kinds
    assert any(s["kind"] == "set" for s in d["sources"])
    # The 2pc vampire proc surfaces as a set passive.
    set_passives = [p for p in r.json()["passives"] if p["source"] == "set"]
    (sp,) = set_passives
    (proc,) = sp["procs"]
    assert proc["per_minute"] == 1.8 and proc["trigger"] == "any_hit"
    assert proc["components"][0]["condition"] == "If target is vampire"
    assert proc["components"][0]["max_dmg"] == 2861.0


@pytest.mark.asyncio
async def test_rotation_own_aa_adjustments_compress_dot(app):
    """The character's OWN Enhance: Soulrot (rank 5) applies to their
    Soulrot: +5% whole-tooltip damage, and the −2.5s duration cut
    COMPRESSES the dot — same tick count squeezed into the shorter
    window (4s/1s → 1.5s/0.375s; in-game tooltip confirmed)."""
    from backend.server.api.character import rotation as mod

    soulrot = _row(
        1,
        "Soulrot VII",
        level=57,
        effects=[
            {"description": "Inflicts 821 - 889 disease damage on target", "indentation": 0},
            {"description": "Inflicts 201 disease damage on target every second", "indentation": 0},
        ],
    )
    meta = {"Soulrot VII (Master)": {"spell_duration": 400.0, "spell_power_cost": None}}
    enhance_row = {
        "name": "Enhance: Soulrot",
        "tier": 5,
        "tier_name": "",
        "level": 70,
        "type": "pcinnates",
        "beneficial": 1,
        "crc": 901,
        "cast_secs": 0.0,
        "recast_secs": 0.0,
        "recovery_secs": 0.0,
        "effects": json.dumps(
            [
                {"description": "Reduces duration by 2.5 seconds", "indentation": 0},
                {"description": "Increases damage by 5%", "indentation": 0},
            ]
        ),
    }
    tree = {
        "name": "Necromancer",
        "tree_type": "subclass",
        "nodes": [{"node_id": 1, "name": "Enhance: Soulrot", "spellcrc": 901}],
    }
    char = _fake_char(name="Menludeth", spell_ids=[1])
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (char, False)
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.views.character_cache", mock_cache),
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch(
            "backend.server.api.character.rotation._fetch_aa_trees",
            AsyncMock(return_value=[(49, {"1": 5})]),
        ),
        patch.object(mod._aas, "get_tree", lambda tid: tree),
        patch.object(mod._spells, "find_by_ids", lambda ids: {1: soulrot}),
        patch.object(
            mod._spells, "find_by_crc", lambda crc, tier=None: enhance_row if (crc, tier) == (901, 5) else None
        ),
        patch.object(mod._items, "spell_meta_by_names", lambda names: meta),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Menludeth/rotation-data")
    assert r.status_code == 200, r.text
    (a,) = [x for x in r.json()["abilities"] if x["base_name"] == "Soulrot"]
    assert a["dmg_mod_pct"] == 5.0
    assert len(a["aa_adjustments"]) == 2
    assert a["duration_s"] == 1.5  # 4.0 − 2.5
    dot = next(c for c in a["components"] if c["kind"] == "dot")
    assert dot["duration_s"] == 1.5
    assert dot["interval_s"] == pytest.approx(0.375)  # 1.0 × (1.5 / 4.0)


@pytest.mark.asyncio
async def test_buff_tiers_endpoint(app):
    """GET /api/simulator/buff-tiers: every era tier row of one raid-buff
    line, parsed — the tier dropdown's data (values differ per tier)."""
    from backend.server.api.character import rotation as mod

    def tier_row(tier: int, tier_name: str, pct: float) -> dict:
        r = _row(900 + tier, "Unholy Strength V", level=73, beneficial=1)
        r["tier"] = tier
        r["tier_name"] = tier_name
        r["target_type"] = "raid"
        r["effects"] = json.dumps(
            [{"description": f"Increase spell damage of group and raid members by {pct}%.", "indentation": 0}]
        )
        return r

    rows = [tier_row(5, "Adept", 3.75), tier_row(7, "Expert", 4.82), tier_row(9, "Master", 5.0)]
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    with (
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch.object(mod._spells, "beneficial_buff_tiers", lambda base, lvl: rows if base == "Unholy Strength" else []),
        patch.object(mod._items, "spell_meta_by_names", lambda names: {}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/simulator/buff-tiers?name=Unholy%20Strength")
    assert r.status_code == 200, r.text
    tiers = [(b["tier_name"], b["mods"]) for b in r.json()]
    assert tiers == [
        ("Adept", {"baseDamagePct": 3.75}),
        ("Expert", {"baseDamagePct": 4.82}),
        ("Master", {"baseDamagePct": 5.0}),
    ]


@pytest.mark.asyncio
async def test_rotation_404_unknown_character(app):
    mock_db = MagicMock()
    mock_db.exists.return_value = True
    mock_census = AsyncMock()
    mock_census.get_character = AsyncMock(return_value=None)
    with (
        patch("backend.server.api.character.rotation._SPELLS_DB", mock_db),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_census),
    ):
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
