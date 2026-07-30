"""Download one max-level reference character per class from LIVE (Thurgadin).

Live characters at cap carry their full class kit across every level, so
their spell lists are the complete per-class reference the spell→class
mapping needs (TLE characters only know era-capped subsets). The FULL
character JSON is stored per class — local only, gitignored — because the
rest of the document (AAs, equipment, achievements) may be useful later.

Usage:
    uv run python scripts/dev/download_live_class_refs.py
    uv run python scripts/dev/download_live_class_refs.py --level 135 --world Thurgadin

Output: scripts/dev/live_class_refs/<Class>.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.server.config import SERVICE_ID  # noqa: E402

BASE = f"https://census.daybreakgames.com/s:{SERVICE_ID}/json/get/eq2/character/"
DEFAULT_OUT = Path(__file__).parent / "live_class_refs"
OUT_DIR = DEFAULT_OUT  # reassigned from --out-dir in main()
SLEEP_S = 1.5


def adventure_classes() -> list[tuple[int, str]]:
    conn = sqlite3.connect(Path(__file__).parent.parent.parent / "data" / "classes" / "classes.db")
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT icon_id, name FROM classes WHERE icon_id < 100 ORDER BY icon_id")]
    finally:
        conn.close()


def find_candidate(client: httpx.Client, world: str, classid: int, level: int) -> dict | None:
    """A max-level character of this class; falls back to 'highest 120+'."""
    for params in (
        {"type.level": str(level)},
        {"type.level": f"]{max(1, level - 20)}"},  # near-cap fallback
    ):
        r = client.get(BASE, params={
            "locationdata.world": world,
            "type.classid": str(classid),
            "c:show": "id,name,type,spell_list",
            "c:limit": "5",
            **params,
        })
        r.raise_for_status()
        rows = r.json().get("character_list", [])
        if rows:
            # Prefer the candidate with the largest spell list (best coverage).
            return max(rows, key=lambda c: len(c.get("spell_list") or []))
        time.sleep(SLEEP_S)
    return None


def fetch_resolved_spells() -> None:
    """The full documents carry spell_list as bare ids; the mapping needs the
    resolved entries (name/given_by/...). One resolve query per stored ref."""
    with httpx.Client(timeout=120) as client:
        for ref in sorted(OUT_DIR.glob("*.json")):
            if ref.name.endswith(".spells.json"):
                continue
            doc = json.loads(ref.read_text(encoding="utf-8"))
            char_id = doc.get("id")
            if char_id is None:
                continue
            r = client.get(BASE, params={
                "id": str(char_id),
                "c:show": "id,name,type,spell_list",
                "c:resolve": "spells(name,given_by,level,type,crc,tier_name)",
                "c:limit": "1",
            })
            r.raise_for_status()
            rows = r.json().get("character_list", [])
            spells = (rows[0].get("spell_list") or []) if rows else []
            out = ref.with_suffix("").with_suffix("")  # strip .json
            out = ref.parent / f"{ref.stem}.spells.json"
            out.write_text(json.dumps(spells, indent=0, ensure_ascii=False), encoding="utf-8")
            print(f"ok {ref.stem:<14} resolved spells {len(spells):>4} -> {out.name}")
            time.sleep(SLEEP_S)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world", default="Thurgadin", help="live server to sample (default Thurgadin)")
    ap.add_argument("--level", type=int, default=135, help="target max level (default 135)")
    ap.add_argument("--only", default=None, help="comma-separated class names to (re)fetch")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT), help="reference dump directory")
    ap.add_argument("--spells-only", action="store_true",
                    help="fetch resolved spell lists (<Class>.spells.json) for already-downloaded refs")
    args = ap.parse_args()
    global OUT_DIR
    OUT_DIR = Path(args.out_dir)
    only = {c.strip().lower() for c in args.only.split(",")} if args.only else None

    if args.spells_only:
        fetch_resolved_spells()
        return

    OUT_DIR.mkdir(exist_ok=True)
    ok, missing = 0, []
    with httpx.Client(timeout=60) as client:
        for classid, name in adventure_classes():
            if only is not None and name.lower() not in only:
                continue
            candidate = find_candidate(client, args.world, classid, args.level)
            time.sleep(SLEEP_S)
            if candidate is None:
                print(f"!! {name:<14} no character found on {args.world}")
                missing.append(name)
                continue

            # Full document — no c:show, everything census has.
            r = client.get(BASE, params={"id": str(candidate["id"]), "c:limit": "1"})
            r.raise_for_status()
            rows = r.json().get("character_list", [])
            if not rows:
                print(f"!! {name:<14} full fetch returned nothing (id {candidate['id']})")
                missing.append(name)
                continue
            full = rows[0]
            out = OUT_DIR / f"{name}.json"
            out.write_text(json.dumps(full, indent=1), encoding="utf-8")
            spells = len(full.get("spell_list") or [])
            char_name = (full.get("name") or {}).get("first", "?")
            char_level = (full.get("type") or {}).get("level", "?")
            print(f"ok {name:<14} {char_name:<16} level {char_level}  spells {spells:>4}  -> {out.name}")
            ok += 1
            time.sleep(SLEEP_S)

    print(f"\n{ok} classes saved to {OUT_DIR}")
    if missing:
        print(f"missing: {', '.join(missing)}")


if __name__ == "__main__":
    main()
