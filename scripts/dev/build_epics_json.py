"""Build data/quests/epics.json — per-class Epic Weapon quest chains with
census quest CRCs.

Two sources, joined by quest name:

  1. EQ2i's Epic_Weapons overview page (MediaWiki API, wikitext) — the
     per-class fabled + mythical weapon names and their quest chains
     (the ==Quests== section covers every class in one page).
  2. Census ``quest`` datatype — resolves each quest name to its crc
     (``id`` == ``crc``), level, and category.

Completion detection on the website side: a character has their epic when
the chain's FINAL quest crc appears in ``character_misc.completed_quest_list``
(entries are ``{crc, completion_date}``; character_misc is keyed by the
character id).

Usage:
    python scripts/dev/build_epics_json.py            # fetch wiki + census
    python scripts/dev/build_epics_json.py --offline  # reuse cached wikitext

Re-run safe: output is deterministic given the same wiki + census data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://eq2.fandom.com/api.php"
USER_AGENT = "EQ2Lexicon-EpicsBuilder/0.1 (https://eq2lexicon.com; contact tovortexuk@gmail.com)"
CENSUS_BASE = "https://census.daybreakgames.com"

REPO = Path(__file__).resolve().parents[2]
OUT_PATH = REPO / "data" / "quests" / "epics.json"
CACHE_PATH = Path(__file__).parent / ".epics_wikitext_cache.json"

# Known wiki-side quest-name typos / case drift → the census-side truth.
# Applied when the as-written name resolves to nothing.
NAME_FIXES = {
    "Defender ofn the Faith": "Defender of the Faith",
    "For A Better Tomorrow": "For a Better Tomorrow",
    "The Path to Understanding": "The Path to Understanding...",
    "A Chance for Redemption": "A Chance For Redemption",
}

# Wiki chain steps that are NOT census quests (e.g. reading a book item).
# Kept in the chain for documentation but expected to carry crc: null.
KNOWN_NON_QUESTS = {
    "Tome: The Maiden of Masks",
}

# Overview-page chains where the LAST listed quest is not the completion
# signal. Ranger: "For The Swamp!" is a level-75 prerequisite side-quest the
# overview appends after the actual mythical quest — the class timeline
# page's mythical section lists only "The Untapped Power!".
COMPLETION_OVERRIDES = {
    ("Ranger", "mythical"): "The Untapped Power!",
}

# Class archetype map (matches backend/census/constants.py groupings).
ARCHETYPES = {
    "Berserker": "Fighter",
    "Guardian": "Fighter",
    "Bruiser": "Fighter",
    "Monk": "Fighter",
    "Paladin": "Fighter",
    "Shadowknight": "Fighter",
    "Warlock": "Mage",
    "Wizard": "Mage",
    "Coercer": "Mage",
    "Illusionist": "Mage",
    "Conjuror": "Mage",
    "Necromancer": "Mage",
    "Fury": "Priest",
    "Warden": "Priest",
    "Inquisitor": "Priest",
    "Templar": "Priest",
    "Defiler": "Priest",
    "Mystic": "Priest",
    "Dirge": "Scout",
    "Troubador": "Scout",
    "Assassin": "Scout",
    "Ranger": "Scout",
    "Brigand": "Scout",
    "Swashbuckler": "Scout",
    "Beastlord": "Scout",  # Animalist — not on RoK-era TLE servers
}


def fetch_wikitext(offline: bool) -> str:
    if offline and CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))["wikitext"]
    url = f"{API_URL}?action=parse&page=Epic_Weapons&prop=wikitext&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    wikitext = data["parse"]["wikitext"]["*"]
    CACHE_PATH.write_text(json.dumps({"wikitext": wikitext}), encoding="utf-8")
    return wikitext


def parse_quest_section(wikitext: str) -> dict[str, dict]:
    """Parse the ==Quests== section: per class, the fabled weapon + quest
    chain and the mythical weapon + quest chain (in wiki order)."""
    section = wikitext.split("==Quests==", 1)[1]
    classes: dict[str, dict] = {}
    current_class: str | None = None
    current_weapon: str | None = None
    slot: str | None = None  # "fabled" | "mythical"

    for line in section.splitlines():
        line = line.strip()
        m = re.match(r"^====\s*(.+?)\s*====$", line)
        if m:
            current_class = m.group(1)
            classes[current_class] = {
                "fabled": {"weapon": None, "quests": []},
                "mythical": {"weapon": None, "quests": []},
            }
            slot = None
            continue
        if current_class is None:
            continue
        # Weapon heading: '''[[Page|Display]]''' (optionally two, for Beastlord)
        m = re.match(r"^'''\[\[(?:[^]|]*\|)?([^]|]+)\]\]'''", line)
        if m:
            current_weapon = m.group(1)
            # First weapon heading per class = fabled, second = mythical.
            slot = "fabled" if classes[current_class]["fabled"]["weapon"] is None else "mythical"
            classes[current_class][slot]["weapon"] = current_weapon
            continue
        # Quest line: #{{Quest|Page|Display?}} (level)
        m = re.match(r"^#\{\{Quest\|([^}]+)\}\}", line)
        if m and slot is not None:
            args = m.group(1).split("|")
            name = args[-1].strip()  # display name when the template has one
            classes[current_class][slot]["quests"].append(name)
    return classes


def census_quest_lookup(service_id: str, name: str) -> list[dict]:
    q = urllib.parse.quote(name, safe="")
    url = f"{CENSUS_BASE}/s:{service_id}/json/get/eq2/quest/?name={q}&c:show=id,crc,name,category,level&c:limit=10"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Census intermittently times out — retry with backoff rather than
    # losing a 130-lookup run to one flake.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            return data.get("quest_list", [])
        except (TimeoutError, OSError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return []


def resolve_quest(service_id: str, name: str) -> tuple[dict | None, str]:
    """Resolve a wiki quest name against census. Returns (row, resolved_name).
    Tries the name as written, then any known typo fix."""
    for candidate in (name, NAME_FIXES.get(name)):
        if not candidate:
            continue
        rows = census_quest_lookup(service_id, candidate)
        exact = [r for r in rows if r.get("name") == candidate]
        if exact:
            return exact[0], candidate
        if rows:
            return rows[0], rows[0].get("name", candidate)
        time.sleep(0.1)
    return None, name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="reuse the cached wikitext")
    ap.add_argument("--service-id", default=None, help="census service id (default: CENSUS_SERVICE_ID from .env)")
    args = ap.parse_args()

    service_id = args.service_id
    if not service_id:
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("CENSUS_SERVICE_ID="):
                service_id = line.strip().split("=", 1)[1]
                break
    if not service_id:
        print("no census service id (pass --service-id or set CENSUS_SERVICE_ID in .env)")
        return 1

    wikitext = fetch_wikitext(args.offline)
    classes = parse_quest_section(wikitext)
    print(f"wiki: {len(classes)} classes parsed")

    out: dict = {
        "_source": "EQ2i (eq2.fandom.com) Epic_Weapons + census eq2/quest",
        "_notes": (
            "A character has the fabled/mythical epic when the chain's final quest crc "
            "appears in census character_misc.completed_quest_list (entries carry crc + "
            "completion_date; character_misc is keyed by character id). Wiki quest levels "
            "reflect current live, not RoK-era, values."
        ),
        "classes": {},
    }
    unresolved: list[str] = []

    for cls, data in classes.items():
        entry: dict = {"archetype": ARCHETYPES.get(cls, "Unknown")}
        for slot in ("fabled", "mythical"):
            weapon = data[slot]["weapon"]
            chain = []
            for qname in data[slot]["quests"]:
                if qname in KNOWN_NON_QUESTS:
                    chain.append({"name": qname, "crc": None, "note": "not a census quest (book/item step)"})
                    continue
                row, resolved = resolve_quest(service_id, qname)
                time.sleep(0.15)  # polite pacing
                if row is None:
                    unresolved.append(f"{cls}/{slot}: {qname}")
                    chain.append({"name": qname, "crc": None})
                    continue
                chain.append(
                    {
                        "name": resolved,
                        "crc": row["crc"],
                        "level": row.get("level"),
                        "category": row.get("category"),
                    }
                )
            # The completion signal: finishing the LAST quest in the chain,
            # unless a documented override names a different one.
            completion = chain[-1] if chain else None
            override = COMPLETION_OVERRIDES.get((cls, slot))
            if override:
                completion = next((q for q in chain if q["name"] == override), completion)
            entry[slot] = {
                "weapon": weapon,
                "quests": chain,
                "completion_quest": completion,
            }
        out["classes"][cls] = entry
        f = entry["fabled"]["completion_quest"]
        m = entry["mythical"]["completion_quest"]
        print(f"  {cls:14} fabled={f and f['crc']}  mythical={m and m['crc']}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT_PATH} ({len(out['classes'])} classes)")
    if unresolved:
        print("\nUNRESOLVED quest names (need NAME_FIXES entries or wiki correction):")
        for u in unresolved:
            print("  -", u)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
