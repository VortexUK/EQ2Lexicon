"""Distill the live class-reference dumps into a compact spell→class map.

Input:  scripts/dev/live_class_refs/<Class>.json  (download_live_class_refs.py)
Output: base spell name (roman numerals stripped, lower-cased) → sorted class
        list. This is the lookup EQ2Parser embeds for combatant class
        detection and ability source tagging (class / raid / item).

Filtering: spells granted by race/tradition/trait/tradeskill channels cross
classes and would pollute detection; class-identifying channels are kept
(scrolls, class grants, training, AAs, warder and focus abilities).

Usage:
    uv run python scripts/dev/build_spell_classes.py
    uv run python scripts/dev/build_spell_classes.py --out E:/git/EQ2Parser/src/EQ2Parser.Core/Resources/spell_classes.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# Every reference dump directory is merged: live (full-range, live-current
# names) + TLE servers (era-correct names — live renamed spells over the
# years, e.g. TLE "Smite" is live "Divine Smite").
REF_DIRS = sorted(Path(__file__).parent.glob("*_class_refs*"))

CLASS_CHANNELS = {
    "spellscroll", "class", "classtraining", "alternateadvancement",
    "warderspell", "focusabilities",
}

_ROMAN_RE = re.compile(r"\s+[IVXLC]+$")


def strip_roman(name: str) -> str:
    return _ROMAN_RE.sub("", name.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).parent / "spell_classes.json"))
    args = ap.parse_args()

    mapping: dict[str, set[str]] = defaultdict(set)
    per_class: dict[str, int] = defaultdict(int)

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
            per_class[cls] = max(per_class[cls], len(names))

    out = {name: sorted(classes) for name, classes in sorted(mapping.items())}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=0, ensure_ascii=False), encoding="utf-8")

    unique = sum(1 for classes in mapping.values() if len(classes) == 1)
    print(f"classes: {len(per_class)} | distinct base spells: {len(mapping):,} | unique-to-one-class: {unique:,} ({100 * unique / max(1, len(mapping)):.0f}%)")
    for cls, count in sorted(per_class.items()):
        print(f"  {cls:<14} {count:>4} base spells")
    print(f"wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
