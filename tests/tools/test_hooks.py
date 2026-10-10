"""The two Claude Code hook scripts: pure decision logic, no git or ruff needed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "tools" / "hooks" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmt = _load("format_py")
guard = _load("guard_migrations")


def _payload(path: Path | str, cwd: Path | None = None) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}, "cwd": str(cwd) if cwd else None}


# ── format_py ────────────────────────────────────────────────────────────────


def test_format_targets_python_files_inside_the_repo(tmp_path):
    (tmp_path / "backend").mkdir()
    target = tmp_path / "backend" / "x.py"
    target.write_text("x = 1\n", encoding="utf-8")

    assert fmt.target_python_file(_payload(target), repo=tmp_path) == target
    assert (
        fmt.target_python_file(_payload("backend/x.py", cwd=tmp_path), repo=tmp_path) == tmp_path / "backend" / "x.py"
    )


def test_format_ignores_everything_else(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "conf.py").write_text("", encoding="utf-8")
    (tmp_path / "a.ts").write_text("", encoding="utf-8")
    outside = tmp_path.parent / "elsewhere.py"

    assert fmt.target_python_file(_payload(tmp_path / "a.ts"), repo=tmp_path) is None
    assert fmt.target_python_file(_payload(tmp_path / "docs" / "conf.py"), repo=tmp_path) is None
    assert fmt.target_python_file(_payload(tmp_path / "missing.py"), repo=tmp_path) is None
    assert fmt.target_python_file(_payload(outside), repo=tmp_path) is None
    assert fmt.target_python_file({"tool_input": {}}, repo=tmp_path) is None


# ── guard_migrations ─────────────────────────────────────────────────────────


def test_guard_recognises_only_numbered_migration_files(tmp_path):
    folder = tmp_path / "db" / "migrations"
    folder.mkdir(parents=True)

    assert guard.migration_name(_payload(folder / "0007_items.sql"), repo=tmp_path) == "0007_items.sql"
    assert guard.migration_name(_payload("db/migrations/0024_users_x.sql", cwd=tmp_path), repo=tmp_path) == (
        "0024_users_x.sql"
    )
    assert guard.migration_name(_payload(folder / "README.md"), repo=tmp_path) is None
    assert guard.migration_name(_payload(folder / "sub" / "0001_x.sql"), repo=tmp_path) is None
    assert guard.migration_name(_payload(tmp_path / "backend" / "0001_x.sql"), repo=tmp_path) is None
    assert guard.migration_name(_payload(tmp_path.parent / "db" / "migrations" / "0001_x.sql"), repo=tmp_path) is None


def test_guard_next_number_follows_the_highest_prefix():
    assert guard.next_number(["0001_users.sql", "0023_users_officer_ranks.sql", "notes.txt"]) == "0024"
    assert guard.next_number([]) == "0001"


def test_guard_refusal_explains_the_ledger_and_the_way_out():
    message = guard.refusal("0005_raids.sql", ["0005_raids.sql", "0006_lockdown.sql"])

    assert "db/migrations/0005_raids.sql" in message
    assert "keyed by filename" in message
    assert "next free number: 0007" in message
    assert "ALLOW_MIGRATION_EDIT=1" in message


def test_guard_fails_open_when_git_cannot_answer(tmp_path):
    assert guard.on_base_ref("0001_users.sql", repo=tmp_path) is False
