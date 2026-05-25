"""
PoC scraper: fetch raid-zone wikitext from EQ2i (Fandom) via the
MediaWiki API, parse out zone metadata + per-section markdown +
linked named-mob pages, and emit a structured JSON artifact for human
review.

This is the PoC — runs against 2-3 hand-picked zones by default. After
you've eyeballed the JSON and approved the shape, the next phase will
extend it to the full 60-zone in-scope list (Vanilla through RoK
raids) and load the results into ``data/raids/raids.db`` via the
helpers in ``census/raids_db.py``.

Usage:
    .venv/Scripts/python scripts/dev/scrape_eq2i_raids.py
    .venv/Scripts/python scripts/dev/scrape_eq2i_raids.py --zone "Veeshan's Peak"

Raw API responses get cached under scripts/dev/.eq2i_cache/<title>.json
to be polite to Fandom and re-runnable without re-hitting the network.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import mwparserfromhell as mwp  # noqa: E402

from census import zones_db  # noqa: E402
from census.wikitext_md import convert  # noqa: E402

# Expansion in-scope set for the "raids for TLE" feature. Vanilla
# through Rise of Kunark. Live-expansion content is deferred.
IN_SCOPE_EXPANSIONS: tuple[str, ...] = ("Vanilla", "DoF", "KoS", "EoF", "RoK")
# Type tokens that denote a raid zone (the boss list is what we want)
RAID_TYPE_TOKENS: frozenset[str] = frozenset(
    {
        "raid_x4",
        "raid_x3",
        "raid_x2",
        "raid",
        "contested_raid",
    }
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_URL = "https://eq2.fandom.com/api.php"
USER_AGENT = "EQ2Lexicon-RaidScraper/0.1 (https://eq2lexicon.com; contact ben.mcelroy.uk@gmail.com)"
CACHE_DIR = Path(__file__).resolve().parent / ".eq2i_cache"
OUT_DIR = Path(__file__).resolve().parent
POLITE_DELAY_SEC = 1.0  # between live API calls (cached calls don't sleep)

# Default sample for the PoC — one zone per major in-scope expansion
# so the output exercises the full pipeline across different page
# shapes. Override with --zone.
DEFAULT_SAMPLE_ZONES = [
    "Mistmoore's Inner Sanctum",  # EoF, well-documented
    "Trakanon's Lair",  # RoK, agent flagged as good
    "Veeshan's Peak",  # RoK, signature zone
]

# Section names we extract as named markdown fields. Everything else
# gets dropped from per-section extraction (still visible in the body
# blob if we choose to keep it). Case-insensitive match on the heading
# text (level 2 only).
INTERESTING_SECTIONS = {
    "background": "background_md",
    "overview": "overview_md",
    "strategy": "strategy_md",
    "tactics": "strategy_md",  # alternate name for the same thing
    "walkthrough": "walkthrough_md",
    "notes": "notes_md",
    "access": "access_md",
    "named mobs": "named_mobs_md",
    "named encounters": "named_mobs_md",
}


# ---------------------------------------------------------------------------
# Cached HTTP
# ---------------------------------------------------------------------------


def _cache_path(title: str) -> Path:
    safe = title.replace("/", "_").replace(":", "_").replace("?", "_")
    return CACHE_DIR / f"{safe}.json"


def fetch_wikitext(title: str, *, force: bool = False) -> str | None:
    """Fetch the wikitext for a wiki page, caching the raw API JSON.

    Returns the raw wikitext string, or None when the API reports the
    page doesn't exist / returns an error.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(title)
    if cache.exists() and not force:
        payload = json.loads(cache.read_text(encoding="utf-8"))
    else:
        time.sleep(POLITE_DELAY_SEC)
        params = {
            "action": "parse",
            "page": title,
            "prop": "wikitext",
            "format": "json",
            "redirects": "1",
        }
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"  ERR fetching {title!r}: {exc}", file=sys.stderr)
            return None
        cache.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  fetched + cached: {title}")
    if "error" in payload:
        print(f"  WARN API error for {title!r}: {payload['error'].get('info')}")
        return None
    parse = payload.get("parse") or {}
    wikitext_obj = parse.get("wikitext") or {}
    if isinstance(wikitext_obj, dict):
        return wikitext_obj.get("*")
    return None


# ---------------------------------------------------------------------------
# Wikitext extraction
# ---------------------------------------------------------------------------


def extract_zone_metadata(wikitext: str) -> dict:
    """Pull structured fields from the IZoneInformation template (or
    a fallback for OZoneInformation). Returns whatever fields are
    present — all keys are optional."""
    code = mwp.parse(wikitext)
    for tpl in code.filter_templates():
        name = str(tpl.name).strip().lower().replace(" ", "")
        if name not in ("izoneinformation", "ozoneinformation", "zoneinformation"):
            continue
        out: dict[str, str] = {}
        for param in tpl.params:
            key = str(param.name).strip().lower()
            value = str(param.value).strip()
            if value:
                out[key] = value
        return out
    return {}


def extract_sections(wikitext: str) -> dict[str, str]:
    """Split wikitext on level-2 headings; return a dict
    {section_name_lowered: markdown_body}. Body includes any nested
    level-3+ headings as part of that section's content.

    The zone-information template is filtered out before conversion so
    the structured fields don't appear as junk text in the body.
    """
    code = mwp.parse(wikitext)
    # Strip the zone-info template — handled separately
    for tpl in list(code.filter_templates(recursive=False)):
        name = str(tpl.name).strip().lower().replace(" ", "")
        if name in ("izoneinformation", "ozoneinformation", "zoneinformation"):
            try:
                code.remove(tpl)
            except ValueError:
                pass

    out: dict[str, str] = {}
    sections = code.get_sections(levels=[2], include_lead=False, flat=True)
    for sec in sections:
        # First node should be the heading; use its title as the key.
        heading = next(
            (n for n in sec.nodes if hasattr(n, "level") and n.level == 2),
            None,
        )
        if heading is None:
            continue
        # Heading text. Some pages use bold inside headings
        # (=='''Strategy'''==), which our converter would surface as
        # the literal "'''Strategy'''" key. Convert to markdown then
        # strip markdown bold markers + whitespace so the key is just
        # the plain section name.
        raw_title = convert(str(heading.title)).strip()
        title = raw_title.lstrip("*").rstrip("*").strip("* \t")
        md = convert(str(sec))
        # Drop the heading line itself — caller wants just the body.
        # convert() preserves the ## heading; strip the first line if it's it.
        lines = md.split("\n", 1)
        body = lines[1].lstrip() if len(lines) > 1 else ""
        out[title.lower()] = body.strip()
    return out


_TRASH_PREFIXES = ("a ", "an ", "the ")


def _resolve_disambig(
    wikitext: str,
    page_title: str,
    zone_name: str,
) -> tuple[str, str] | None:
    """Follow a Disambig page to its zone-specific variant.

    Strategy:
      1. Walk the disambig page's wikilinks.
      2. Prefer a link whose title contains the zone name in parens
         (e.g. ``Trakanon (Trakanon's Lair)``) or the zone name without
         the trailing apostrophe-s.
      3. Fall back: prefer any link with ``(Instanced)`` in the name
         (a common EQ2i convention for raid-instance variants).
      4. Last resort: first non-File/Category wikilink.
    """
    code = mwp.parse(wikitext)
    candidates: list[str] = []
    for link in code.filter_wikilinks():
        target = str(link.title).strip()
        if not target or target.lower().startswith(("file:", "image:", "category:")):
            continue
        candidates.append(target)
    if not candidates:
        return None

    zone_lower = zone_name.lower()
    # Strip trailing apostrophe-s for matching: "Trakanon's Lair" → "trakanon"
    zone_token = zone_lower.split("'s ")[0]

    def score(c: str) -> int:
        lower = c.lower()
        # Prefer EQ2i mob-page disambiguator suffixes FIRST — they
        # win over zone-name matches because a disambig page often
        # lists the zone link too, which would just be filtered out
        # as kind=zone if we followed it. The mob page is what we
        # actually want.
        if "(monster)" in lower or "(named)" in lower or "(instanced)" in lower:
            return 10
        if "(npc)" in lower:
            return 8
        # Zone-name-based fallback — useful when the disambig is
        # structured "Mob (Zone Name)" rather than "Mob (Monster)".
        if f"({zone_token}" in lower or f"({zone_lower}" in lower:
            return 6
        if zone_lower in lower:
            return 3  # last-resort; usually points at the zone page itself
        return 0

    best = max(candidates, key=score)
    if score(best) == 0:
        # Nothing matched the zone — disambig is too ambiguous
        return None
    resolved_wt = fetch_wikitext(best)
    if resolved_wt is None:
        return None
    return (best, resolved_wt)


def _detect_page_kind(wikitext: str) -> str:
    """Quick page-type classifier based on the first template in the
    wikitext. Returns one of: 'disambig', 'zone', 'quest', 'mob',
    'unknown'. Used to filter the mob-candidate list down to actual
    raid-boss pages without recursively walking everything.
    """
    code = mwp.parse(wikitext)
    for tpl in code.filter_templates(recursive=False):
        name = str(tpl.name).strip().lower().replace(" ", "")
        if name == "disambig":
            return "disambig"
        if name in ("zonebox", "izoneinformation", "ozoneinformation", "zoneinformation"):
            return "zone"
        if name in ("quest", "questinformation"):
            return "quest"
        if name in ("namedinformation", "npcinformation", "monsterinformation"):
            return "mob"
        # EQ2i item infoboxes — many raid pages wikilink to loot
        # (Amulet of Desecration, Armor of Anuk, etc.). They look
        # like proper nouns so our boss-name heuristic doesn't filter
        # them, but the item template is a clean signal.
        if name in (
            "iteminformation",
            "iteminfo",
            "item",
            "lootitem",
            "lore_item",
            "loreitem",
            "spellinformation",
            "spellinfo",
            "abilityinformation",
            "abilityinfo",
            "recipe",
            "recipeinformation",
        ):
            return "item"
        # First template wins — break after the first known token.
        # If we hit an unknown template first, keep looking through
        # the rest before giving up.
        # (No break — just keep iterating.)
    return "unknown"


def _looks_like_boss_name(target: str) -> bool:
    """Filter heuristic for 'this wikilink target is probably a real
    raid boss', not trash / lore / zone reference.

    EQ2 trash mob names are common nouns — '[[a sanctum chaperone]]',
    '[[an undead servant]]'. Real raid bosses are proper nouns —
    'Trakanon', 'Mayong Mistmoore (Instanced)', 'Drayek'. Reject
    indefinite-article prefixes; require an uppercase first char.
    Drops Faction/POI/Update references via substring check.
    """
    t = (target or "").strip()
    if not t:
        return False
    lower = t.lower()
    if lower.startswith(_TRASH_PREFIXES):
        return False
    if not t[0].isalpha() or not t[0].isupper():
        return False
    # Common non-mob page suffixes that the wiki uses with proper-noun
    # names (lore characters, sub-areas, factions, updates).
    for skip in ("(Faction)", "(POI)", "Update:", "Category:"):
        if skip in t:
            return False
    return True


def extract_linked_mob_pages(wikitext: str) -> list[str]:
    """Find wikilinks that look like links to per-mob raid-boss pages.

    Combines two signals:
      1. ``{{Monster|key|display}}`` template references — strong but
         hits trash mobs too.
      2. Bare wikilinks anywhere in the page — noisy (catches lore,
         factions, zones) but catches bosses that aren't in templates.

    Both signals get filtered through ``_looks_like_boss_name`` to
    reject obvious non-bosses (indefinite-article trash, factions,
    POIs). Result is sorted-unique so output is stable.
    """
    code = mwp.parse(wikitext)
    candidates: set[str] = set()

    # Monster templates
    for tpl in code.filter_templates():
        if str(tpl.name).strip().lower() not in ("monster", "npc"):
            continue
        if tpl.has(1):
            key = str(tpl.get(1).value).strip()
            if key:
                candidates.add(key)

    # Bare wikilinks
    for link in code.filter_wikilinks():
        target = str(link.title).strip()
        if not target or target.lower().startswith(("file:", "image:", "category:")):
            continue
        candidates.add(target)

    return sorted(t for t in candidates if _looks_like_boss_name(t))


# ---------------------------------------------------------------------------
# Per-zone orchestration
# ---------------------------------------------------------------------------


def scrape_zone(title: str) -> dict | None:
    """Fetch + parse one raid zone. Returns a structured dict or None
    if the page couldn't be fetched."""
    print(f"\n--- {title} ---")
    wikitext = fetch_wikitext(title)
    if wikitext is None:
        return None

    metadata = extract_zone_metadata(wikitext)
    sections = extract_sections(wikitext)
    mob_links = extract_linked_mob_pages(wikitext)

    # Pull the conventional sections into top-level keys
    named_sections: dict[str, str] = {}
    for wiki_name, key in INTERESTING_SECTIONS.items():
        if wiki_name in sections:
            named_sections[key] = sections[wiki_name]
            del sections[wiki_name]

    # Fetch + parse per-mob pages
    encounters: list[dict] = []
    skipped_pages: list[dict] = []  # for visibility in the JSON
    for mob_title in mob_links:
        mob_wt = fetch_wikitext(mob_title)
        if mob_wt is None:
            skipped_pages.append({"mob_name": mob_title, "reason": "fetch failed"})
            continue
        # Drop pages that are clearly not raid bosses based on their
        # top template. Zone pages use ZoneBox/IZoneInformation. Quest
        # pages are quest references. Disambig pages are special — we
        # try to follow them to the zone-specific variant first.
        kind = _detect_page_kind(mob_wt)
        if kind == "disambig":
            resolved = _resolve_disambig(mob_wt, mob_title, title)
            if resolved is None:
                skipped_pages.append(
                    {
                        "mob_name": mob_title,
                        "reason": "disambig page; no zone-specific variant found",
                    }
                )
                continue
            resolved_title, resolved_wt = resolved
            # Continue with the resolved page instead. Re-classify in
            # case the disambig pointed somewhere weird.
            mob_title, mob_wt = resolved_title, resolved_wt
            kind = _detect_page_kind(mob_wt)
        if kind in ("zone", "quest", "item"):
            skipped_pages.append({"mob_name": mob_title, "reason": f"not a mob page ({kind})"})
            continue
        mob_sections = extract_sections(mob_wt)
        # Strategy lives under various headings depending on the page.
        # Prefer 'strategy', then 'tactics', then 'fight strategy',
        # then 'encounter strategy'. Fall back to 'overview'.
        strategy_md = ""
        for cand in ("strategy", "tactics", "fight strategy", "encounter strategy", "overview", "the fight", "notes"):
            if cand in mob_sections and mob_sections[cand]:
                strategy_md = mob_sections[cand]
                break
        encounters.append(
            {
                "mob_name": mob_title,
                "position": len(encounters) + 1,
                "strategy_md": strategy_md or None,
                "wiki_url": f"https://eq2.fandom.com/wiki/{urllib.parse.quote(mob_title.replace(' ', '_'))}",
            }
        )

    return {
        "zone_name": title,
        "wiki_url": f"https://eq2.fandom.com/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
        "metadata": metadata,
        **named_sections,
        "other_sections": sections,  # leftover sections (loot, history, etc.)
        "encounters": encounters,
        "skipped_pages": skipped_pages,  # candidates that turned out non-mob
        "scraped_at": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _list_in_scope_raid_zones() -> list[str]:
    """Query zones.db for every raid zone in the in-scope expansions.

    Skips deprecated/pseudo-zones; includes both instanced raids and
    open-world contested-raid zones.
    """
    names: list[str] = []
    for exp in IN_SCOPE_EXPANSIONS:
        for z in zones_db.list_by_expansion(exp):
            if z["is_deprecated"]:
                continue
            if any(t in RAID_TYPE_TOKENS for t in z["types"]):
                names.append(z["name"])
    return sorted(set(names))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--zone",
        action="append",
        help="Override the default sample with one or more zone names "
        '(repeatable). E.g. --zone "Veeshan\'s Peak" --zone Sebilis',
    )
    parser.add_argument(
        "--all-raids",
        action="store_true",
        help="Read in-scope raid zone list from zones.db "
        f"(expansions: {', '.join(IN_SCOPE_EXPANSIONS)}) and scrape all of them. "
        "Output goes to scripts/dev/eq2_raid_data.json by default. "
        "Takes ~5-15 minutes due to polite 1s API delay.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write the structured output JSON. "
        "Defaults to eq2_raid_data.json for --all-raids, eq2i_raids.sample.json otherwise.",
    )
    args = parser.parse_args()

    if args.all_raids:
        zones = _list_in_scope_raid_zones()
        default_out = OUT_DIR / "eq2_raid_data.json"
    else:
        zones = args.zone or DEFAULT_SAMPLE_ZONES
        default_out = OUT_DIR / "eq2i_raids.sample.json"
    out_path = args.out or default_out

    print(f"Scraping {len(zones)} zone(s) from EQ2i:")
    for z in zones[:5]:
        print(f"  - {z}")
    if len(zones) > 5:
        print(f"  ... and {len(zones) - 5} more")

    results: list[dict] = []
    for z in zones:
        result = scrape_zone(z)
        if result is not None:
            results.append(result)

    payload = {
        "source": "EQ2i (eq2.fandom.com) via MediaWiki API",
        "scraped_at": int(time.time()),
        "zones": results,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")

    # Quick summary
    print()
    for z in results:
        n_enc = len(z["encounters"])
        bg = "Y" if z.get("background_md") else "-"
        ov = "Y" if z.get("overview_md") else "-"
        print(f"  {z['zone_name']:40s}  background={bg}  overview={ov}  encounters={n_enc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
