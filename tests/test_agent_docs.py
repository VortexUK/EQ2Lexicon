"""CLAUDE.md, .claude/rules and .claude/skills stay small and point at real files.

CLAUDE.md is paid for on every Claude Code session, so regrowth is a cost; a
rule or skill that names a file which no longer exists is misinformation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("check_agent_docs", REPO / "scripts" / "tools" / "check_agent_docs.py")
assert _spec is not None and _spec.loader is not None
docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs)


def test_agent_docs_are_small_and_point_at_real_files():
    problems = docs.check(REPO)
    assert not problems, "\n" + "\n".join(problems)


def test_glob_to_regex_matches_like_claude_code_paths():
    assert docs.glob_to_regex("backend/**").match("backend/server/app.py")
    assert docs.glob_to_regex("backend/**/*.sql").match("backend/eq2db/zones.sql")
    assert docs.glob_to_regex("backend/**/*.sql").match("backend/x.sql")
    assert docs.glob_to_regex("backend/server/census_*.py").match("backend/server/census_health.py")
    assert not docs.glob_to_regex("backend/server/census_*.py").match("backend/server/api/census_x.py")
    assert not docs.glob_to_regex("backend/pg.py").match("backend/pg_migrate.py")


def test_path_tokens_strip_suffixes_and_skip_templates():
    text = (
        "See `backend/server/api/parses/ingest.py:ingest_parse` and `tests/test_pg_migrate.py::test_x`, "
        "run `uv run python scripts/tools/verify.py --all`, add `db/migrations/NNNN_users.sql`, "
        "`.claude/rules/<area>.md`, `data/AAs/icons/{id}.png`, and `pages/guild/Tab.tsx`."
    )
    assert docs.path_tokens(text) == [
        "backend/server/api/parses/ingest.py",
        "tests/test_pg_migrate.py",
        "scripts/tools/verify.py",
    ]


def test_parse_frontmatter_reads_lists_and_scalars():
    meta, body = docs.parse_frontmatter('---\nname: x\npaths:\n  - "a/**"\n  - b/*.py\n---\n\n# Title\n')
    assert meta == {"name": "x", "paths": ["a/**", "b/*.py"]}
    assert body.strip() == "# Title"
    assert docs.parse_frontmatter("# no frontmatter\n") == ({}, "# no frontmatter\n")


def test_check_reports_each_kind_of_problem(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "real.py").write_text("", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("line\n" * 201 + "`backend/real.py` `backend/gone.py`\n", encoding="utf-8")
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "unscoped.md").write_text("# no frontmatter\n", encoding="utf-8")
    (rules / "dead.md").write_text('---\npaths:\n  - "nowhere/**"\n---\n', encoding="utf-8")
    (rules / "ok.md").write_text('---\npaths:\n  - "backend/**"\n---\n`backend/real.py`\n', encoding="utf-8")
    skill = tmp_path / ".claude" / "skills" / "thing"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: other\n---\nbody\n", encoding="utf-8")

    problems = "\n".join(docs.check(tmp_path))

    assert "CLAUDE.md: 202 lines" in problems
    assert "names a path that does not exist: backend/gone.py" in problems
    assert "backend/real.py" not in problems
    assert "unscoped.md: no `paths:` frontmatter" in problems
    assert "dead.md: paths glob matches no file: nowhere/**" in problems
    assert "ok.md" not in problems
    assert "SKILL.md: no `description`" in problems
    assert "name `other` does not match its directory `thing`" in problems
