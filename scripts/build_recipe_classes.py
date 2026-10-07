"""Build the recipe_classes mapping in the recipes schema.

    uv run python scripts/build_recipe_classes.py

Rebuilds recipes.recipe_classes from recipe-book items (authoritative) plus a learned
item-type fallback; reads items.items. Rules and run order: docs/runbooks/catalogue-refresh.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.census.constants import FIGHTERS, MAGES, PRIESTS, SCOUTS  # noqa: E402
from backend.eq2db.items import catalogue as items_catalogue  # noqa: E402
from backend.eq2db.recipes import catalogue as recipes_catalogue  # noqa: E402

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


def _output_item_ids(conn: Any, recipe_ids: list[int]) -> dict[int, int]:
    """recipe_id → its named-quality output item id (out_elaborate_id)."""
    if not recipe_ids:
        return {}
    rows = conn.execute("SELECT id, out_elaborate_id FROM recipes WHERE id = ANY(%s)", (recipe_ids,)).fetchall()
    return {row["id"]: row["out_elaborate_id"] for row in rows if row["out_elaborate_id"]}


def _signatures_for(conn: Any, items_tbl: str, item_ids: list[int]) -> dict[int, str | None]:
    uniq = list(set(item_ids))
    if not uniq:
        return {}
    rows = conn.execute(f"SELECT id, raw_json FROM {items_tbl} WHERE id = ANY(%s)", (uniq,)).fetchall()
    return {row["id"]: (_signature(json.loads(row["raw_json"])) if row["raw_json"] else None) for row in rows}


def build() -> None:
    items_tbl = f"{items_catalogue.schema}.items"  # cross-schema reference
    conn = recipes_catalogue.init_db()  # recipes schema; items read schema-qualified

    authoritative: dict[int, set[str]] = defaultdict(set)
    ground_truth: dict[int, str] = {}  # single-class primary recipe → class (for learning)
    book_recipe_ids: set[int] = set()
    single_books = multi_books = secondary_books = 0
    try:
        # ORDER BY id: pin an order here so the
        # first-single-class-book-wins setdefault below stays deterministic.
        books = conn.execute(f"SELECT raw_json FROM {items_tbl} WHERE typeinfo_name = 'recipescroll' ORDER BY id")
        for book in books:
            raw = book["raw_json"]
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
        gt_out = _output_item_ids(conn, list(ground_truth))
        fallback_ids = sorted(book_recipe_ids - set(authoritative))
        fb_out = _output_item_ids(conn, fallback_ids)

        sig_of = _signatures_for(conn, items_tbl, list(gt_out.values()) + list(fb_out.values()))

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

        conn.execute("DELETE FROM recipe_classes")
        if pairs:
            conn.executemany(
                "INSERT INTO recipe_classes (recipe_id, class) VALUES (%s, %s) ON CONFLICT DO NOTHING", pairs
            )
        conn.commit()
        matched = recipes_catalogue.fetchval(
            conn.execute(
                "SELECT COUNT(DISTINCT rc.recipe_id) FROM recipe_classes rc JOIN recipes r ON r.id = rc.recipe_id"
            )
        )
    finally:
        conn.close()

    per_class = Counter(cls for _, cls in pairs)
    distinct = len({rid for rid, _ in pairs})
    multi = sum(1 for _, n in Counter(rid for rid, _ in pairs).items() if n > 1)
    print(f"books: {single_books} single-class, {secondary_books} secondary, {multi_books} multi-class (ignored)")
    accuracy = f"{correct}/{total} = {correct / total * 100:.1f}%" if total else "0/0 (no ground truth)"
    print(f"learned signature map accuracy (resubstitution): {accuracy}")
    print(f"fallback recipes classified by item type: {fallback_assigned}/{len(fallback_ids)}")
    print(f"distinct recipes mapped: {distinct}  (matched in recipes table: {matched})  in >1 class: {multi}")
    print("per class:")
    for cls, n in per_class.most_common():
        print(f"  {n:6d}  {cls}")


def main() -> None:
    argparse.ArgumentParser(
        description="Build recipe_classes mapping (books-authoritative + item-type fallback)."
    ).parse_args()
    build()


if __name__ == "__main__":
    main()
