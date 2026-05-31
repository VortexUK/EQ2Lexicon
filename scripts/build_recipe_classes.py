"""Build the recipe_classes mapping in recipes.db.

A recipe's tradeskill class is NOT in the recipe JSON (recipes only carry a
shared crafting `bench`). Two sources, in priority order:

1. AUTHORITATIVE — dedicated recipe-book items:
   - A book tagged with exactly ONE primary tradeskill class in
     `typeinfo.classes` (e.g. "Advanced Armorer Volume 20" → Armorer) maps
     every recipe in its `recipe_list` to that class. 100% reliable.
   - Secondary tradeskills (Tinkering, Adorning) have empty typeinfo.classes;
     they're identified by the item's top-level `requiredskill.text`
     ("tinkering"/"adorning") → Tinkerer / Adorner.
   - Multi-class "Lore and Legend" books (tagged with all 9 classes at once)
     are IGNORED here — they'd stamp every class onto every recipe (the bug
     this rewrite fixes).
   A recipe in two dedicated single-class books (rare) maps to both.

2. ITEM-TYPE FALLBACK — for recipes that appear ONLY in multi-class books
   (so step 1 never assigned them a class): classify from what the recipe
   crafts. The signature→class map is *learned* from the step-1 ground truth
   (so the rules are validated against real data, not hard-coded lore):
   armour weight (Plate/Chain→Armorer, Leather/Cloth→Tailor), jewellery slot
   →Jeweler, weapon→Weaponsmith/Woodworker, food→Provisioner,
   house item→Carpenter, spell scroll→scholar by the spell's archetype.

Output: rebuilt recipe_classes(recipe_id, class) in recipes.db. The DB is a
local artifact (gitignored) — copy it to the Railway volume after running.

Usage:
    uv run python scripts/build_recipe_classes.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.census.constants import FIGHTERS, MAGES, PRIESTS, SCOUTS  # noqa: E402
from backend.eq2db.items import DB_PATH as ITEMS_DB  # noqa: E402
from backend.eq2db.recipes import DB_PATH as RECIPES_DB  # noqa: E402
from backend.eq2db.recipes import init_db  # noqa: E402

PRIMARY_CLASSES = frozenset(
    {"Armorer", "Weaponsmith", "Tailor", "Carpenter", "Provisioner", "Woodworker", "Sage", "Alchemist", "Jeweler"}
)
SECONDARY_BY_SKILL = {"tinkering": "Tinkerer", "adorning": "Adorner"}
_ARCHETYPES = [("Fighter", FIGHTERS), ("Priest", PRIESTS), ("Scout", SCOUTS), ("Mage", MAGES)]


def _archetype(classes: dict) -> str | None:
    names = {(v.get("displayname") if isinstance(v, dict) else None) or k.capitalize() for k, v in classes.items()}
    for label, members in _ARCHETYPES:
        if names & members:
            return label
    return None


def _signature(item: dict) -> str | None:
    """A class-predictive signature for the item a recipe crafts (or None)."""
    ti = item.get("typeinfo") or {}
    tn = ti.get("name")
    if not tn:
        return None
    if tn == "armor":
        kd = (ti.get("knowledgedesc") or "").strip()
        if kd:
            return f"armor:{kd}"  # "Plate Armor" / "Chain Armor" / "Leather Armor" / "Cloth Armor"
        slots = [s.get("name") for s in (item.get("slot_list") or []) if isinstance(s, dict) and s.get("name")]
        return f"armor:slot:{slots[0]}" if slots else None  # jewellery: Ear / Finger / Wrist / Neck / ...
    if tn == "weapon":
        return f"weapon:{ti.get('wieldstyle') or '?'}"
    if tn == "spellscroll":
        arch = _archetype(ti.get("classes") or {})
        return f"spell:{arch}" if arch else None
    return tn  # food / houseitem / shield / ammo / ...


def _output_item_ids(rc: sqlite3.Connection, recipe_ids: list[int]) -> dict[int, int]:
    """recipe_id → its named-quality output item id (out_elaborate_id)."""
    out: dict[int, int] = {}
    for i in range(0, len(recipe_ids), 900):
        chunk = recipe_ids[i : i + 900]
        q = f"SELECT id, out_elaborate_id FROM recipes WHERE id IN ({','.join('?' * len(chunk))})"
        for rid, oid in rc.execute(q, chunk):
            if oid:
                out[rid] = oid
    return out


def _signatures_for(src: sqlite3.Connection, item_ids: list[int]) -> dict[int, str | None]:
    sig: dict[int, str | None] = {}
    uniq = list(set(item_ids))
    for i in range(0, len(uniq), 900):
        chunk = uniq[i : i + 900]
        q = f"SELECT id, raw_json FROM items WHERE id IN ({','.join('?' * len(chunk))})"
        for iid, raw in src.execute(q, chunk):
            sig[iid] = _signature(json.loads(raw)) if raw else None
    return sig


def build(items_db: str, recipes_db: str) -> None:
    src = sqlite3.connect(f"file:{items_db}?mode=ro", uri=True)

    authoritative: dict[int, set[str]] = defaultdict(set)
    ground_truth: dict[int, str] = {}  # single-class primary recipe → class (for learning)
    book_recipe_ids: set[int] = set()
    single_books = multi_books = secondary_books = 0
    try:
        for (raw,) in src.execute("SELECT raw_json FROM items WHERE typeinfo_name = 'recipescroll'"):
            if not raw:
                continue
            item = json.loads(raw)
            ti = item.get("typeinfo") or {}
            rl = [e["id"] for e in (ti.get("recipe_list") or []) if isinstance(e, dict) and e.get("id") is not None]
            if not rl:
                continue
            book_recipe_ids.update(rl)
            primary = [
                name
                for k, v in (ti.get("classes") or {}).items()
                if (name := (v.get("displayname") if isinstance(v, dict) and v.get("displayname") else k.capitalize()))
                in PRIMARY_CLASSES
            ]
            if len(primary) == 1:
                single_books += 1
                for rid in rl:
                    authoritative[rid].add(primary[0])
                    ground_truth.setdefault(rid, primary[0])
            elif len(primary) > 1:
                multi_books += 1  # Lore-and-Legend etc. — ignored (noise source)
            else:
                secondary = SECONDARY_BY_SKILL.get(((item.get("requiredskill") or {}).get("text") or "").lower())
                if secondary:
                    secondary_books += 1
                    for rid in rl:
                        authoritative[rid].add(secondary)

        # Learn signature → class from the single-class ground truth.
        rc = sqlite3.connect(recipes_db)
        gt_out = _output_item_ids(rc, list(ground_truth))
        fallback_ids = sorted(book_recipe_ids - set(authoritative))
        fb_out = _output_item_ids(rc, fallback_ids)
        rc.close()

        sig_of = _signatures_for(src, list(gt_out.values()) + list(fb_out.values()))
    finally:
        src.close()

    sig_class: dict[str, Counter] = defaultdict(Counter)
    for rid, cls in ground_truth.items():
        sig = sig_of.get(gt_out.get(rid, -1))
        if sig:
            sig_class[sig][cls] += 1
    learned = {sig: dist.most_common(1)[0][0] for sig, dist in sig_class.items()}

    # Resubstitution accuracy of the learned map (transparency).
    correct = total = 0
    for sig, dist in sig_class.items():
        correct += dist.most_common(1)[0][1]
        total += sum(dist.values())

    # Apply item-type fallback to recipes with no authoritative class.
    fallback_assigned = 0
    for rid in fallback_ids:
        sig = sig_of.get(fb_out.get(rid, -1))
        cls = learned.get(sig) if sig else None
        if cls:
            authoritative[rid].add(cls)
            fallback_assigned += 1

    pairs = sorted((rid, cls) for rid, classes in authoritative.items() for cls in classes)

    conn = init_db(Path(recipes_db))
    try:
        conn.execute("DELETE FROM recipe_classes")
        conn.executemany("INSERT OR IGNORE INTO recipe_classes (recipe_id, class) VALUES (?, ?)", pairs)
        conn.commit()
        matched = conn.execute(
            "SELECT COUNT(DISTINCT rc.recipe_id) FROM recipe_classes rc JOIN recipes r ON r.id = rc.recipe_id"
        ).fetchone()[0]
    finally:
        conn.close()

    per_class = Counter(cls for _, cls in pairs)
    distinct = len({rid for rid, _ in pairs})
    multi = sum(1 for _, n in Counter(rid for rid, _ in pairs).items() if n > 1)
    print(f"books: {single_books} single-class, {secondary_books} secondary, {multi_books} multi-class (ignored)")
    print(f"learned signature map accuracy (resubstitution): {correct}/{total} = {correct / total * 100:.1f}%")
    print(f"fallback recipes classified by item type: {fallback_assigned}/{len(fallback_ids)}")
    print(f"distinct recipes mapped: {distinct}  (matched in recipes table: {matched})  in >1 class: {multi}")
    print("per class:")
    for cls, n in per_class.most_common():
        print(f"  {n:6d}  {cls}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build recipe_classes mapping (books-authoritative + item-type fallback).")
    ap.add_argument("--items-db", default=str(ITEMS_DB))
    ap.add_argument("--recipes-db", default=str(RECIPES_DB))
    args = ap.parse_args()
    build(args.items_db, args.recipes_db)


if __name__ == "__main__":
    main()
