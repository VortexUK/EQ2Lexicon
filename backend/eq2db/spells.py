"""
Local SQLite mirror of the Census /spell/ collection.

Each row is one spell entry — a specific tier of a specific spell (e.g.
"Divine Strike III Adept" is a separate row from "Divine Strike III Master").
The `crc` field groups all tier-variants of the same base spell together.

167 k rows total; download once with scripts/download_spells.py and refresh
whenever spells are patched (rare — typically expansion launches only).

Character spell-check looks up spell IDs in this table so the per-character
Census call can return bare IDs instead of resolved spell objects, making it
faster and removing the c:resolve overhead.

All behaviour lives on :class:`SpellCatalogue` (the eq2db data-interface
convention — see AACatalogue): DB lookups are instance methods; the pure
spell-domain helpers (strip_roman, unique_highest_entries, load_blocklist,
spell_to_row) are staticmethods on the same class so consumers import ONE
name — the shared ``catalogue`` instance. Module level holds only types
(SpellRow, Blocklist), constants (DB_PATH), and the instance.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from backend.census._coerce import coerce_float as _float
from backend.census._coerce import coerce_int as _int
from backend.census._coerce import coerce_str_or_none as _str
from backend.db_helpers import like_escape, resolve_db_path
from backend.eq2db._catalogue import BaseCatalogue
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


DB_PATH: Path = resolve_db_path("DB_SPELLS_PATH", "spells", "spells.db")

# Roman-numeral suffix pattern (I–XX) used for base_name computation.
# Matches a space-separated Roman numeral at the end of a spell name.
_ROMAN_RE = re.compile(
    r"\s+(?:XX|XIX|XVIII|XVII|XVI|XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)$",
    re.IGNORECASE,
)

_BLOCKLIST_PATH: Path = Path(__file__).resolve().parent.parent.parent / "data" / "spells" / "blocklist.json"

# Schema (CREATE TABLE / INDEX) lives in spells.sql; init_db runs each block.

# SQL queries live in spells.sql; loaded once at import. Composition for
# the dynamic IN-list (find_by_ids) and the shared column-list fragment
# is done in the methods below via f-string formatting.
_SQL = load_sql(__file__)

# The column list lives in spells.sql under the `select_cols` block — fragment
# spliced into every find_* query at format-time.
_SELECT_COLS = _SQL["select_cols"]

# `_meta` get/set is shared across every eq2db module — see backend/eq2db/_meta.py.
from backend.eq2db._meta import get_meta, set_meta  # noqa: E402,F401


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


def _row_to_dict(row: sqlite3.Row) -> SpellRow:
    return dict(row)  # type: ignore[return-value]


class SpellCatalogue(BaseCatalogue):
    """Read (and build) access to one spells.db file, with per-instance caching.

    The eq2db data-interface convention (see AACatalogue): the DB path and
    caches live on the instance; the shared module-level ``catalogue`` is the
    runtime entry point, and tests construct ``SpellCatalogue(tmp_db)``. The
    pure spell-domain helpers are staticmethods here so the class is the one
    interface for everything spell-shaped.

    Only the CRC lookup is cached (the hot path — AA tooltips resolve spell
    effects by crc per hover); id/name lookups take dynamic inputs and stay
    uncached. ``upsert_spells`` clears the crc cache (BE-236: spell data
    changed; stale CRC lookups would lie).
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)
        self._crc_cache: dict[tuple[int, int | None], SpellRow | None] = {}

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_SQL["schema_spells"])
        conn.executescript(_SQL["indexes_spells"])
        # Idempotent migration: add effects column if missing (pre-existing DBs)
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(spells)").fetchall()}
        if "effects" not in existing_cols:
            conn.execute(_SQL["migrate_add_effects_column"])

    def clear_caches(self) -> None:
        """Reset the per-instance caches — used by tests and upsert_spells."""
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
            _log.warning("[spells_db] Failed to load blocklist: %s", exc)
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
                "[spells_db] effect_list for spell %s has unexpected shape %s — returning empty",
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

    def upsert_spells(self, spells: list[dict], conn: sqlite3.Connection) -> int:
        """Upsert a batch of raw Census spell dicts. Returns the number inserted/replaced."""
        rows = [self.spell_to_row(s) for s in spells if s.get("id") is not None]
        conn.executemany(_SQL["upsert"], rows)
        conn.commit()
        self.clear_caches()  # BE-236: spell data changed; stale CRC lookups would lie
        return len(rows)

    def spell_count(self, conn: sqlite3.Connection) -> int:
        return conn.execute(_SQL["count"]).fetchone()[0]

    # ── Lookups (async-friendly via asyncio.to_thread) ───────────────────────

    def find_by_id(self, spell_id: int) -> SpellRow | None:
        """Return a spell row dict for the given ID, or None."""
        row = self._fetchone(_SQL["find_by_id"].format(cols=_SELECT_COLS), (spell_id,))
        return _row_to_dict(row) if row else None

    def find_by_ids(self, spell_ids: list[int]) -> dict[int, SpellRow]:
        """Return {spell_id: row_dict} for all matching IDs. Missing IDs are omitted."""
        if not spell_ids:
            return {}
        placeholders = ",".join("?" * len(spell_ids))
        rows = self._fetchall(
            _SQL["find_by_ids"].format(cols=_SELECT_COLS, placeholders=placeholders),
            spell_ids,
        )
        return {row["id"]: _row_to_dict(row) for row in rows}

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
        placeholders = ",".join("?" * len(ids))
        rows = self._fetchall(_SQL["upgradeable_crcs"].format(placeholders=placeholders), ids)
        return {r[0] for r in rows}

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
        self._crc_cache[key] = result
        return result

    def find_by_name(self, name: str) -> list[SpellRow]:
        """Return all spell rows whose name matches (exact, then LIKE). Ordered by level."""
        rows = self._fetchall(_SQL["find_by_name_exact"].format(cols=_SELECT_COLS), (name.lower(),))
        if not rows:
            # LIKE fallback — escape user wildcards (BE-006).
            rows = self._fetchall(
                _SQL["find_by_name_like"].format(cols=_SELECT_COLS),
                (f"%{like_escape(name.lower())}%",),
            )
        return [_row_to_dict(r) for r in rows]

    def character_upgradeable_spells(self, spell_ids: list[int]) -> list[SpellRow]:
        """The canonical "which upgradeable spells does this character own"
        list, at each line's highest owned tier.

        Single source of truth for both the spells tab and the upgrade-materials
        checker so the two can't drift. Keeps scribed/trained/auto-granted
        spells alike (excluding only AA abilities) and restricts to lines that
        actually have a tier ladder — the ``given_by == 'spellscroll'`` gate
        used to drop trainer-granted (``classtraining``) and base-tier
        (``class``) spells, so a trained Apprentice never showed up. See
        get_character_spells for the history.
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


# The shared default instance — every runtime consumer goes through this.
catalogue = SpellCatalogue()
