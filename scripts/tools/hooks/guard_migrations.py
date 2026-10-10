"""Claude Code PreToolUse hook: refuse edits to a migration that is already on origin/main.

The migration ledger (`public.schema_migrations`) is keyed by filename with no
checksum. Editing an applied file therefore changes nothing in production,
while the test suite, which rebuilds its database from scratch, runs the edited
file and passes. This hook turns that silent divergence into an immediate,
explained refusal.

Wire it in `.claude/settings.json`:

    "PreToolUse": [{"matcher": "Edit|Write",
      "hooks": [{"type": "command",
                 "command": "uv run --frozen python scripts/tools/hooks/guard_migrations.py"}]}]

Exit 2 blocks the tool call and shows stderr to the model. It fails open: if
git or the ref is unavailable, the edit goes ahead. Set ALLOW_MIGRATION_EDIT=1
for a deliberate edit (for example a comment-only fix).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = "db/migrations"
_NAME = re.compile(r"^(\d{4})_[\w.-]+\.sql$")
BASE_REF = "origin/main"


def migration_name(payload: dict, repo: Path = REPO) -> str | None:
    """Filename of the migration this tool call targets, or None if it targets something else."""
    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or repo) / path
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    if rel.parent.as_posix() != MIGRATIONS or not _NAME.match(rel.name):
        return None
    return rel.name


def next_number(names: list[str]) -> str:
    """Next free four-digit prefix given the existing migration filenames."""
    used = [int(m.group(1)) for n in names if (m := _NAME.match(n))]
    return f"{max(used, default=0) + 1:04d}"


def on_base_ref(name: str, repo: Path = REPO, ref: str = BASE_REF) -> bool:
    """True when the file exists at `ref`, meaning a deploy has already applied it."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{MIGRATIONS}/{name}"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return res.returncode == 0


def refusal(name: str, existing: list[str]) -> str:
    return (
        f"{MIGRATIONS}/{name} is already on {BASE_REF}, so production has applied it. "
        "The ledger is keyed by filename: an edit here changes nothing in prod while the tests still pass. "
        f"Add a new migration instead (next free number: {next_number(existing)}). "
        "For a deliberate edit, set ALLOW_MIGRATION_EDIT=1."
    )


def main() -> int:
    if os.environ.get("ALLOW_MIGRATION_EDIT") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    name = migration_name(payload)
    if name is None or not on_base_ref(name):
        return 0
    folder = REPO / MIGRATIONS
    existing = [p.name for p in folder.iterdir()] if folder.is_dir() else []
    print(refusal(name, existing), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
