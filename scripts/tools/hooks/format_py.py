"""Claude Code PostToolUse hook: format and lint-fix the Python file just edited.

Silent when the file ends up clean. When ruff still has findings it could not
fix, they go to stderr with exit code 2, which hands them straight back to the
model instead of surfacing minutes later in the pre-push run.

Wire it in `.claude/settings.json`:

    "PostToolUse": [{"matcher": "Edit|Write",
      "hooks": [{"type": "command",
                 "command": "uv run --frozen python scripts/tools/hooks/format_py.py"}]}]

Stdin is the hook payload: {"tool_name": ..., "tool_input": {"file_path": ...}, "cwd": ...}.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# Ruff is configured to skip these; formatting them from a hook would fight that.
SKIP_PARTS = frozenset({".venv", "node_modules", "docs"})


def target_python_file(payload: dict, repo: Path = REPO) -> Path | None:
    """The edited file, if it is an existing .py file inside this repo that ruff covers."""
    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw or not str(raw).endswith(".py"):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or repo) / path
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    if SKIP_PARTS.intersection(rel.parts) or not path.is_file():
        return None
    return path


def ruff_command(repo: Path = REPO) -> list[str]:
    """Prefer the venv's ruff (no startup cost); fall back to uv."""
    for candidate in (repo / ".venv" / "Scripts" / "ruff.exe", repo / ".venv" / "bin" / "ruff"):
        if candidate.is_file():
            return [str(candidate)]
    if shutil.which("uv"):
        return ["uv", "run", "--frozen", "ruff"]
    return []


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    path = target_python_file(payload)
    ruff = ruff_command()
    if path is None or not ruff:
        return 0
    run = {"cwd": REPO, "capture_output": True, "encoding": "utf-8", "errors": "replace", "check": False}
    subprocess.run([*ruff, "format", "--quiet", str(path)], **run)
    lint = subprocess.run([*ruff, "check", "--fix", "--quiet", "--output-format", "concise", str(path)], **run)
    if lint.returncode != 0:
        findings = (lint.stdout or lint.stderr).strip().split("\n")
        shown = findings[:20]
        if len(findings) > len(shown):
            shown.append(f"... {len(findings) - len(shown)} more")
        print("ruff could not fix these; fix them now:\n" + "\n".join(shown), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
