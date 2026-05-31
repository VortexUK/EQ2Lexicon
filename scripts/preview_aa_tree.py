#!/usr/bin/env python3
"""
Render an AA tree to preview_aa_tree.png without needing Discord.
Tree type is detected automatically from the data.

Usage:
    python scripts/preview_aa_tree.py 3     # tree id 3 (Cleric class tree)
    python scripts/preview_aa_tree.py 25    # tree id 25 (Templar subclass tree)
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.image.aa_tree import render_tree

if __name__ == "__main__":
    tree_id = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    img, tree_type = render_tree(tree_id)
    print(f"Detected type: {tree_type}")

    out = Path("preview_aa_tree.png")
    img.save(out)
    print(f"Saved: {out.resolve()}")

    if sys.platform == "win32":
        os.startfile(str(out))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(out)], check=False)
    else:
        subprocess.run(["xdg-open", str(out)], check=False)
