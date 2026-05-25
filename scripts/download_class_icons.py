"""Download the 26 EQ2 class icons from EQ2wire into data/classes/icons/.

Source: https://u.eq2wire.com/images/class_medium/{icon_id}.png
Saved as data/classes/icons/{icon_id}.png. These are small static assets and
ARE committed (unlike the gitignored classes.db).

Usage:
    uv run python scripts/download_class_icons.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from census.classes_db import CLASS_SEED  # noqa: E402

_BASE = "https://u.eq2wire.com/images/class_medium/{id}.png"
_DEST = Path(__file__).resolve().parent.parent / "data" / "classes" / "icons"


def main() -> None:
    _DEST.mkdir(parents=True, exist_ok=True)
    for c in CLASS_SEED:
        url = _BASE.format(id=c.icon_id)
        out = _DEST / f"{c.icon_id}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "EQ2Lexicon/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = resp.read()
        out.write_bytes(data)
        print(f"{c.name:14s} id={c.icon_id:<3} {len(data):>6} bytes -> {out.name}")
    print(f"\nDownloaded {len(CLASS_SEED)} icons to {_DEST}")


if __name__ == "__main__":
    main()
