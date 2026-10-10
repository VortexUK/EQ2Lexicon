"""Guard the agent-facing docs against regrowth and drift.

CLAUDE.md is loaded into every Claude Code session, so its size is a fixed cost
on every task; rules and skills are loaded on demand and must point at files
that exist. This check fails when any of that stops being true.

Usage:
    uv run --frozen python scripts/tools/check_agent_docs.py
    uv run --frozen python scripts/tools/check_agent_docs.py --sizes
    uv run --frozen pytest tests/test_agent_docs.py -q

Checks:
    CLAUDE.md             at most 200 lines and 8 KB
    .claude/rules/*.md    has `paths:` frontmatter, every glob matches a file, at most 16 KB
    .claude/skills/*/     SKILL.md has a description and at most 500 lines
    all of the above      every repo path named in backticks exists
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

ROOT_DOC = "CLAUDE.md"
ROOT_MAX_LINES = 200
ROOT_MAX_BYTES = 8 * 1024
RULE_MAX_BYTES = 16 * 1024
SKILL_MAX_LINES = 500

_TOP_DIRS = r"backend|frontend|scripts|tests|docs|db|data|ops|\.claude|\.github"
_PATH_TOKEN = re.compile(rf"^(?:{_TOP_DIRS})/[\w./*\-]+$")
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# A token carrying one of these is a template, not a path.
_PLACEHOLDERS = ("<", "{", "NNNN", "YYYY")

# Paths the docs may name that a clean checkout does not contain: gitignored
# local intermediates and machine-local Claude Code settings.
ALLOW_MISSING = frozenset(
    {
        ".claude/settings.json",
        ".claude/settings.local.json",
        "frontend/node_modules",
    }
)


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a path glob (`**`, `*`, `?`) into an anchored regex over posix paths."""
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def path_tokens(text: str) -> list[str]:
    """Repo paths named inside backtick spans, without `:line` / `::test` suffixes."""
    found: list[str] = []
    for span in _CODE_SPAN.findall(text):
        for raw in span.split():
            tok = raw.strip("(),;").split(":")[0].rstrip(".")
            if any(p in tok for p in _PLACEHOLDERS):
                continue
            if _PATH_TOKEN.match(tok) and tok not in found:
                found.append(tok)
    return found


def parse_frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """Minimal YAML frontmatter reader: `key: value` and `key:` + `  - item` lists."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    meta: dict[str, str | list[str]] = {}
    key: str | None = None
    for ln in lines[1:end]:
        item = re.match(r"^\s+-\s+(.*)$", ln)
        if item and key is not None:
            current = meta.get(key)
            if not isinstance(current, list):
                current = []
            current.append(item.group(1).strip().strip("\"'"))
            meta[key] = current
            continue
        pair = re.match(r"^([\w-]+):\s*(.*)$", ln)
        if pair:
            key = pair.group(1)
            meta[key] = pair.group(2).strip().strip("\"'")
    return meta, "\n".join(lines[end + 1 :])


def known_paths(repo: Path) -> set[str]:
    """Tracked plus untracked-not-ignored files and their directories, as posix paths.

    Git is the source so the answer matches a clean checkout; outside a git
    repo (the unit tests) the directory is walked instead.
    """
    files: list[str] = []
    try:
        top = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == repo.resolve():
            for args in (["ls-files"], ["ls-files", "--others", "--exclude-standard"]):
                res = subprocess.run(
                    ["git", "-C", str(repo), *args],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=True,
                )
                files.extend(ln for ln in res.stdout.split("\n") if ln)
    except (OSError, subprocess.CalledProcessError):
        files = []
    if not files:
        files = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file()]
    known: set[str] = set()
    for f in files:
        known.add(f)
        parts = f.split("/")
        for n in range(1, len(parts)):
            known.add("/".join(parts[:n]))
    return known


def _exists(tok: str, known: set[str]) -> bool:
    if tok in ALLOW_MISSING:
        return True
    if "*" in tok:
        rx = glob_to_regex(tok)
        return any(rx.match(k) for k in known)
    return tok.rstrip("/") in known


def _line_count(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def doc_files(repo: Path) -> dict[str, list[Path]]:
    rules_dir = repo / ".claude" / "rules"
    skills_dir = repo / ".claude" / "skills"
    return {
        "root": [repo / ROOT_DOC],
        "rules": sorted(rules_dir.rglob("*.md")) if rules_dir.is_dir() else [],
        "skills": sorted(skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else [],
        "skill_extras": sorted(p for p in skills_dir.glob("*/**/*.md") if p.name != "SKILL.md")
        if skills_dir.is_dir()
        else [],
    }


def check(repo: Path = REPO) -> list[str]:
    """Return one human-readable line per problem; empty means the docs are healthy."""
    problems: list[str] = []
    known = known_paths(repo)
    docs = doc_files(repo)

    def rel(p: Path) -> str:
        return p.relative_to(repo).as_posix()

    def check_paths(p: Path, text: str) -> None:
        for tok in path_tokens(text):
            if not _exists(tok, known):
                problems.append(f"{rel(p)}: names a path that does not exist: {tok}")

    root = docs["root"][0]
    if not root.is_file():
        return [f"{ROOT_DOC}: missing"]
    text = root.read_text(encoding="utf-8")
    lines, size = _line_count(text), len(text.encode("utf-8"))
    if lines > ROOT_MAX_LINES:
        problems.append(
            f"{ROOT_DOC}: {lines} lines, limit {ROOT_MAX_LINES}. Move detail to a rule, skill or docstring."
        )
    if size > ROOT_MAX_BYTES:
        problems.append(f"{ROOT_DOC}: {size} bytes, limit {ROOT_MAX_BYTES}. Move detail to a rule, skill or docstring.")
    check_paths(root, text)

    for p in docs["rules"]:
        text = p.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        globs = meta.get("paths")
        if isinstance(globs, str):
            globs = [g.strip() for g in globs.split(",") if g.strip()]
        if not globs:
            problems.append(f"{rel(p)}: no `paths:` frontmatter, so it would load into every session")
        for g in globs or []:
            rx = glob_to_regex(g)
            if not any(rx.match(k) for k in known):
                problems.append(f"{rel(p)}: paths glob matches no file: {g}")
        size = len(text.encode("utf-8"))
        if size > RULE_MAX_BYTES:
            problems.append(f"{rel(p)}: {size} bytes, limit {RULE_MAX_BYTES}. Split it or move detail to docs/.")
        check_paths(p, text)

    for p in docs["skills"]:
        text = p.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if not meta.get("description"):
            problems.append(f"{rel(p)}: no `description` in frontmatter")
        name = meta.get("name")
        if name and name != p.parent.name:
            problems.append(f"{rel(p)}: name `{name}` does not match its directory `{p.parent.name}`")
        lines = _line_count(text)
        if lines > SKILL_MAX_LINES:
            problems.append(
                f"{rel(p)}: {lines} lines, limit {SKILL_MAX_LINES}. Move reference material to a sibling file."
            )
        check_paths(p, text)

    for p in docs["skill_extras"]:
        check_paths(p, p.read_text(encoding="utf-8"))

    return problems


def sizes(repo: Path = REPO) -> list[str]:
    out: list[str] = []
    for group, files in doc_files(repo).items():
        for p in files:
            if p.is_file():
                text = p.read_text(encoding="utf-8")
                out.append(
                    f"{len(text.encode('utf-8')):7d} B {_line_count(text):5d} L  {group:12s} {p.relative_to(repo).as_posix()}"
                )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", action="store_true", help="print the size of every checked file")
    args = ap.parse_args(argv)
    if args.sizes:
        print("\n".join(sizes()))
    problems = check()
    for line in problems:
        print(line)
    print(f"{'FAIL' if problems else 'PASS'}  agent docs  ({len(problems)} problem{'s' if len(problems) != 1 else ''})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
