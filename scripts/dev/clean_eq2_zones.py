"""
Clean and re-classify the noisy EQ2 wiki zone dump at
``scripts/dev/eq2_zones.json``.

The upstream scraper synthesised a ``tags`` field that's broken: it
mislabels ~76% of zones as ``Instance: Raid`` (real EQ2 has nowhere
near that many raids). The wiki ``categories`` it also captured are
reliable and carry the real classification signal.

Outputs (alongside the source file):

  * ``eq2_zones.cleaned.json`` — UTF-8 (the source is UTF-16). Same
    top-level shape as the input plus a ``quality_report`` summary.
    Each zone gets a new ``classification`` block with:
        - ``types``           list, e.g. ["solo"], ["raid_x4"], ["solo","group"]
        - ``expansion``       {short, name, year, confidence, source}
        - ``is_persistent_instance`` bool
        - ``is_endless_persistent``  bool
        - ``is_tradeskill``          bool
        - ``is_pvp``                  bool
        - ``is_openworld``            bool
        - ``is_instance``             bool
    Original ``tags`` / ``expansion`` are preserved for diffing.

  * ``eq2_zones.report.txt`` — human-readable summary: counts, zones
    that are still ``unknown`` expansion (with their categories so a
    human can fill in an override), and zones whose expansion came
    from the lowest-confidence ``name_keyword`` heuristic for
    spot-checking.

Idempotent. Re-run after editing the source.

Run with:
    .venv/Scripts/python scripts/dev/clean_eq2_zones.py
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_JSON = SCRIPT_DIR / "eq2_zones.json"
OVERRIDES_JSON = SCRIPT_DIR / "eq2_zones.overrides.json"
ALIASES_JSON = SCRIPT_DIR / "eq2_zones.aliases.json"
CLEANED_JSON = SCRIPT_DIR / "eq2_zones.cleaned.json"
REPORT_TXT = SCRIPT_DIR / "eq2_zones.report.txt"


# ---------------------------------------------------------------------------
# Expansion catalogue
# ---------------------------------------------------------------------------
# Authoritative EQ2 expansion list. ``short`` is the canonical abbreviation;
# ``year`` is the calendar year of release. The empty-name keys at the
# bottom are pre-expansion / DLC content that gets folded into the launch
# era for classification purposes.
#
# Pulled from the wiki category counts already in the source (every name
# that appears as an ``X Zones`` or ``X Instances`` category) cross-checked
# against the EQ2 wiki expansions list.

EXPANSIONS = [
    # (short, full name, year)
    ("Vanilla", "Shattered Lands", 2004),  # original release
    ("BC", "Bloodline Chronicles", 2005),  # adventure pack
    ("SS", "Splitpaw Saga", 2005),  # adventure pack
    ("DoF", "Desert of Flames", 2005),
    ("FD", "Fallen Dynasty", 2006),  # adventure pack (Mar 2006, between DoF and KoS)
    ("KoS", "Kingdom of Sky", 2006),
    ("EoF", "Echoes of Faydwer", 2006),
    ("RoK", "Rise of Kunark", 2007),
    ("TSO", "The Shadow Odyssey", 2008),
    ("SF", "Sentinel's Fate", 2010),
    ("DoV", "Destiny of Velious", 2011),
    ("AoD", "Age of Discovery", 2011),  # DLC
    ("CoE", "Chains of Eternity", 2012),
    ("ToV", "Tears of Veeshan", 2013),
    ("AoM", "Altar of Malice", 2014),
    ("ToT", "Terrors of Thalumbra", 2015),
    ("KA", "Kunark Ascending", 2016),
    ("PoP", "Planes of Prophecy", 2017),
    ("CD", "Chaos Descending", 2018),
    ("BoL", "Blood of Luclin", 2019),
    ("RoS", "Reign of Shadows", 2020),
    ("VoV", "Visions of Vetrovia", 2021),
    ("RoR", "Renewal of Ro", 2022),
    ("BoZ", "Ballads of Zimara", 2023),
    ("SoD", "Scars of Destruction", 2024),
    ("RoC", "Rage of Cthurath", 2025),
]

# ---------------------------------------------------------------------------
# Live Update → expansion era mapping
# ---------------------------------------------------------------------------
# LU numbers are EQ2's Game Update sequence. Each LU released during a
# particular expansion's lifecycle. When the wiki tags a zone with an LU
# number, that's its INTRODUCTION update — so the expansion at that LU's
# release date is the zone's true expansion era.
#
# This map was built by cross-referencing:
#   * The EQ2 wiki's Game Updates timeline
#   * The LU correlations our own high-confidence data shows (built via
#     scripts/dev/clean_eq2_zones.py earlier)
#
# Where the two disagree, the wiki timeline wins — the data is
# contaminated by location-prefix misclassifications (e.g. an LU65 zone
# in Antonica gets called "Vanilla" by the location matcher even though
# LU65 is DoV-era).
#
# Format: (min_inclusive_lu, max_inclusive_lu, expansion_short)
# Ordered by LU range — first match wins.

LU_TO_EXPANSION = [
    (1, 12, "Vanilla"),  # Dec 2004 – Sep 2005
    (13, 18, "DoF"),  # DoF launch (Sep 2005) through Feb 2006
    (19, 26, "KoS"),  # KoS launch (Feb 2006) through Nov 2006
    (27, 39, "EoF"),  # EoF launch (Nov 2006) through Nov 2007
    (40, 49, "RoK"),  # RoK launch (Nov 2007) through Nov 2008
    (50, 56, "TSO"),  # TSO launch (Nov 2008) through Feb 2010
    (57, 59, "SF"),  # SF launch (Feb 2010) through Feb 2011
    (60, 65, "DoV"),  # DoV launch (Feb 2011) through Nov 2012
    (66, 68, "CoE"),  # CoE launch (Nov 2012) through Nov 2013
    (69, 75, "ToV"),  # ToV launch (Nov 2013) through Nov 2014
    (76, 82, "AoM"),  # AoM launch (Nov 2014) through Nov 2015
    (83, 95, "ToT"),  # ToT launch (Nov 2015) through Nov 2016
    (96, 100, "KA"),  # KA launch (Nov 2016) through Nov 2017
    (101, 104, "PoP"),  # PoP launch (Nov 2017) through Nov 2018
    (105, 108, "CD"),  # CD launch (Nov 2018) through Dec 2019
    (109, 115, "BoL"),  # BoL launch (Dec 2019) through Dec 2020
    (116, 122, "RoS"),  # RoS launch (Dec 2020) through Dec 2021
    (123, 127, "VoV"),  # VoV launch (Dec 2021) through Dec 2022
    (128, 130, "RoR"),  # RoR launch (Dec 2022) through Dec 2023
    (131, 999, "BoZ"),  # BoZ launch (Dec 2023) onwards — open-ended; revise
    # when SoD / RoC / later LU ranges are confirmed
]


def _lu_to_expansion(lu: int) -> str | None:
    """Return the expansion short code for an LU number, or None if out of range."""
    for lo, hi, short in LU_TO_EXPANSION:
        if lo <= lu <= hi:
            return short
    return None


# ---------------------------------------------------------------------------
# Live-event introductions
# ---------------------------------------------------------------------------
# Annual EQ2 events recur every year, so a "Tinkerfest" instance could
# have been added in any year since the event launched. Without more
# data we attribute each event to its INTRODUCTION expansion. Zones in
# these categories also get the is_live_event flag so a future schema
# can distinguish "expansion zone" from "event zone".

EVENT_CATEGORY_EXPANSION = {
    "Tinkerfest": "SF",  # introduced Aug 2010
    "Bristlebane Day": "DoV",  # introduced Apr 2011
    "Brew Day": "ToV",  # introduced Mar 2013
    "Chronoportal Phenomenon": "ToV",  # introduced Apr 2014 (just before AoM)
    "Darkpaw Rising": "BoZ",  # recent annual event (2023+)
    "Frostfell": "Vanilla",  # Dec 2004 onwards
    "Erollisi Day": "Vanilla",  # Feb 2005 onwards
    "Heroes' Festival": "Vanilla",  # Nov 2005 onwards
    "Nights of the Dead": "Vanilla",  # Oct 2005 onwards
}


# ---------------------------------------------------------------------------
# Date-stamped wiki update categories
# ---------------------------------------------------------------------------
# Some zones are categorised by the wiki maintainers under "Update
# YYYY.MM.DD" rather than an LU number. Parse the date and pick the
# expansion live at that calendar point.
#
# Format: (year, month_inclusive_upper_bound, expansion_short).
# Walked in order — first match wins. An entry "(2014, 11, 'ToV')"
# means "any update on or before November 2014 (but after the prior
# entry's bound) is ToV-era".

UPDATE_DATE_EXPANSION = [
    (2005, 9, "Vanilla"),
    (2006, 2, "DoF"),
    (2006, 11, "KoS"),
    (2007, 11, "EoF"),
    (2008, 11, "RoK"),
    (2010, 2, "TSO"),
    (2011, 2, "SF"),
    (2012, 11, "DoV"),
    (2013, 11, "CoE"),
    (2014, 11, "ToV"),
    (2015, 11, "AoM"),
    (2016, 11, "ToT"),
    (2017, 11, "KA"),
    (2018, 11, "PoP"),
    (2019, 12, "CD"),
    (2020, 12, "BoL"),
    (2021, 12, "RoS"),
    (2022, 12, "VoV"),
    (2023, 12, "RoR"),
    (2024, 12, "BoZ"),
    (2025, 12, "SoD"),
    (2099, 12, "RoC"),  # open-ended
]

_UPDATE_DATE_RE = re.compile(r"^Update\s+(\d{4})\.(\d{2})\.(\d{2})(?:\s|$)")


def _update_date_to_expansion(year: int, month: int) -> str | None:
    """Map a calendar (year, month) to the expansion that was live then."""
    for y, m, short in UPDATE_DATE_EXPANSION:
        if (year, month) <= (y, m):
            return short
    return None


# ---------------------------------------------------------------------------
# Pseudo-zone exclusions
# ---------------------------------------------------------------------------
# Wiki index / list / UI pages that the upstream scraper hoovered up as
# "zones" but aren't actually zones. Set is_pseudo=true on these and
# skip them from the cleaned output so downstream consumers don't see
# garbage rows.

PSEUDO_ZONE_NAMES = {
    "Dungeon Finder",  # game UI element, removed from game
    "Fabled Dungeons and Raids",  # wiki category index page
    "Zones By Level",  # wiki category index page
    "Betrayal Timeline",  # quest line, not a zone
    "Citizenship Timeline",  # quest line, not a zone
}

# Reverse lookups, all keyed by lowercase full name for tolerant matching.
_EXP_BY_NAME = {full.lower(): (short, full, year) for short, full, year in EXPANSIONS}
_EXP_BY_SHORT = {short.upper(): (short, full, year) for short, full, year in EXPANSIONS}


# ---------------------------------------------------------------------------
# Type-classification categories
# ---------------------------------------------------------------------------
# Map a wiki category name to one of our canonical type tokens. A zone
# can carry several categories simultaneously (e.g. it has a solo AND
# group variant), so we accumulate into a set and store the sorted list.

TYPE_CATEGORY_MAP = {
    "Raid x4 Zones": "raid_x4",
    "Raid x3 Zones": "raid_x3",  # 18-player format EQ2 had briefly
    "Raid x2 Zones": "raid_x2",
    "Raid Zones": "raid",  # generic
    "Group Zones": "group",
    "Heroic Zones": "heroic",  # synonymous with group in EQ2 terminology
    "Solo Zones": "solo",
    "Solo-Group Zones": "solo_or_group",
    "Tradeskill Zones": "tradeskill",
    "Public Zones": "openworld_public",
    "Public Raid Zones": "contested_raid",  # open-world raid zone (Karnor/Sebilis style)
    "Public Raid Instances": "raid_x4",  # per-raid x4 instance (modern persistent format)
    "PvP Zones": "pvp",
    "Cities": "city",  # hub zones — no combat type expected
}


# ---------------------------------------------------------------------------
# Location-prefix → expansion mapping (used when no direct expansion
# category is present). Curated by EQ2 geography knowledge — many of
# these are continents/cities that *appeared* in a specific expansion.
# Same prefix always means "this instance attaches to that continent",
# which gives an expansion hint even when the category itself doesn't
# name one.
#
# Marked at "location_prefix" confidence — lower than direct category
# matches, higher than the name-keyword heuristic.

LOCATION_PREFIX_EXPANSION = {
    # Pre-launch / vanilla locations
    "Antonica": "Vanilla",
    "Commonlands": "Vanilla",
    "Nektulos Forest": "Vanilla",
    "Qeynos": "Vanilla",
    "Qeynos Harbor": "Vanilla",
    "South Qeynos": "Vanilla",
    "North Qeynos": "Vanilla",
    "Qeynos Capitol District": "Vanilla",
    "The City of Freeport": "Vanilla",
    "Freeport": "Vanilla",
    "Thundering Steppes": "Vanilla",
    "Enchanted Lands": "Vanilla",
    "Zek": "Vanilla",
    "Feerrott": "Vanilla",
    "Everfrost": "Vanilla",
    "Lavastorm": "Vanilla",
    "Rivervale": "Vanilla",
    "Runnyeye": "Vanilla",
    "Stormhold": "Vanilla",
    "Fallen Gate": "Vanilla",
    "The Ruins of Varsoon": "Vanilla",
    "Cazic-Thule": "Vanilla",
    "Solusek's Eye": "Vanilla",
    "Permafrost": "Vanilla",
    "Sundered Frontier": "Vanilla",
    "Stonebrunt Highlands": "Vanilla",
    # DoF zones
    "Maj'Dul": "DoF",
    "Sinking Sands": "DoF",
    "Pillars of Flame": "DoF",
    "Living Tombs": "DoF",
    "Clefts of Rujark": "DoF",
    "Pillars": "DoF",
    "Silent City": "DoF",
    # EoF
    "Greater Faydark": "EoF",
    "Butcherblock Mountains": "EoF",
    "Loping Plains": "EoF",
    "Steamfont Mountains": "EoF",
    "Kelethin": "EoF",
    "Lesser Faydark": "EoF",
    # RoK
    "Kylong Plains": "RoK",
    "Fens of Nathsar": "RoK",
    "Kunzar Jungle": "RoK",
    "Jarsath Wastes": "RoK",
    "Karnor's Castle": "RoK",
    "Sebilis": "RoK",
    "Veeshan's Peak": "RoK",
    # TSO
    "Moors of Ykesha": "TSO",
    # SF
    "The Sundered Frontier": "SF",
    # DoV / Velious
    "Great Divide": "DoV",
    "Eastern Wastes": "DoV",
    "Coldain": "DoV",
    "Kael Drakkel": "DoV",
    "Skyshrine": "DoV",
    "Western Wastes": "DoV",
    "Velium": "DoV",
    # Thalumbra
    "Thalumbra, the Ever Deep": "ToT",
    "Maldura": "ToT",
    # Chains of Eternity / Tears of Veeshan
    "Obol Plains": "CoE",
    "Withered Lands": "CoE",
    # Chaos Descending — planes
    "Plane of Magic": "CD",
    "Plane of Innovation": "CD",
    "Plane of Disease": "CD",
    # Areas surfaced by the unknown-prefix scan
    "Coliseum of Valor": "DoV",  # Skyshrine PvP arena
    "The Fortress of Drunder": "DoV",  # Skyshrine-era
    "Cobalt Scar": "CoE",
    "Obol Plains": "CoE",
    "Withered Lands": "CoE",
    "Wracklands": "VoV",
    "Phantom Sea": "AoM",
    "Aurelian Coast": "AoM",
    "Tranquil Sea": "AoM",
    "Sandstone Delta": "AoM",
    "Vesspyr Isles": "AoM",
    "Sodden Archipelago": "AoM",
    "Forlorn Gist": "ToT",
    "Shadeweaver's Thicket": "ToT",
    "Obulus Frontier": "ToT",
    "Maldura": "ToT",
    "Mahngavi Wastes": "KA",
    "Myrist, the Great Library": "CD",  # Chaos Descending plane gateway
    "Zimara Breadth": "BoZ",
    "Raj'Dur Plateaus": "ToV",  # Tears of Veeshan
    # Vanilla locations that the wiki also uses with " Instances" suffix
    "The Commonlands": "Vanilla",
    "The Thundering Steppes": "Vanilla",
    "The Feerrott": "Vanilla",
    "The Pillars of Flame": "DoF",
    "The Sinking Sands": "DoF",
    "The Bonemire": "KoS",
    "Zek, the Orcish Wastes": "Vanilla",
    "New Halas": "SF",  # released with SF (Feb 2010)
    "The City of New Halas": "SF",
    "Plane of Sky": "KoS",  # original KoS launch zone
    "Svarni Expanse": "RoR",  # Renewal of Ro era
    "Bar of Brell": "DoV",  # Brewday content was added in DoV era originally
    "The Mystic Lake": "FD",  # Fallen Dynasty adventure pack
}


# ---------------------------------------------------------------------------
# Name-keyword heuristic (lowest confidence). Last resort for zones
# whose categories don't name an expansion. Match against the zone name
# itself — risky because some location names span multiple expansions.
# Stamped with confidence='name_keyword' so the user can spot-check.

NAME_KEYWORD_HINTS = [
    # (substring, expansion-short) — checked in order
    ("Maj'Dul", "DoF"),
    ("Sinking Sands", "DoF"),
    ("Pillars of Flame", "DoF"),
    ("Living Tombs", "DoF"),
    ("Greater Faydark", "EoF"),
    ("Butcherblock", "EoF"),
    ("Steamfont", "EoF"),
    ("Loping Plains", "EoF"),
    ("Kelethin", "EoF"),
    ("Kunzar", "RoK"),
    ("Fens of Nathsar", "RoK"),
    ("Kylong", "RoK"),
    ("Karnor", "RoK"),
    ("Sebilis", "RoK"),
    ("Veeshan's Peak", "RoK"),
    ("Veeshan", "ToV"),  # ToV revisits Veeshan-themed content; weak
    ("Moors of Ykesha", "TSO"),
    ("Skyshrine", "DoV"),
    ("Kael Drakkel", "DoV"),
    ("Great Divide", "DoV"),
    ("Eastern Wastes", "DoV"),
    ("Maldura", "ToT"),
    ("Thalumbra", "ToT"),
    ("Withered Lands", "CoE"),
    ("Obol", "CoE"),
    ("Vesspyr Isles", "AoM"),
    ("Phantom Sea", "AoM"),
    ("Antonica", "Vanilla"),
    ("Commonlands", "Vanilla"),
    ("Nektulos", "Vanilla"),
    ("Qeynos", "Vanilla"),
    ("Freeport", "Vanilla"),
    ("Thundering Steppes", "Vanilla"),
    ("Enchanted Lands", "Vanilla"),
    ("Cazic-Thule", "Vanilla"),
    ("Permafrost", "Vanilla"),
    ("Stormhold", "Vanilla"),
    ("Runnyeye", "Vanilla"),
    ("Splitpaw", "SS"),
    ("Bloodline", "BC"),
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Classification:
    types: list[str] = field(default_factory=list)
    expansion_short: str = "UNK"
    expansion_name: str = "Unknown"
    expansion_year: int | None = None
    expansion_confidence: str = "unknown"
    expansion_source: str = ""  # which category / heuristic produced it
    is_persistent_instance: bool = False
    is_endless_persistent: bool = False
    is_tradeskill: bool = False
    is_pvp: bool = False
    is_openworld: bool = False
    is_instance: bool = False
    is_live_event: bool = False  # recurring annual event content
    event_name: str = ""  # e.g. "Tinkerfest" when is_live_event=true
    is_city: bool = False  # hub zone (Qeynos / Freeport / Kelethin etc.)
    is_contested: bool = False  # open-world raid (Karnor / Sebilis / Temple of Scale)
    is_deprecated: bool = False  # "Removed from game" per wiki — kept for historical logs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zone_categories(zone: dict) -> set[str]:
    """Union of categories across all of a zone's variants."""
    cats: set[str] = set()
    for v in zone.get("variants", []):
        for c in v.get("categories", []):
            cats.add(c)
    return cats


def _classify_types(cats: set[str], original_tags: list[str]) -> list[str]:
    types: set[str] = set()
    for cat, token in TYPE_CATEGORY_MAP.items():
        if cat in cats:
            types.add(token)
    # solo_or_group implies both — expand it so downstream consumers
    # don't have to know about the joint type. Keep the original token
    # too for traceability.
    if "solo_or_group" in types:
        types.update({"solo", "group"})
    # "Public Raid Zones" are contested raids that are ALSO open-world.
    # Add openworld_public so a "give me all openworld zones" query
    # picks them up alongside the contested_raid flag.
    if "contested_raid" in types:
        types.add("openworld_public")
    # Fallback: the upstream tag synthesiser sometimes marked open-world
    # zones with the bare "Openworld" tag but no wiki category to match.
    # If we ended up with no public-style type, use the original tag.
    if "Openworld" in (original_tags or []) and "openworld_public" not in types:
        types.add("openworld_public")
    return sorted(types)


_LU_CAT_RE = re.compile(r"^LU(\d+)(?:\s|$)")


def _extract_lu_numbers(cats: set[str]) -> list[int]:
    """Pull every LU number out of the categories.

    Handles both shapes the wiki uses:
      * Bare       'LU63'
      * Suffixed   'LU63 Instances', 'LU63 Zones'

    Returns LUs sorted ascending — the smallest is the introduction LU
    (later LUs that revamp the zone are also tagged on the wiki).
    """
    lus: set[int] = set()
    for c in cats:
        m = _LU_CAT_RE.match(c)
        if m:
            lus.add(int(m.group(1)))
    return sorted(lus)


def _classify_expansion(
    cats: set[str],
    zone_name: str,
) -> tuple[str, str, str]:
    """
    Return (short, confidence, source_explanation).

    Priority order (later sources are lower confidence):
      1. Direct '<Expansion Name> Zones' / 'Instances' category — the wiki
         explicitly attributes the zone to that expansion.
      2. Bare expansion-name category.
      3. Shattered Lands / DLC adventure-pack categories.
      4. LU number → expansion era lookup. Trumps location prefix
         because LU number reflects WHEN the zone was added, while a
         location prefix only reflects WHERE in Norrath it sits — a
         vanilla location (Antonica, Qeynos) often gets new content
         in later expansions and the location alone would mis-date it.
      5. Location-prefix mapping (city/continent instances) — used when
         no LU is available, which is the case for many original-launch
         zones that have no LU marker.
      6. Name-keyword heuristic.
      7. Unknown.
    """
    # 1. Direct "X Zones" or "X Instances" category
    for cat in cats:
        for suffix in (" Zones", " Instances"):
            if cat.endswith(suffix):
                stem = cat[: -len(suffix)]
                hit = _EXP_BY_NAME.get(stem.lower())
                if hit:
                    return hit[0], "category", f"category '{cat}'"
    # 2. Bare expansion-name category (e.g. "Desert of Flames")
    for cat in cats:
        hit = _EXP_BY_NAME.get(cat.lower())
        if hit:
            return hit[0], "category", f"category '{cat}'"
    # 3. Shattered Lands / DLC packs
    if "Shattered Lands" in cats or "Shattered Lands Zones" in cats or "Shattered Lands Instances" in cats:
        return "Vanilla", "category", "category 'Shattered Lands'"
    if "Bloodline Chronicles" in cats:
        return "BC", "category", "category 'Bloodline Chronicles'"
    if "Splitpaw Saga" in cats:
        return "SS", "category", "category 'Splitpaw Saga'"
    if "Fallen Dynasty" in cats:
        return "FD", "category", "category 'Fallen Dynasty'"
    # 4. LU number → expansion era
    lus = _extract_lu_numbers(cats)
    if lus:
        intro_lu = lus[0]
        short = _lu_to_expansion(intro_lu)
        if short:
            return (
                short,
                "live_update",
                f"introduced in LU{intro_lu:02d}",
            )
    # 4b. Date-stamped wiki update categories ("Update 2015.06.23")
    for cat in cats:
        m = _UPDATE_DATE_RE.match(cat)
        if m:
            y, mo, _d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            short = _update_date_to_expansion(y, mo)
            if short:
                return short, "update_date", f"wiki '{cat}'"
    # 5. Location-prefix mapping — beats the event fallback because a
    #    location tells you WHEN the underlying zone existed, which is
    #    a hard floor on when the event content could have been added.
    #    E.g. "Haunted Mansion" in Loping Plains is NotD content (event),
    #    but Loping Plains itself is EoF, so the earliest the zone
    #    could exist is EoF — not Vanilla just because NotD started in
    #    vanilla-era 2005.
    for cat in cats:
        for suffix in (" Instances", " Zones"):
            if cat.endswith(suffix):
                prefix = cat[: -len(suffix)]
                if prefix in LOCATION_PREFIX_EXPANSION:
                    return (
                        LOCATION_PREFIX_EXPANSION[prefix],
                        "location_prefix",
                        f"prefix '{prefix}' from category '{cat}'",
                    )
    # 6. Recurring annual event categories — last-resort attribution
    #    for event zones that don't have a usable LU or location signal
    #    (e.g. "Portal to the Past: Guk" lives entirely in the
    #    Chronoportal Phenomenon space, no underlying location).
    for event_cat, short in EVENT_CATEGORY_EXPANSION.items():
        if event_cat in cats or f"{event_cat} Instances" in cats or f"{event_cat} Zones" in cats:
            return short, "live_event", f"event category '{event_cat}'"
    # 6. Name-keyword heuristic (lowest confidence)
    lower_name = zone_name.lower()
    for substr, short in NAME_KEYWORD_HINTS:
        if substr.lower() in lower_name:
            return short, "name_keyword", f"name contains '{substr}'"
    return "UNK", "unknown", ""


def _classify(zone: dict) -> Classification:
    cats = _zone_categories(zone)
    c = Classification()
    c.types = _classify_types(cats, zone.get("tags") or [])
    short, conf, src = _classify_expansion(cats, zone["name"])
    if short != "UNK":
        meta = _EXP_BY_SHORT[short.upper()]
        c.expansion_short = meta[0]
        c.expansion_name = meta[1]
        c.expansion_year = meta[2]
    c.expansion_confidence = conf
    c.expansion_source = src
    c.is_persistent_instance = "Persistent Instances" in cats
    c.is_endless_persistent = "Endless Persistent Instances" in cats
    c.is_tradeskill = "tradeskill" in c.types or "Tradeskill" in zone.get("tags", [])
    c.is_pvp = "pvp" in c.types or "Battleground/PvP" in zone.get("tags", [])
    c.is_openworld = "openworld_public" in c.types or "Openworld" in zone.get("tags", [])
    c.is_instance = "Instances" in cats or any(cat.endswith(" Instances") for cat in cats)
    c.is_city = "city" in c.types or "Cities" in cats
    c.is_contested = "contested_raid" in c.types
    c.is_deprecated = "Removed from game" in cats
    # Live-event flag — set when any of the EVENT_CATEGORY_EXPANSION
    # keys appears in the zone's categories. Remember the event name
    # so a future schema can group by event.
    for event_cat in EVENT_CATEGORY_EXPANSION:
        if event_cat in cats or f"{event_cat} Instances" in cats or f"{event_cat} Zones" in cats:
            c.is_live_event = True
            c.event_name = event_cat
            break

    # Event-year floor: an event zone CAN'T have been introduced
    # earlier than the event itself was. If the classifier picked an
    # expansion that predates the event (e.g. location prefix to
    # Steamfont/EoF for a Tinkerfest zone — Tinkerfest started in SF,
    # 2010), bump the attribution forward to the event's introduction.
    # Conversely, if the classifier picked a LATER expansion (the LU
    # says the zone was specifically added in CoE during a Tinkerfest
    # patch), trust that — events accrue zones over years.
    if c.is_live_event and c.event_name in EVENT_CATEGORY_EXPANSION:
        event_short = EVENT_CATEGORY_EXPANSION[c.event_name]
        event_meta = _EXP_BY_SHORT[event_short.upper()]
        event_year = event_meta[2]
        if c.expansion_year is not None and c.expansion_year < event_year:
            c.expansion_short = event_meta[0]
            c.expansion_name = event_meta[1]
            c.expansion_year = event_meta[2]
            prev_src = c.expansion_source
            c.expansion_confidence = "live_event"
            c.expansion_source = f"event '{c.event_name}' floor (overrode earlier {prev_src})"
    return c


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _load_overrides() -> dict:
    """
    Load the manual-overrides file if it exists. Format:

        {
          "Zone Name (exact match)": {
            "expansion": "AoM",          // short code from EXPANSIONS
            "reason": "LU100 (2015) — AoM era"
          },
          ...
        }

    Each entry overrides whatever the rule-based classifier picked
    for that zone. The cleaned record's ``expansion.confidence`` is
    set to ``manual_override`` and ``source`` records the reason
    so the cleanup output stays auditable.

    Returns {} when the file doesn't exist — overrides are optional.
    """
    if not OVERRIDES_JSON.exists():
        return {}
    try:
        text = OVERRIDES_JSON.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            print(f"WARN: {OVERRIDES_JSON.name} is not a JSON object; ignoring.")
            return {}
        # Validate each entry shape and resolve the expansion short code.
        resolved: dict = {}
        for zone_name, entry in data.items():
            # Keys starting with '_' are reserved for in-file
            # documentation (e.g. "_doc": [...]) and skipped silently.
            if zone_name.startswith("_"):
                continue
            if not isinstance(entry, dict):
                print(f"WARN: override for {zone_name!r} is not an object; skipping.")
                continue
            short = entry.get("expansion")
            if not short or short.upper() not in _EXP_BY_SHORT:
                print(
                    f"WARN: override for {zone_name!r} has unknown expansion "
                    f"{short!r}; skipping. Valid codes: "
                    f"{sorted(_EXP_BY_SHORT.keys())}"
                )
                continue
            resolved[zone_name] = {
                "short": short,
                "reason": entry.get("reason", "manual override"),
            }
        return resolved
    except json.JSONDecodeError as exc:
        print(f"WARN: {OVERRIDES_JSON.name} is invalid JSON ({exc}); ignoring.")
        return {}


def _load_aliases() -> dict[str, list[str]]:
    """
    Load the alias-merge file if it exists. Format:

        {
          "Canonical Zone Name": ["Alternate Name 1", "Alternate Name 2", ...],
          ...
        }

    Each duplicate listed under a canonical name will be:
      - Dropped from the cleaned output (its standalone record disappears).
      - Appended to the canonical record's ``aliases`` array.

    Lets us collapse upstream wiki duplicates (e.g. "Fabled Deathtoll"
    vs "The Fabled Deathtoll" — both are real wiki pages, but the same
    in-game zone) without losing the alternate name.

    Returns {} when the file doesn't exist.
    """
    if not ALIASES_JSON.exists():
        return {}
    try:
        text = ALIASES_JSON.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            print(f"WARN: {ALIASES_JSON.name} is not a JSON object; ignoring.")
            return {}
        result: dict[str, list[str]] = {}
        for canonical, dupes in data.items():
            if canonical.startswith("_"):
                continue  # documentation key
            if not isinstance(dupes, list) or not all(isinstance(d, str) for d in dupes):
                print(f"WARN: alias entry for {canonical!r} is not a list of strings; skipping.")
                continue
            result[canonical] = dupes
        return result
    except json.JSONDecodeError as exc:
        print(f"WARN: {ALIASES_JSON.name} is invalid JSON ({exc}); ignoring.")
        return {}


def main() -> None:
    raw = SOURCE_JSON.read_bytes()
    data = json.loads(raw.decode("utf-16"))
    zones_in = data["zones"]
    print(f"Loaded {len(zones_in)} zones from {SOURCE_JSON.name}")

    overrides = _load_overrides()
    if overrides:
        print(f"Loaded {len(overrides)} manual override(s) from {OVERRIDES_JSON.name}")

    aliases = _load_aliases()
    if aliases:
        total_dupes = sum(len(d) for d in aliases.values())
        print(f"Loaded {len(aliases)} canonical name(s) covering {total_dupes} alias(es) from {ALIASES_JSON.name}")

    cleaned_zones: list[dict] = []
    type_counter: Counter[str] = Counter()
    expansion_counter: Counter[str] = Counter()
    confidence_counter: Counter[str] = Counter()
    still_unknown: list[dict] = []
    name_keyword_picks: list[dict] = []
    location_prefix_picks: list[dict] = []
    live_update_picks: list[dict] = []
    override_picks: list[dict] = []
    type_resolutions: Counter[str] = Counter()
    overrides_applied = 0
    overrides_unmatched = set(overrides.keys())
    pseudo_filtered: list[str] = []

    for z in zones_in:
        # Filter out wiki index / list / UI pages that aren't real zones.
        # Recorded in the report so a future schema update knows what
        # got dropped.
        if z["name"] in PSEUDO_ZONE_NAMES:
            pseudo_filtered.append(z["name"])
            continue
        c = _classify(z)

        # Apply manual override if one exists for this zone name.
        # Overrides trump rule-based classification — they're the
        # escape hatch for cases like "Fabled Temple of Cazic-Thule"
        # where the name-keyword heuristic grabbed Vanilla but the
        # zone is actually a much later content update.
        if z["name"] in overrides:
            ov = overrides[z["name"]]
            meta = _EXP_BY_SHORT[ov["short"].upper()]
            c.expansion_short = meta[0]
            c.expansion_name = meta[1]
            c.expansion_year = meta[2]
            c.expansion_confidence = "manual_override"
            c.expansion_source = ov["reason"]
            overrides_applied += 1
            overrides_unmatched.discard(z["name"])
            override_picks.append(
                {
                    "name": z["name"],
                    "expansion": c.expansion_short,
                    "source": c.expansion_source,
                }
            )

        # Diff record: was the original "Instance: Raid" tag wrong?
        orig_tags = set(z.get("tags", []))
        if "Instance: Raid" in orig_tags:
            if "raid_x4" not in c.types and "raid_x2" not in c.types and "raid" not in c.types:
                type_resolutions["Instance:Raid → not a raid"] += 1

        # Build the cleaned record. Keep originals for audit / re-runs.
        cleaned = {
            "name": z["name"],
            "classification": {
                "types": c.types,
                "expansion": {
                    "short": c.expansion_short,
                    "name": c.expansion_name,
                    "year": c.expansion_year,
                    "confidence": c.expansion_confidence,
                    "source": c.expansion_source,
                },
                "is_persistent_instance": c.is_persistent_instance,
                "is_endless_persistent": c.is_endless_persistent,
                "is_tradeskill": c.is_tradeskill,
                "is_pvp": c.is_pvp,
                "is_openworld": c.is_openworld,
                "is_instance": c.is_instance,
                "is_live_event": c.is_live_event,
                "event_name": c.event_name,
                "is_city": c.is_city,
                "is_contested": c.is_contested,
                "is_deprecated": c.is_deprecated,
            },
            # Original fields preserved for audit / future re-classification
            "original_tags": z.get("tags", []),
            "original_expansion": z.get("expansion"),
            "variants": z.get("variants", []),
            "aliases": z.get("aliases", []),
            "source_pages": z.get("source_pages", []),
        }
        cleaned_zones.append(cleaned)

        # Stats / report data
        for t in c.types:
            type_counter[t] += 1
        if not c.types:
            type_counter["(none)"] += 1
        expansion_counter[c.expansion_short] += 1
        confidence_counter[c.expansion_confidence] += 1
        if c.expansion_confidence == "unknown":
            still_unknown.append({"name": z["name"], "categories": sorted(_zone_categories(z))})
        elif c.expansion_confidence == "name_keyword":
            name_keyword_picks.append(
                {
                    "name": z["name"],
                    "expansion": c.expansion_short,
                    "source": c.expansion_source,
                }
            )
        elif c.expansion_confidence == "location_prefix":
            location_prefix_picks.append(
                {
                    "name": z["name"],
                    "expansion": c.expansion_short,
                    "source": c.expansion_source,
                }
            )
        elif c.expansion_confidence == "live_update":
            live_update_picks.append(
                {
                    "name": z["name"],
                    "expansion": c.expansion_short,
                    "source": c.expansion_source,
                }
            )

    # ---------------------------------------------------------------
    # Apply alias merges (drop duplicate records, append their names
    # to the canonical record's aliases list)
    # ---------------------------------------------------------------
    alias_merges: list[dict] = []
    aliases_unmatched_canonical: list[str] = []
    aliases_unmatched_dupes: list[str] = []
    if aliases:
        by_name = {z["name"]: z for z in cleaned_zones}
        to_drop: set[str] = set()
        for canonical, dupe_names in aliases.items():
            if canonical not in by_name:
                aliases_unmatched_canonical.append(canonical)
                continue
            canonical_record = by_name[canonical]
            existing_aliases = list(canonical_record.get("aliases") or [])
            for dupe in dupe_names:
                if dupe not in by_name:
                    aliases_unmatched_dupes.append(dupe)
                    continue
                # Drop the duplicate record; append its name to the
                # canonical's aliases. Also pull in any aliases the
                # duplicate already had so we don't lose information.
                dupe_record = by_name[dupe]
                if dupe not in existing_aliases:
                    existing_aliases.append(dupe)
                for a in dupe_record.get("aliases") or []:
                    if a not in existing_aliases and a != canonical:
                        existing_aliases.append(a)
                to_drop.add(dupe)
                alias_merges.append({"canonical": canonical, "merged": dupe})
            canonical_record["aliases"] = existing_aliases
        if to_drop:
            cleaned_zones = [z for z in cleaned_zones if z["name"] not in to_drop]
        for c in aliases_unmatched_canonical:
            print(f"WARN: alias canonical {c!r} not found in source data")
        for d in aliases_unmatched_dupes:
            print(f"WARN: alias duplicate {d!r} not found in source data")

    # ---------------------------------------------------------------
    # Emit cleaned JSON
    # ---------------------------------------------------------------
    out = {
        "source": data.get("source", ""),
        "cleaned_by": "scripts/dev/clean_eq2_zones.py",
        "count": len(cleaned_zones),
        "quality_report": {
            "type_counts": dict(type_counter.most_common()),
            "expansion_counts": dict(expansion_counter.most_common()),
            "expansion_confidence_counts": dict(confidence_counter.most_common()),
            "type_resolutions": dict(type_resolutions),
        },
        "zones": cleaned_zones,
    }
    CLEANED_JSON.write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {CLEANED_JSON.name} ({CLEANED_JSON.stat().st_size:,} bytes)")

    # ---------------------------------------------------------------
    # Emit human-readable report
    # ---------------------------------------------------------------
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("EQ2 zones cleanup report")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Total zones (after pseudo-filter + alias-merge): {len(cleaned_zones)}")
    if pseudo_filtered:
        lines.append(f"Pseudo-zones filtered out ({len(pseudo_filtered)}): " + ", ".join(sorted(pseudo_filtered)))
    if alias_merges:
        lines.append(f"Alias merges applied ({len(alias_merges)}):")
        for m in alias_merges:
            lines.append(f"  {m['merged']!r}  →  merged into  {m['canonical']!r}")
    lines.append("")
    lines.append("Types (a zone can have multiple):")
    for t, n in type_counter.most_common():
        lines.append(f"  {n:5d}  {t}")
    lines.append("")
    lines.append("Expansion confidence distribution:")
    for c, n in confidence_counter.most_common():
        lines.append(f"  {n:5d}  {c}")
    lines.append("")
    lines.append("Expansion counts (excluding unknown):")
    for e, n in expansion_counter.most_common():
        if e == "UNK":
            continue
        lines.append(f"  {n:5d}  {e}")
    lines.append("")
    lines.append("Type resolutions:")
    for k, v in type_resolutions.most_common():
        lines.append(f"  {v:5d}  {k}")
    lines.append("")

    if override_picks:
        lines.append("-" * 70)
        lines.append(f"MANUAL OVERRIDES applied ({len(override_picks)}) — from eq2_zones.overrides.json")
        lines.append("-" * 70)
        for p in override_picks:
            lines.append(f"  {p['name']:50s} → {p['expansion']}  ({p['source']})")
        lines.append("")

    if overrides_unmatched:
        lines.append("-" * 70)
        lines.append(f"UNMATCHED OVERRIDES ({len(overrides_unmatched)}) — name not in source data")
        lines.append("-" * 70)
        for name in sorted(overrides_unmatched):
            lines.append(f"  {name}")
        lines.append("")
        # Echo to stdout too — these are almost certainly typos in the
        # overrides file the user wants to know about immediately.
        for name in sorted(overrides_unmatched):
            print(f"WARN: override for {name!r} did not match any zone in source data")

    lines.append("-" * 70)
    lines.append(f"NAME-KEYWORD heuristic picks ({len(name_keyword_picks)}) — spot-check these")
    lines.append("-" * 70)
    for p in name_keyword_picks:
        lines.append(f"  {p['name']:50s} → {p['expansion']}  ({p['source']})")
    lines.append("")

    lines.append("-" * 70)
    lines.append(f"LIVE-UPDATE picks ({len(live_update_picks)}) — LU → expansion-era lookup")
    lines.append("-" * 70)
    for p in live_update_picks[:60]:
        lines.append(f"  {p['name']:50s} → {p['expansion']}  ({p['source']})")
    if len(live_update_picks) > 60:
        lines.append(f"  ... and {len(live_update_picks) - 60} more")
    lines.append("")

    lines.append("-" * 70)
    lines.append(f"LOCATION-PREFIX picks ({len(location_prefix_picks)}) — usually safe")
    lines.append("-" * 70)
    for p in location_prefix_picks[:50]:
        lines.append(f"  {p['name']:50s} → {p['expansion']}  ({p['source']})")
    if len(location_prefix_picks) > 50:
        lines.append(f"  ... and {len(location_prefix_picks) - 50} more")
    lines.append("")

    lines.append("-" * 70)
    lines.append(f"STILL UNKNOWN expansion ({len(still_unknown)}) — manual overrides candidate")
    lines.append("-" * 70)
    for u in still_unknown:
        lines.append(f"  {u['name']}")
        if u["categories"]:
            for c in u["categories"]:
                lines.append(f"      cat: {c}")
        lines.append("")

    REPORT_TXT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT_TXT.name} ({REPORT_TXT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
