"""Distill the live class-reference dumps into a compact spell→class map.

Input:  scripts/dev/live_class_refs/<Class>.json  (download_live_class_refs.py)
Output: base spell name (roman numerals stripped, lower-cased) → sorted class
        list. This is the lookup EQ2Parser embeds for combatant class
        detection and ability source tagging (class / raid / item).

Filtering: spells granted by race/tradition/trait/tradeskill channels cross
classes and would pollute detection; class-identifying channels are kept
(scrolls, class grants, training, AAs, warder and focus abilities).

Effect-cast mining: combat logs record the TRIGGERED effect's name, not the
knowledge-book spell — "Holy Intercession V" logs as "Divine Prayer" ("When
any damage is received this spell will cast Divine Prayer on target."). The
effect names live in spells.db effect text; each mined name is attributed to
the classes whose reference spells trigger it.

Usage:
    uv run python scripts/dev/build_spell_classes.py
    uv run python scripts/dev/build_spell_classes.py --out E:/git/EQ2Parser/src/EQ2Parser.Core/Resources/spell_classes.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# Every reference dump directory is merged: live (full-range, live-current
# names) + TLE servers (era-correct names — live renamed spells over the
# years, e.g. TLE "Smite" is live "Divine Smite").
REF_DIRS = sorted(Path(__file__).parent.glob("*_class_refs*"))

CLASS_CHANNELS = {
    "spellscroll",
    "class",
    "classtraining",
    "alternateadvancement",
    "warderspell",
    "focusabilities",
}

_ROMAN_RE = re.compile(r"\s+[IVXLC]+$")

# "When any damage is received this spell will cast Divine Prayer on target."
_EFFECT_CAST_RE = re.compile(r"will cast ([A-Z][A-Za-z' \-]+?) on ")


def strip_roman(name: str) -> str:
    return _ROMAN_RE.sub("", name.strip())


def effect_casts_by_base(spells_db: Path) -> dict[str, set[str]]:
    """spells.db base_name_lower → the effect names that spell line casts."""
    by_base: dict[str, set[str]] = defaultdict(set)
    conn = sqlite3.connect(spells_db)
    try:
        rows = conn.execute("SELECT base_name_lower, effects FROM spells WHERE effects LIKE '%will cast%'")
        for base, effects in rows:
            for m in _EFFECT_CAST_RE.finditer(effects or ""):
                by_base[base].add(m.group(1))
    finally:
        conn.close()
    return by_base


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).parent / "spell_classes.json"))
    ap.add_argument("--spells-db", default=str(Path(__file__).parent.parent.parent / "data" / "spells" / "spells.db"))
    args = ap.parse_args()

    mapping: dict[str, set[str]] = defaultdict(set)
    per_class: dict[str, int] = defaultdict(int)
    class_names: dict[str, set[str]] = defaultdict(set)

    for refs_dir in REF_DIRS:
        for ref in sorted(refs_dir.glob("*.spells.json")):
            cls = ref.name.removesuffix(".spells.json")
            spells = json.loads(ref.read_text(encoding="utf-8"))
            names = set()
            for spell in spells:
                if (spell.get("given_by") or "") not in CLASS_CHANNELS:
                    continue
                name = strip_roman(str(spell.get("name") or ""))
                if len(name) < 2:
                    continue
                names.add(name.lower())
            for name in names:
                mapping[name].add(cls)
            class_names[cls] |= names
            per_class[cls] = max(per_class[cls], len(names))

    # Effect-cast pass: attribute triggered-effect names to the classes whose
    # reference spells cast them. Kept SEPARATE from the scroll layer: granted
    # procs (Aria of Magic's "Precise Note") fire on the RECIPIENT's casts, so
    # effect names are safe for source tagging but poison for class voting —
    # a Conjuror in the bard group logs Precise Note constantly.
    effects: dict[str, set[str]] = defaultdict(set)
    spells_db = Path(args.spells_db)
    if spells_db.exists():
        by_base = effect_casts_by_base(spells_db)
        for cls, names in class_names.items():
            for base in names:
                for effect in by_base.get(base, ()):
                    key = strip_roman(effect).lower()
                    if len(key) < 2:
                        continue
                    effects[key].add(cls)
    else:
        print(f"warning: {spells_db} missing - effect-cast mining skipped")

    out = {
        "spells": {name: sorted(classes) for name, classes in sorted(mapping.items())},
        "effects": {name: sorted(classes) for name, classes in sorted(effects.items())},
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=0, ensure_ascii=False), encoding="utf-8")

    unique = sum(1 for classes in mapping.values() if len(classes) == 1)
    print(
        f"classes: {len(per_class)} | distinct base spells: {len(mapping):,} | unique-to-one-class: {unique:,} ({100 * unique / max(1, len(mapping)):.0f}%) | effect names: {len(effects):,}"
    )
    for cls, count in sorted(per_class.items()):
        print(f"  {cls:<14} {count:>4} base spells")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
