"""
Mirror of the Census /item/ collection (Postgres `items` schema).

All behaviour lives on :class:`ItemCatalogue` (the eq2db data-interface
convention — see AACatalogue / SpellCatalogue): DB lookups are instance
methods (the async ``find_by_name`` / ``find_by_id`` pair included); the
pure item-domain helpers (compute_class_label, extract_item_stats,
extract_effect_stats, item_to_row) are staticmethods on the same class so
consumers import ONE name — the shared ``catalogue`` instance. Module
level holds only types (GearRow), constants (SCHEMA, SERVER_MAX_LEVEL),
and the instance. Schema DDL is owned by db/migrations/0007_items.sql.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, NamedTuple

from backend.census._coerce import coerce_int as _coerce_int
from backend.census._coerce import coerce_str_or_none as _coerce_str
from backend.census.item_level import compute_ilvl
from backend.db_catalogue import PgCatalogue
from backend.db_helpers import like_escape
from backend.eq2db.classes import catalogue as _classes
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

_log = logging.getLogger(__name__)


SCHEMA = "items"


def _resolve_max_level() -> int | None:
    """
    SERVER_MAX_LEVEL env var caps item lookups by name to items usable at or
    below this level (e.g. 70 for an Echoes of Faydwer TLE).
    Unset → no level filtering.
    """
    import os

    v = os.getenv("SERVER_MAX_LEVEL")
    return int(v) if v else None


SERVER_MAX_LEVEL: int | None = _resolve_max_level()

# ---------------------------------------------------------------------------
# EQ2 class-group label helper
# ---------------------------------------------------------------------------
# Class-group membership is OWNED by the Postgres classes schema, accessed
# via backend.eq2db.classes.catalogue (archetype_groups / subclass_groups /
# crafter_names). Census API item rows use lowercase class-name keys
# ({"guardian": {...}}), so the tables below lowercase the canonical
# TitleCase names from the DB rows.
#
# LAZY on purpose (Phase 3): class data is a network read now, and this
# module is imported by conftest/scripts before migrations or env setup can
# run — the first compute_class_label call builds the tables, cached forever.
#
# DO NOT redefine class groupings here — edit the classes schema.

_class_tables_cache: tuple[frozenset[str], frozenset[str], list[tuple[str, frozenset[str]]]] | None = None


def _class_tables() -> tuple[frozenset[str], frozenset[str], list[tuple[str, frozenset[str]]]]:
    """(crafters, all_adventurers, archetype-priority groups), built once.

    Groups are checked in priority order: full archetypes first ("All
    Fighters"…), then subclasses ("All Warriors"…). compute_class_label
    removes matched classes from `remaining` as it goes, so a complete
    archetype is consumed before its constituent subclasses are tested."""
    global _class_tables_cache
    if _class_tables_cache is None:
        crafters = frozenset(name.lower() for name in _classes.crafter_names())
        adventurers = frozenset(n.lower() for _, members in _classes.archetype_groups() for n in members)
        archetypes = [
            (f"All {archetype}s", frozenset(n.lower() for n in members))
            for archetype, members in _classes.archetype_groups()
        ] + [(f"All {subclass}s", frozenset(n.lower() for n in members)) for subclass, members in _classes.subclass_groups()]
        _class_tables_cache = (crafters, adventurers, archetypes)
    return _class_tables_cache


# ---------------------------------------------------------------------------
# Effect-based stat extraction
# ---------------------------------------------------------------------------
# Some stats in EQ2 are not in the `modifiers` dict but are expressed as
# human-readable effect lines, e.g.:
#   "Increases Attack Speed of caster by 25.0"
#
# Each entry is (compiled_regex, canonical_stat_name).  The regex must have
# exactly one capture group that captures the numeric value.
#
# The stat name must match a key in STAT_MAP / an entry in item_stats so the
# existing search machinery works unchanged.

_EFFECT_STAT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "Increases Attack Speed of caster by 25.0"
    # "Increases Attack Speed of the caster by 25.0"
    (re.compile(r"Attack Speed of .+? by ([\d.]+)"), "Haste"),
]

_PVP_STAT_PREFIXES = ("pvp",)

# ---------------------------------------------------------------------------
# Generic field coercers (Census JSON → column values)
# ---------------------------------------------------------------------------


def _flag(flags: dict, key: str) -> int:
    val = flags.get(key)
    if isinstance(val, dict):
        val = val.get("value", 0)
    return 1 if val in (1, True, "1", 1.0) else 0


def _str_field(item: dict, key: str) -> str | None:
    """Stripped string or None — shared Census coercion semantics
    (backend.census._coerce), applied to a dict field."""
    return _coerce_str(item.get(key))


def _int_field(v: Any) -> int | None:
    """coerce_int with 0 treated as NULL (quest IDs etc.)."""
    return _coerce_int(v) or None


# Keeps 0 — exactly the shared coercer.
_int_field_zero = _coerce_int


class GearRow(NamedTuple):
    ilvl: float | None
    wield_style: str | None
    level: int | None  # level_to_use, for adorn-bonus calc
    tier_display: str | None  # for adorn-bonus calc


class ItemCatalogue(PgCatalogue):
    """Read (and build) access to the items schema.

    The eq2db data-interface convention (see AACatalogue / SpellCatalogue):
    the schema name lives on the instance; the shared module-level
    ``catalogue`` is the runtime entry point, and tests lease a scratch
    schema and re-point ``catalogue.schema``. The pure item-domain helpers
    are staticmethods here so the class is the one interface for everything
    item-shaped.

    The old SQLite startup backfills (pvp flag, effect stats,
    classification_list) are gone: ``item_to_row`` computes every derived
    column at write time, and historic rows arrived pre-backfilled via the
    one-time bulk copy. A new effect-stat pattern now means re-running
    scripts/backfill_item_stats.py rather than bumping a version gate.
    """

    READY_TABLE = "items"

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)

    # ── Pure helpers (no DB access — statics so the class is the ONE interface) ──

    @staticmethod
    def compute_class_label(classes: dict | None) -> str | None:
        """
        Return a human-readable class restriction label.

        Rules:
        - Any set that covers all 26 adventure classes (with or without crafters)
          → "All Classes"
        - Full archetype groups are collapsed: "All Fighters", "All Priests", etc.
        - Partial archetypes + individual classes are listed by display name.
        - None / empty → None
        """
        if not classes or not isinstance(classes, dict):
            return None

        crafters, all_adventurers, archetypes = _class_tables()
        keys = frozenset(classes.keys())
        adv = keys & all_adventurers

        # All 26 adventure classes present (crafters optional) → "All Classes"
        if adv >= all_adventurers:
            return "All Classes"

        parts: list[str] = []
        remaining = set(adv)

        for label, group in archetypes:
            if remaining >= group:
                parts.append(label)
                remaining -= group

        # Any leftover individual classes
        for key in sorted(remaining):
            entry = classes.get(key)
            display = entry.get("displayname", key.title()) if isinstance(entry, dict) else key.title()
            parts.append(display)

        # Crafter-only items (no adventure classes matched at all)
        if not parts:
            crafter_keys = keys & crafters
            if crafter_keys:
                return "Crafters"

        return " / ".join(parts) if parts else None

    @staticmethod
    def extract_item_stats(raw: dict) -> dict[str, float]:
        """
        Return a mapping of canonical stat display-name → value extracted from the
        Census ``modifiers`` dict stored in raw_json.

        Multiple API tag names that resolve to the same display name (e.g.
        ``arcane``/``elemental``/``noxious`` → "Resistances") keep only the first
        non-zero value encountered.
        """
        # Import lazily to avoid circular import at module level
        from backend.census.constants import STAT_MAP  # noqa: PLC0415

        modifiers = raw.get("modifiers") or {}
        result: dict[str, float] = {}
        for tag, mod in modifiers.items():
            if not isinstance(mod, dict):
                continue
            key = tag.lower()
            mapping = STAT_MAP.get(key)
            if mapping:
                display_name = mapping[0]
            else:
                api_dn = (mod.get("displayname") or "").strip()
                if api_dn.lower() == "all":
                    display_name = "Ability Mod"
                elif len(api_dn) > 3:
                    display_name = api_dn
                else:
                    continue  # no usable name → skip
            value = float(mod.get("value") or 0)
            if value and display_name not in result:
                result[display_name] = value
        return result

    @staticmethod
    def extract_effect_stats(raw: dict) -> dict[str, float]:
        """Return a mapping of canonical stat name → value parsed from effect_list.

        Complements extract_item_stats (which only reads the ``modifiers`` dict).
        Only extracts stats listed in _EFFECT_STAT_PATTERNS.  When both a modifier
        and an effect line exist for the same stat the modifier value takes
        precedence (callers use INSERT OR IGNORE for these rows).
        """
        result: dict[str, float] = {}
        for eff in raw.get("effect_list") or []:
            if not isinstance(eff, dict):
                continue
            desc = str(eff.get("description") or "")
            for pattern, stat_name in _EFFECT_STAT_PATTERNS:
                if stat_name in result:
                    continue  # already captured; keep first occurrence
                m = pattern.search(desc)
                if m:
                    try:
                        result[stat_name] = float(m.group(1))
                    except (ValueError, IndexError):
                        pass
        return result

    @staticmethod
    def _is_pvp_item(item: dict) -> int:
        """Return 1 if the item is PvP-specific, 0 otherwise.

        Detection strategy (either condition is sufficient):
        1. Has a stat whose Census name starts with 'pvp' (pvptoughness, pvplethality,
           pvpcriticalmitigation, etc.).
        2. The raw item JSON contains the substring 'pvp' (case-insensitive), which
           catches effect restrictions like 'Must be engaged in pvp combat'.
        """
        # Check stat modifiers. Census ships `modifiers` as a dict keyed by stat name
        # (e.g. {"pvptoughness": {...}}); older/alternate shapes are a list of dicts.
        for mod_list_key in ("modifiers", "stat_list", "stats"):
            coll = item.get(mod_list_key)
            if isinstance(coll, dict):
                names = [str(k).lower() for k in coll]
            elif isinstance(coll, list):
                names = [str(mod.get("name") or mod.get("stat") or "").lower() for mod in coll if isinstance(mod, dict)]
            else:
                continue
            if any(name.startswith(p) for name in names for p in _PVP_STAT_PREFIXES):
                return 1
        # Check raw JSON text (catches effects + any other pvp references)
        raw = json.dumps(item).lower()
        if "pvp" in raw:
            return 1
        return 0

    @staticmethod
    def item_to_row(item: dict) -> dict:
        """Convert a raw Census API item dict to a flat DB row dict."""
        typeinfo = item.get("typeinfo") or {}
        flags = item.get("flags") or {}
        slot_list = item.get("slot_list") or []
        extended = item.get("_extended") or {}
        reqskill = item.get("requiredskill")
        if not isinstance(reqskill, dict):
            reqskill = {}

        discovered = (extended.get("discovered") or {}).get("timestamp")
        aq = _int_field(item.get("associatedquest"))
        autoq = _int_field(item.get("autoquest"))

        tier_display = _str_field(item, "tier") or "COMMON"
        ilvl = compute_ilvl(
            level_to_use=_int_field_zero(item.get("leveltouse")),
            tier_display=tier_display,
            potency=ItemCatalogue.extract_item_stats(item).get("Potency", 0.0),
            item_type=_str_field(item, "type"),
            two_handed=typeinfo.get("wieldstyle") == "Two-Handed",
        )

        return {
            "id": item.get("id"),
            "displayname": str(item.get("displayname") or ""),
            "displayname_lower": str(item.get("displayname") or "").lower(),
            "gamelink": _str_field(item, "gamelink"),
            "description": _str_field(item, "description"),
            "last_update": _int_field_zero(item.get("last_update")),
            "tier": _str_field(item, "tier"),
            "tierid": _int_field_zero(item.get("tierid")),
            "tier_display": tier_display,
            "type": _str_field(item, "type"),
            "typeid": _int_field_zero(item.get("typeid")),
            "item_level": _int_field_zero(item.get("itemlevel")),
            "level_to_use": _int_field_zero(item.get("leveltouse")),
            "planar_level": _int_field_zero(item.get("planar_level")),
            "ilvl": ilvl,
            "icon_id": _int_field_zero(item.get("iconid")),
            "max_stack_size": _int_field_zero(item.get("maxstacksize")),
            "slot": slot_list[0].get("name") if slot_list else None,
            "armor_class_min": _int_field_zero(typeinfo.get("minarmorclass")),
            "armor_class_max": _int_field_zero(typeinfo.get("maxarmorclass")),
            "damage_min": _int_field_zero(typeinfo.get("mindamage")),
            "damage_max": _int_field_zero(typeinfo.get("maxdamage")),
            "damage_base": _int_field_zero(typeinfo.get("damage")),
            "damage_type": _str_field(typeinfo, "damagetype"),
            "damage_type_id": _int_field_zero(typeinfo.get("damagetypeid")),
            "damage_rating": typeinfo.get("damagerating"),
            "delay": typeinfo.get("delay"),
            "wield_style": _str_field(typeinfo, "wieldstyle"),
            "spell_name": _str_field(typeinfo, "spellname"),
            "spell_tier_id": _int_field_zero(typeinfo.get("tier")),
            "spell_cast_time": typeinfo.get("spellcasttime"),
            "spell_recast_time": typeinfo.get("spellrecasttime"),
            "spell_duration": typeinfo.get("spellduration"),
            "weapon_range_min": typeinfo.get("minrange"),
            "weapon_range_max": typeinfo.get("range"),
            "food_duration": _str_field(typeinfo, "duration"),
            "food_satiation": _str_field(typeinfo, "satiation"),
            "food_level": _int_field_zero(typeinfo.get("foodlevel")),
            "adornment_color": _str_field(typeinfo, "color"),
            "container_slots": _int_field_zero(typeinfo.get("slots")),
            "status_reduction": _int_field_zero(typeinfo.get("statusreduction")),
            "max_charges": _int_field_zero(item.get("maxcharges")),
            "setbonus_name": (item.get("setbonus_info") or {}).get("displayname"),
            "unique_equip_group": (item.get("unique_equipment_group") or {}).get("text"),
            "unique_equip_wearable_count": _int_field_zero(
                (item.get("unique_equipment_group") or {}).get("wearable_count")
            ),
            "unique_equip_prestige": 1 if (item.get("unique_equipment_group") or {}).get("prestige") == "true" else 0,
            "required_skill_name": reqskill.get("text"),
            "required_skill_min": _int_field_zero(reqskill.get("min_skill")),
            "associated_quest": aq,
            "autoquest": autoq,
            "first_discovered": _int_field_zero(discovered),
            "visible": _int_field_zero(item.get("visible")),
            "typeinfo_name": _str_field(typeinfo, "name"),
            "classes_json": json.dumps(typeinfo["classes"]) if typeinfo.get("classes") is not None else None,
            "physical_damage_absorption": _int_field_zero(typeinfo.get("physicaldamageabsorption")),
            "class_label": ItemCatalogue.compute_class_label(typeinfo.get("classes")),
            "class_count": len(typeinfo["classes"]) if typeinfo.get("classes") else None,
            "skill_type": _str_field(typeinfo, "skilltype"),
            "spell_target": _str_field(typeinfo, "spelltarget"),
            "spell_range": _str_field(typeinfo, "spellrange"),
            "spell_power_cost": _int_field_zero(typeinfo.get("spellpowercost")),
            "spell_resistability": _str_field(typeinfo, "resistability"),
            "flag_heirloom": _flag(flags, "heirloom"),
            "flag_lore": _flag(flags, "lore"),
            "flag_lore_equip": _flag(flags, "lore-equip"),
            "flag_no_trade": _flag(flags, "notrade"),
            "flag_no_value": _flag(flags, "novalue"),
            "flag_no_zone": _flag(flags, "nozone"),
            "flag_prestige": _flag(flags, "prestige"),
            "flag_relic": _flag(flags, "relic"),
            "flag_attunable": _flag(flags, "attunable"),
            "flag_ornate": _flag(flags, "ornate"),
            "flag_refined": _flag(flags, "refined"),
            "flag_infusable": _flag(flags, "infusable"),
            "flag_indestructible": _flag(flags, "indestructible"),
            "flag_pvp": ItemCatalogue._is_pvp_item(item),
            "raw_json": json.dumps(item),
            "classification_list": json.dumps(item.get("classification_list") or []),
        }

    # ── Build (scripts/download_items.py) ────────────────────────────────────

    def upsert_items(self, items: list[dict], conn: Any) -> int:
        """Upsert a batch of raw Census item dicts. Returns number inserted/replaced."""
        rows = [self.item_to_row(item) for item in items]
        conn.executemany(_SQL["upsert"], rows)
        # Maintain item_stats side-table.
        # Modifier stats (from `modifiers` dict) are upserted first (DO UPDATE).
        # Effect stats (parsed from effect_list text) are inserted second with
        # DO NOTHING so that modifier values always win when both are present.
        mod_stat_rows: list[tuple] = []
        effect_stat_rows: list[tuple] = []
        for item in items:
            item_id = item.get("id")
            if item_id is None:
                continue
            for stat_name, value in self.extract_item_stats(item).items():
                mod_stat_rows.append((item_id, stat_name, value))
            for stat_name, value in self.extract_effect_stats(item).items():
                effect_stat_rows.append((item_id, stat_name, value))
        if mod_stat_rows:
            conn.executemany(_SQL["insert_item_stat_replace"], mod_stat_rows)
        if effect_stat_rows:
            conn.executemany(_SQL["insert_item_stat_ignore"], effect_stat_rows)
        conn.commit()
        return len(rows)

    def item_count(self, conn: Any) -> int:
        return self.fetchval(conn.execute(_SQL["count"]))

    # ── Lookups ──────────────────────────────────────────────────────────────

    def gear_for_ids(self, ids: list[int]) -> dict[int, GearRow]:
        """Return {item_id: GearRow} for the given ids (read-only).

        Covers both worn items (use ``ilvl``/``wield_style``) and adornments (use
        ``level``/``tier_display`` for the adorn bonus) in one query. Ids missing
        from the DB are absent from the result; non-gear items have ``ilvl=None``."""
        if not ids:
            return {}
        rows = self._fetchall(_SQL["gear_for_ids"], (list(ids),))
        return {
            row["id"]: GearRow(row["ilvl"], row["wield_style"], row["level_to_use"], row["tier_display"])
            for row in rows
        }

    def stats_for_ids(self, ids: list[int]) -> list[tuple[int, str, float]]:
        """All ``(item_id, stat, value)`` rows for the given ids (read-only).

        One row per stat per distinct id — callers holding duplicate ids
        (two copies of the same ring) must weight by occurrence themselves."""
        if not ids:
            return []
        rows = self._fetchall(_SQL["stats_for_ids"], (list(ids),))
        return [(row["item_id"], row["stat"], row["value"]) for row in rows]

    def set_bonus_rows_for_ids(self, ids: list[int]) -> list[tuple[int, str, str | None]]:
        """``(item_id, setbonus_name, raw_json)`` for the ids that belong to an
        item set (read-only)."""
        if not ids:
            return []
        rows = self._fetchall(_SQL["set_bonus_rows_for_ids"], (list(ids),))
        return [(row["id"], row["setbonus_name"], row["raw_json"]) for row in rows]

    def class_spell_names(self, cls: str) -> set[str]:
        """Base spell names (tier suffix stripped) a class can scribe,
        from the spellscroll rows' classes_json — the per-class spell
        universe (spells.db has no class column). SYNC; run via run_sync.

        A class counts only when its entry carries a real scribe level —
        legacy all-class collection scrolls (e.g. one "Breeze (Master)")
        list every class, artisans included, at level 0."""
        cls_key = re.sub(r"[^a-z]", "", cls.lower())
        if not cls_key:
            return set()
        out: set[str] = set()
        rows = self._fetchall(_SQL["spellscroll_names_for_class"], (f'%"{cls_key}"%',))
        for row in rows:
            spell_name, classes_json = row["spell_name"], row["classes_json"]
            try:
                entry = (json.loads(classes_json or "{}") or {}).get(cls_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(entry, dict) or (entry.get("level") or 0) <= 0:
                continue
            m = re.match(r"^(.*) \([^)]+\)$", spell_name or "")
            out.add(m.group(1) if m else (spell_name or ""))
        out.discard("")
        return out

    def effect_lines_for_ids(self, item_ids: list[int]) -> list[tuple[int, str, str, int]]:
        """(item_id, displayname, effect line, indentation) rows across the
        given items (from raw_json effect_list), ORDER PRESERVED — the
        rotation simulator scans worn gear + adorns for hidden damage
        bonuses, and the indentation tells permanent 'When Equipped'
        bonuses apart from lines nested under a proc trigger (which belong
        to a TEMP buff). SYNC; run via run_sync."""
        ids = [i for i in item_ids if i]
        if not ids:
            return []
        out: list[tuple[int, str, str, int]] = []
        for row in self._fetchall(_SQL["raw_json_by_ids"], (ids,)):
            item_id, raw_json = row["id"], row["raw_json"]
            try:
                raw = json.loads(raw_json or "{}")
            except (TypeError, ValueError):
                continue
            name = str(raw.get("displayname") or item_id)
            for e in raw.get("effect_list") or []:
                if isinstance(e, dict) and e.get("description"):
                    out.append((item_id, name, str(e["description"]), int(e.get("indentation") or 0)))
        return out

    def named_effects_for_ids(self, item_ids: list[int]) -> dict[int, list[str]]:
        """{item_id: ['When Equipped' effect names]} from raw_json
        adornment_list ('Arcane Recovery I', 'Disease Cloud VI'). The
        name carries the in-game stacking rule the rotation simulator
        enforces: effects with the SAME name never stack (two Spooky
        Bone Hoops = one 'Disease Cloud VI'), while different tiers of
        the family (VI vs VII) are different names and do. SYNC; run
        via run_sync."""
        ids = [i for i in item_ids if i]
        if not ids:
            return {}
        out: dict[int, list[str]] = {}
        for row in self._fetchall(_SQL["raw_json_by_ids"], (ids,)):
            item_id, raw_json = row["id"], row["raw_json"]
            try:
                raw = json.loads(raw_json or "{}")
            except (TypeError, ValueError):
                continue
            adorns = raw.get("adornment_list") or []
            if isinstance(adorns, dict):
                adorns = [adorns]
            names = [str(a["name"]) for a in adorns if isinstance(a, dict) and a.get("name")]
            if names:
                out[item_id] = names
        return out

    def spell_meta_by_names(self, names: list[str]) -> dict[str, dict]:
        """{spell_name: {"spell_duration": float|None, "spell_power_cost": int|None,
        "effects": list[{"description", "indentation"}]}} for the given
        "<Name> (<TierName>)" spellscroll names — the rotation simulator's
        duration/power/effect-text join. SYNC; callers run it via run_sync.

        spell_duration is in hundredths of a second (see
        backend.eq2db.spell_effects.SPELL_DURATION_DIVISOR). ``effects`` is
        the scroll's effect_list from raw_json — the properly SCALED damage
        text; the spells.db spell-record text is unscaled for some spells
        (Smite Corruption reads "1 - 2" where the scroll says "132 - 161"),
        so consumers prefer this when present."""
        if not names:
            return {}
        out: dict[str, dict] = {}
        for row in self._fetchall(_SQL["spell_meta_by_names"], (list(names),)):
            effects: list[dict] = []
            if row["raw_json"]:
                try:
                    raw = json.loads(row["raw_json"])
                    effects = [
                        {"description": e.get("description"), "indentation": e.get("indentation")}
                        for e in (raw.get("effect_list") or [])
                        if isinstance(e, dict)
                    ]
                except (TypeError, ValueError):
                    effects = []
            out[row["spell_name"]] = {
                "spell_duration": row["spell_duration"],
                "spell_power_cost": row["spell_power_cost"],
                "effects": effects,
            }
        return out

    async def find_by_name(self, name: str) -> dict | None:
        """Return raw Census JSON dict for the closest name match, or None."""
        from backend import pg  # deferred: sync-only consumers never pay the import

        async with pg.aconnection() as db:
            await db.execute(pg.search_path_sql(self.schema))

            async def _best(where_clause: str, params: tuple) -> dict | None:
                """
                Return the best matching row given a WHERE clause + params.

                When SERVER_MAX_LEVEL is set:
                  1. Try items with level_to_use <= max (or no level requirement).
                     Order: highest level first, then best tier, then most recent.
                  2. If nothing qualifies, fall back to the highest-level item overall
                     (so the user at least gets something rather than nothing).
                When SERVER_MAX_LEVEL is not set:
                  Order by tierid DESC, last_update DESC (original behaviour).
                """
                if SERVER_MAX_LEVEL is not None:
                    # Phase 1: valid for current expansion
                    cur = await db.execute(
                        _SQL["find_by_name_level_capped"].format(where=where_clause),
                        params + (SERVER_MAX_LEVEL,),
                    )
                    row = await cur.fetchone()
                    if row:
                        return row
                    # Phase 2: nothing valid — return highest-level item anyway
                    cur = await db.execute(
                        _SQL["find_by_name_any_level"].format(where=where_clause),
                        params,
                    )
                    return await cur.fetchone()
                cur = await db.execute(
                    _SQL["find_by_name_no_max_level"].format(where=where_clause),
                    params,
                )
                return await cur.fetchone()

            # Exact match first
            row = await _best("displayname_lower = %s", (name.lower(),))
            if row:
                return json.loads(row["raw_json"])
            # LIKE fallback — escape user input so '%' / '_' in a literal name
            # can't silently broaden the match or force a table scan.
            row = await _best(
                "displayname_lower LIKE %s ESCAPE '\\'",
                (f"%{like_escape(name.lower())}%",),
            )
            return json.loads(row["raw_json"]) if row else None

    async def find_by_id(self, item_id: int) -> dict | None:
        """Return raw Census JSON dict for the given item ID, or None."""
        from backend import pg  # deferred: sync-only consumers never pay the import

        async with pg.aconnection() as db:
            await db.execute(pg.search_path_sql(self.schema))
            cur = await db.execute(_SQL["find_by_id_raw_json"], (item_id,))
            row = await cur.fetchone()
            return json.loads(row["raw_json"]) if row else None

    async def find_raw_by_ids(self, item_ids: list[int]) -> dict[int, dict]:
        """{item_id: raw Census JSON dict} for all known ids, ONE query —
        the batch form of :meth:`find_by_id` (equipment parsing resolves
        ~25 slots + adorns per character; per-slot lookups were an N+1)."""
        ids = [i for i in item_ids if i]
        if not ids:
            return {}
        from backend import pg  # deferred: sync-only consumers never pay the import

        out: dict[int, dict] = {}
        async with pg.aconnection() as db:
            await db.execute(pg.search_path_sql(self.schema))
            cur = await db.execute(_SQL["raw_json_by_ids"], (ids,))
            for row in await cur.fetchall():
                try:
                    out[row["id"]] = json.loads(row["raw_json"] or "{}")
                except (TypeError, ValueError):
                    continue
        return out


# The shared default instance — every runtime consumer goes through this.
catalogue = ItemCatalogue()

# Script-facing `_meta` aliases (download_items.py resume offsets) — bound
# to the shared instance so a re-pointed catalogue.schema carries through.
get_meta = catalogue.get_meta
set_meta = catalogue.set_meta
