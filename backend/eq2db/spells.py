"""
Mirror of the Census /spell/ collection (Postgres `spells` schema).

Each row is one spell entry — a specific tier of a specific spell (e.g.
"Divine Strike III Adept" is a separate row from "Divine Strike III Master").
The `crc` field groups all tier-variants of the same base spell together.

167 k rows total; download once with scripts/download_spells.py and refresh
whenever spells are patched (rare — typically expansion launches only).

Character spell-check looks up spell IDs in this table so the per-character
Census call can return bare IDs instead of resolved spell objects (no
c:resolve overhead).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypedDict

from backend.census._coerce import coerce_float as _float
from backend.census._coerce import coerce_int as _int
from backend.census._coerce import coerce_str_or_none as _str
from backend.db_catalogue import PgCatalogue
from backend.sql_loader import load_sql


class SpellRow(TypedDict, total=False):
    """Row shape returned by ``find_by_id`` / ``find_by_ids`` / ``find_by_crc`` / ``find_by_name``.

    ``total=False`` because a query with a narrower SELECT list (e.g. name-only)
    still returns a valid but incomplete dict. Callers that need a guaranteed
    field should use ``dict.get`` with a sensible default.
    """

    id: int
    name: str
    name_lower: str
    base_name: str
    base_name_lower: str
    tier: int
    tier_name: str
    type: str
    typeid: int
    level: int
    given_by: str
    crc: int
    beneficial: int
    passes_spellcheck: int
    cast_secs: float
    recast_secs: float
    recovery_secs: float
    target_type: str
    aoe_radius: float
    max_targets: int
    description: str
    icon_id: int
    icon_backdrop: int
    effects: str  # JSON-encoded
    last_update: int


_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


SCHEMA = "spells"

# Roman-numeral suffix pattern (I–XX) used for base_name computation.
# Matches a space-separated Roman numeral at the end of a spell name.
_ROMAN_RE = re.compile(
    r"\s+(?:XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)$",
    re.IGNORECASE,
)

_BLOCKLIST_PATH: Path = Path(__file__).resolve().parent.parent.parent / "data" / "spells" / "blocklist.json"

# SQL queries live in spells.sql (PG dialect; DDL is in db/migrations/);
# loaded once at import. The shared column-list fragment is spliced into
# every find_* query at format-time.
_SQL = load_sql(__file__)

# The column list lives in spells.sql under the `select_cols` block — fragment
# spliced into every find_* query at format-time.
_SELECT_COLS = _SQL["select_cols"]


class Blocklist:
    """
    Immutable set of blocked base-spell names that supports both exact matches
    and wildcard patterns (fnmatch-style).

    Examples in blocklist.json:
        "Fighting Chance"   – exact base-name match (Roman suffix stripped by caller)
        "Illusion:*"        – wildcard: blocks any spell whose base name starts
                              with "Illusion:" (spaces, colons, etc. all matched by *)

    Usage is identical to a frozenset — callers just use ``name in blocklist``.
    """

    __slots__ = ("_exact", "_patterns")

    def __init__(self, exact: frozenset[str], patterns: list[str]) -> None:
        self._exact = exact  # lowercased, Roman-stripped literals
        self._patterns = patterns  # lowercased wildcard patterns

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        if name in self._exact:
            return True
        for pat in self._patterns:
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def __bool__(self) -> bool:
        return bool(self._exact or self._patterns)

    def __repr__(self) -> str:
        return f"Blocklist(exact={len(self._exact)}, patterns={len(self._patterns)})"


def _row_to_dict(row: dict) -> SpellRow:
    return dict(row)  # type: ignore[return-value]


class SpellCatalogue(PgCatalogue):
    """Read (and build) access to the spells schema, with per-instance caching.

    Only the CRC lookup is cached (the hot path — AA tooltips resolve spell
    effects by crc per hover); id/name lookups take dynamic inputs and stay
    uncached. ``upsert_spells`` clears the crc cache so changed spell data
    can't be served stale.
    """

    READY_TABLE = "spells"

    # Cache bound — parity with the pre-catalogue @lru_cache(maxsize=4096).
    # The route feeding this (GET /aa/spell/{crc}?tier=N) takes arbitrary
    # client ints, so an unbounded dict would grow until process restart.
    _CRC_CACHE_MAX = 4096

    def __init__(self, schema: str = SCHEMA) -> None:
        super().__init__(schema)
        self._crc_cache: dict[tuple[int, int | None], SpellRow | None] = {}

    def clear_caches(self) -> None:
        """Reset the per-instance caches — used by tests and upsert_spells."""
        super().clear_caches()
        self._crc_cache.clear()

    def _cache_info(self) -> dict[str, int]:
        return {"crc_cache": len(self._crc_cache)}

    # ── Pure helpers (no DB access — statics so the class is the ONE interface) ──

    @staticmethod
    def strip_roman(name: str) -> str:
        """Strip a trailing Roman-numeral rank (I–XX) from a spell name."""
        return _ROMAN_RE.sub("", name).strip()

    @staticmethod
    def unique_highest_entries(entries: list) -> list:
        """For each base spell name + spell_type, keep only the highest-level entry.

        Works on any objects (or dicts) that expose .name/.spell_type/.level
        (SpellEntry) or ["name"]/["type"]/["level"] (raw DB rows).
        """
        best: dict[tuple, object] = {}
        for e in entries:
            if isinstance(e, dict):
                name = e.get("name") or ""
                spell_type = e.get("type") or ""
                level = e.get("level") or 0
            else:
                name = getattr(e, "name", "")
                spell_type = getattr(e, "spell_type", "")
                level = getattr(e, "level", 0) or 0
            key = (SpellCatalogue.strip_roman(name), spell_type)
            if key not in best:
                best[key] = e
            else:
                existing = best[key]
                elevel = (
                    (existing.get("level") or 0) if isinstance(existing, dict) else (getattr(existing, "level", 0) or 0)
                )
                if level > elevel:
                    best[key] = e
        return list(best.values())

    @staticmethod
    def load_blocklist(path: Path = _BLOCKLIST_PATH) -> Blocklist:
        """Parse blocklist.json and return a Blocklist.

        Each entry may be:
          - an exact base-spell name  (Roman suffixes stripped automatically)
          - a wildcard pattern        (fnmatch: * matches anything, ? matches one char)

        Re-reads the file on every call so edits take effect without a restart.
        """
        if not path.exists():
            return Blocklist(frozenset(), [])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            names: list[str] = data.get("blocked", []) if isinstance(data, dict) else data

            exact: list[str] = []
            patterns: list[str] = []
            for n in names:
                if not isinstance(n, str):
                    continue
                lowered = n.strip().lower()
                if not lowered:
                    continue
                if "*" in lowered or "?" in lowered:
                    # Wildcard — keep as-is (caller already strips Roman suffixes
                    # before the `in` check, so patterns match the stripped name)
                    patterns.append(lowered)
                else:
                    # Exact — strip Roman suffix so "Fighting Chance" also blocks
                    # "Fighting Chance I", "Fighting Chance II", etc.
                    exact.append(SpellCatalogue.strip_roman(lowered))

            return Blocklist(frozenset(exact), patterns)
        except Exception as exc:
            _log.warning("[spells-db] Failed to load blocklist: %s", exc)
            return Blocklist(frozenset(), [])

    @staticmethod
    def _passes_spellcheck(row: dict) -> int:
        """Return 1 if this spell row would survive the spellcheck filter, else 0."""
        level = row.get("level") or 0
        typ = row.get("type") or ""
        given_by = row.get("given_by") or ""
        if level <= 0:
            return 0
        if typ not in ("spells", "arts"):
            return 0
        if given_by in ("alternateadvancement", "class"):
            return 0
        return 1

    @staticmethod
    def _parse_effects(spell: dict) -> str:
        """Extract effect_list into a compact JSON string.

        Always returns a JSON string (never None):
          - Non-empty array  → the effect lines
          - '[]'             → processed, genuinely no effects in Census
        """
        raw = spell.get("effect_list")
        if raw is None:
            return "[]"
        if not isinstance(raw, list):
            _log.warning(
                "[spells-db] effect_list for spell %s has unexpected shape %s — returning empty",
                spell.get("id"),
                type(raw).__name__,
            )
            return "[]"
        effects = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            desc = str(e.get("description") or "").strip()
            if not desc:
                continue
            effects.append(
                {
                    "description": desc,
                    "indentation": int(e.get("indentation") or 0),
                }
            )
        return json.dumps(effects)

    @staticmethod
    def spell_to_row(spell: dict) -> dict:
        """Convert a raw Census /spell/ dict into a flat DB row dict."""
        icon = spell.get("icon") or {}
        cast_h = _int(spell.get("cast_secs_hundredths"))
        rec_t = _int(spell.get("recovery_secs_tenths"))
        desc = spell.get("description")
        if isinstance(desc, dict):
            desc = None  # Census sometimes returns {} for empty descriptions

        name = str(spell.get("name") or "")
        name_lower = name.lower()
        base = SpellCatalogue.strip_roman(name)
        base_lower = base.lower()

        row = {
            "id": _int(spell.get("id")),
            "name": name,
            "name_lower": name_lower,
            "base_name": base,
            "base_name_lower": base_lower,
            "tier": _int(spell.get("tier")),
            "tier_name": _str(spell.get("tier_name")),
            "type": _str(spell.get("type")),
            "typeid": _int(spell.get("typeid")),
            "level": _int(spell.get("level")),
            "given_by": _str(spell.get("given_by")),
            "crc": _int(spell.get("crc")),
            "beneficial": 1 if spell.get("beneficial") == 1 else 0,
            "cast_secs": cast_h / 100.0 if cast_h is not None else None,
            "recast_secs": _float(spell.get("recast_secs")),
            # DELIBERATELY ÷10 even though the census field actually carries
            # hundredths: every stored row carries this 10×-inflated value
            # and the ONLY consumer (the rotation endpoint) normalises with
            # spell_effects.RECOVERY_DIVISOR at serve time. Changing this to
            # ÷100 requires removing that divisor AND re-downloading the whole
            # catalogue in lockstep — do not "fix" it in isolation.
            "recovery_secs": rec_t / 10.0 if rec_t is not None else None,
            "target_type": _str(spell.get("target_type")),
            "aoe_radius": _float(spell.get("aoe_radius_meters")),
            "max_targets": _int(spell.get("max_targets")),
            "description": _str(desc),
            "icon_id": _int(icon.get("id")),
            "icon_backdrop": _int(icon.get("backdrop")),
            "effects": SpellCatalogue._parse_effects(spell),
            "last_update": _int(spell.get("last_update")),
        }
        row["passes_spellcheck"] = SpellCatalogue._passes_spellcheck(row)
        return row

    # ── Build (scripts/download_spells.py) ───────────────────────────────────

    def upsert_spells(self, spells: list[dict], conn: Any) -> int:
        """Upsert a batch of raw Census spell dicts. Returns the number inserted/replaced."""
        rows = [self.spell_to_row(s) for s in spells if s.get("id") is not None]
        conn.executemany(_SQL["upsert"], rows)
        conn.commit()
        self.clear_caches()  # spell data changed; stale CRC lookups would lie
        return len(rows)

    def spell_count(self, conn: Any) -> int:
        return self.fetchval(conn.execute(_SQL["count"]))

    # ── Lookups (async-friendly via asyncio.to_thread) ───────────────────────

    def find_by_id(self, spell_id: int) -> SpellRow | None:
        """Return a spell row dict for the given ID, or None."""
        row = self._fetchone(_SQL["find_by_id"].format(cols=_SELECT_COLS), (spell_id,))
        return _row_to_dict(row) if row else None

    def find_by_ids(self, spell_ids: list[int]) -> dict[int, SpellRow]:
        """Return {spell_id: row_dict} for all matching IDs. Missing IDs are omitted."""
        if not spell_ids:
            return {}
        rows = self._fetchall(
            _SQL["find_by_ids"].format(cols=_SELECT_COLS),
            (list(spell_ids),),
        )
        return {row["id"]: _row_to_dict(row) for row in rows}

    def beneficial_group_spells(self, names: list[str], max_level: int) -> list[SpellRow]:
        """The rotation simulator's group-buff universe: among the given
        exact spell names, the beneficial group/raid/single-ally rows at
        or under ``max_level`` — best tier per exact name, then the
        highest-level rank per base line (Rousing Tune VII beats VI)."""
        if not names:
            return []
        # One `= ANY` query for the whole name list.
        fetched = self._fetchall(
            _SQL["beneficial_group_by_names"].format(cols=_SELECT_COLS),
            (list(names), max_level),
        )
        rows: list[SpellRow] = [_row_to_dict(r) for r in fetched]
        # Best tier per exact name (raiders run Masters where they exist).
        by_name: dict[str, SpellRow] = {}
        for r in rows:
            name = r.get("name") or ""
            cur = by_name.get(name)
            if cur is None or (r.get("tier") or 0) > (cur.get("tier") or 0):
                by_name[name] = r
        # Highest-level rank per base line.
        by_base: dict[str, SpellRow] = {}
        for r in by_name.values():
            base = self.strip_roman(r.get("name") or "")
            cur = by_base.get(base)
            if cur is None or (r.get("level") or 0) > (cur.get("level") or 0):
                by_base[base] = r
        return sorted(by_base.values(), key=lambda r: r.get("level") or 0, reverse=True)

    def beneficial_buff_tiers(self, base_name: str, max_level: int) -> list[SpellRow]:
        """Every era TIER row (Apprentice → Master) of the HIGHEST rank of
        ``base_name`` at or under ``max_level`` — the raid-buff panel's
        tier dropdown ('Crusade' → Crusade V's Apprentice/Journeyman/
        Adept/Expert/Master rows). One row per tier NAME (highest tier
        number, earliest level variant — the era-populated one), sorted
        ascending by tier."""
        fetched = self._fetchall(
            _SQL["beneficial_tiers_by_base"].format(cols=_SELECT_COLS),
            (base_name, f"{base_name} %", max_level),
        )
        rows = [r for r in (_row_to_dict(f) for f in fetched) if self.strip_roman(r.get("name") or "") == base_name]
        if not rows:
            return []
        best_name = max(rows, key=lambda r: r.get("level") or 0).get("name")
        per_tier: dict[str, SpellRow] = {}
        for r in rows:
            if r.get("name") != best_name:
                continue
            tname = r.get("tier_name") or ""
            cur = per_tier.get(tname)
            if (
                cur is None
                or (r.get("tier") or 0) > (cur.get("tier") or 0)
                or ((r.get("tier") or 0) == (cur.get("tier") or 0) and (r.get("level") or 0) < (cur.get("level") or 0))
            ):
                per_tier[tname] = r
        return sorted(per_tier.values(), key=lambda r: r.get("tier") or 0)

    def upgradeable_crcs(self, crcs: Iterable[int | None]) -> set[int]:
        """Return the subset of ``crcs`` that are upgradeable spells.

        A spell is upgradeable when its line spans more than one tier in the
        catalogue (Apprentice → Grandmaster, …). Single-tier abilities — utility
        casts like Cure, Resurrect, Soothe, Enduring Breath — have one tier and
        are excluded. This is independent of *how* a character acquired the
        spell (spellscroll / classtraining / class), so an upgradeable spell
        granted by the trainer or auto-granted at base tier still counts.

        Empty input (or a missing DB) → empty set.
        """
        ids = [c for c in {*crcs} if c is not None]
        if not ids:
            return set()
        rows = self._fetchall(_SQL["upgradeable_crcs"], (ids,))
        return {r["crc"] for r in rows}

    def find_by_crc(self, crc: int, tier: int | None = None) -> SpellRow | None:
        """Return the spell row for the given CRC and AA rank tier.

        AA nodes reference spells by CRC; multiple rows share a CRC — one per
        rank (tier).  Pass the character's spent tier to get the right values.
        Falls back to the highest available tier if the exact one isn't found.
        Cached per instance (hot path: AA tooltips) — invalidated on upsert.
        """
        key = (crc, tier)
        if key in self._crc_cache:
            return self._crc_cache[key]
        row = None
        if tier is not None:
            row = self._fetchone(_SQL["find_by_crc_and_tier"].format(cols=_SELECT_COLS), (crc, tier))
        if row is None:
            # Fallback: highest available tier
            row = self._fetchone(_SQL["find_by_crc_highest_tier"].format(cols=_SELECT_COLS), (crc,))
        result = _row_to_dict(row) if row else None
        if len(self._crc_cache) >= self._CRC_CACHE_MAX:
            # FIFO eviction (dicts preserve insertion order) — cheap and
            # good enough for a cache that upsert_spells fully clears anyway.
            self._crc_cache.pop(next(iter(self._crc_cache)))
        self._crc_cache[key] = result
        return result

    def find_by_crc_bands(self, crc: int, tier: int) -> list[SpellRow]:
        """All real-level band rows for an AA rank, level ascending
        (bands 70/100/110… — the game interpolates between them for the
        character's level). Uncached; AA sets are small."""
        rows = self._fetchall(_SQL["find_by_crc_tier_bands"].format(cols=_SELECT_COLS), (crc, tier))
        return [_row_to_dict(r) for r in rows]

    def find_by_name(self, name: str) -> list[SpellRow]:
        """Return all spell rows whose name matches (exact, then LIKE). Ordered by level."""
        rows = self._find_exact_then_like(
            _SQL["find_by_name_exact"].format(cols=_SELECT_COLS),
            _SQL["find_by_name_like"].format(cols=_SELECT_COLS),
            name,
        )
        return [_row_to_dict(r) for r in rows]

    def character_upgradeable_spells(self, spell_ids: list[int]) -> list[SpellRow]:
        """The canonical "which upgradeable spells does this character own"
        list, at each line's highest owned tier.

        Single source of truth for both the spells tab and the upgrade-materials
        checker so the two can't drift. Keeps scribed/trained/auto-granted
        spells alike (excluding only AA abilities) and restricts to lines that
        actually have a tier ladder. Do not gate on ``given_by ==
        'spellscroll'``: that drops trainer-granted (``classtraining``) and
        base-tier (``class``) spells.
        """
        spell_db = self.find_by_ids(spell_ids)
        blocklist = self.load_blocklist()
        candidate = [
            r
            for r in spell_db.values()
            if (r.get("level") or 0) > 0
            and r.get("type") in ("spells", "arts")
            and r.get("given_by") != "alternateadvancement"
            and self.strip_roman(r.get("name") or "").lower() not in blocklist
        ]
        upgradeable = self.upgradeable_crcs({r.get("crc") for r in candidate})
        rows = [r for r in candidate if r.get("crc") in upgradeable]
        return self.unique_highest_entries(rows)

    def character_rotation_spells(self, spell_ids: list[int]) -> list[SpellRow]:
        """The rotation simulator's ability universe: every castable spell/
        art the character owns, at each line's highest owned tier.

        Same candidate filter as :meth:`character_upgradeable_spells`
        (level>0, spells/arts, not AA, blocklist) but WITHOUT the
        upgradeable-crcs gate — single-tier abilities (utility casts,
        some temp buffs) must appear in a rotation universe even though
        the spells tab rightly hides them. Kept separate so the spells
        tab / upgrade-checker contract never drifts."""
        spell_db = self.find_by_ids(spell_ids)
        blocklist = self.load_blocklist()
        candidate = [
            r
            for r in spell_db.values()
            if (r.get("level") or 0) > 0
            and r.get("type") in ("spells", "arts")
            and r.get("given_by") != "alternateadvancement"
            and self.strip_roman(r.get("name") or "").lower() not in blocklist
        ]
        return self.unique_highest_entries(candidate)


# The shared default instance — every runtime consumer goes through this.
catalogue = SpellCatalogue()

# Script-facing `_meta` aliases (download_spells.py resume offsets) — bound
# to the shared instance so a re-pointed catalogue.schema carries through.
get_meta = catalogue.get_meta
set_meta = catalogue.set_meta
