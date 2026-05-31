"""Download the 26 EQ2 adventure-class icons from EQ2wire into data/classes/icons/.

Source: https://u.eq2wire.com/images/class_medium/{icon_id}.png
Saved as data/classes/icons/{icon_id}.png. These are small static assets and
ARE committed alongside the source-of-truth classes.db. Only adventure-class
icons are fetched — crafter rows use placeholder icon_ids (100+) for which
EQ2wire has no public assets.

Usage:
    uv run python scripts/download_class_icons.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.eq2db.classes import list_all  # noqa: E402

_BASE = "https://u.eq2wire.com/images/class_medium/{id}.png"
_DEST = Path(__file__).resolve().parent.parent / "data" / "classes" / "icons"


def main() -> None:
    _DEST.mkdir(parents=True, exist_ok=True)
    # Adventure classes only (Crafter rows have placeholder icon_ids 100+).
    adv = [r for r in list_all() if r["archetype"] != "Crafter"]
    for r in adv:
        url = _BASE.format(id=r["icon_id"])
        out = _DEST / f"{r['icon_id']}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "EQ2Lexicon/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = resp.read()
        out.write_bytes(data)
        print(f"{r['name']:14s} id={r['icon_id']:<3} {len(data):>6} bytes -> {out.name}")
    print(f"\nDownloaded {len(adv)} icons to {_DEST}")


if __name__ == "__main__":
    main()
