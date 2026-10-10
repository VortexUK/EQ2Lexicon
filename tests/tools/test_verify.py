"""scripts/tools/verify.py: change-set -> stage planning, output trimming, exit codes.

Pure: tmp_path trees and a fake ``subprocess.run``; nothing here runs git, ruff, pytest or node.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    key = f"_tools_{name}"
    spec = importlib.util.spec_from_file_location(key, REPO / "scripts" / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


verify = _load("verify")

_TESTS = (
    "tests/test_pg_migrate.py",
    "tests/test_sql_blocks.py",
    "tests/test_sql_loader.py",
    "tests/test_agent_docs.py",
    "tests/server/test_db_facade.py",
    "tests/server/test_guild.py",
    "tests/server/test_guild_history.py",
    "tests/server/test_users.py",
    "tests/server/test_shared.py",
    "tests/server/parses/test_parses_list.py",
    "tests/tools/test_verify.py",
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for rel in (*_TESTS, "backend/server/api/guild.py", "backend/orphan.py", "frontend/src/App.tsx"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


def _tool(name: str) -> str | None:
    return f"/fake/bin/{name}"


def _no_tool(_name: str) -> str | None:
    return None


# ── change set ────────────────────────────────────────────────────────────────


def test_normalise_paths_dedupes_and_uses_forward_slashes():
    assert verify.normalise_paths(["backend\\a.py", "./backend/a.py", " ", "x.md"]) == ["backend/a.py", "x.md"]


def test_git_changed_unions_diff_untracked_and_base(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs["cwd"] == tmp_path
        out = {"diff HEAD": "backend/a.py\n", "ls-files": "new.py\n", "diff main...HEAD": "backend/a.py\nb.sql\n"}
        key = "ls-files" if "ls-files" in cmd else f"diff {cmd[-1]}"
        return subprocess.CompletedProcess(cmd, 0, stdout=out[key], stderr="")

    monkeypatch.setattr(verify.subprocess, "run", fake_run)

    assert verify.git_changed(tmp_path) == ["backend/a.py", "new.py"]
    assert len(calls) == 2
    assert verify.git_changed(tmp_path, base="main") == ["backend/a.py", "new.py", "b.sql"]
    assert calls[-1][-2:] == ["--name-only", "main...HEAD"]


def test_git_changed_reports_a_git_failure(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: bad revision 'nope'\n")

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="bad revision"):
        verify.git_changed(tmp_path, base="nope")


# ── test mapping ──────────────────────────────────────────────────────────────


def test_backend_file_maps_to_every_test_named_after_it(tree):
    tests, unmapped = verify.map_tests(["backend/server/api/guild.py"], tree)
    assert tests == ["tests/server/test_guild.py", "tests/server/test_guild_history.py"]
    assert unmapped == []


def test_changed_test_runs_itself_and_a_deleted_one_is_dropped(tree):
    tests, _ = verify.map_tests(["tests/server/test_users.py", "tests/server/test_gone.py"], tree)
    assert tests == ["tests/server/test_users.py"]


def test_migration_and_sql_sidecar_rules(tree):
    assert verify.map_tests(["db/migrations/0099_x.sql"], tree)[0] == ["tests/test_pg_migrate.py"]
    tests, unmapped = verify.map_tests(["backend/server/api/guild.sql"], tree)
    assert tests == [
        "tests/server/test_guild.py",
        "tests/server/test_guild_history.py",
        "tests/test_sql_blocks.py",
        "tests/test_sql_loader.py",
    ]
    assert unmapped == []


def test_db_package_adds_the_facade_test(tree):
    tests, unmapped = verify.map_tests(["backend/server/db/users.py", "backend/server/db/claims.sql"], tree)
    assert "tests/server/test_db_facade.py" in tests
    assert "tests/server/test_users.py" in tests
    assert unmapped == []


def test_agent_docs_test_only_when_it_exists(tree):
    assert verify.map_tests(["CLAUDE.md"], tree)[0] == ["tests/test_agent_docs.py"]
    assert verify.map_tests([".claude/rules/x.md"], tree)[0] == ["tests/test_agent_docs.py"]
    (tree / "tests/test_agent_docs.py").unlink()
    assert verify.map_tests(["CLAUDE.md"], tree) == ([], [])


def test_unmapped_backend_file_is_reported(tree):
    tests, unmapped = verify.map_tests(["backend/orphan.py", "README.md"], tree)
    assert tests == []
    assert unmapped == ["backend/orphan.py"]


def test_package_init_and_underscore_modules_use_their_natural_name(tree):
    assert verify.map_tests(["backend/server/api/parses/__init__.py"], tree)[0] == [
        "tests/server/parses/test_parses_list.py"
    ]
    assert verify.map_tests(["backend/server/api/act/_shared.py"], tree)[0] == ["tests/server/test_shared.py"]


def test_scripts_map_to_their_tests_without_an_unmapped_notice(tree):
    assert verify.map_tests(["scripts/tools/verify.py"], tree) == (["tests/tools/test_verify.py"], [])
    assert verify.map_tests(["scripts/download_items.py"], tree) == ([], [])


def test_sub_conftest_runs_its_directory_and_shared_helpers_are_reported(tree):
    tests, unmapped = verify.map_tests(["tests/server/conftest.py", "tests/fixtures/pg.py"], tree)
    assert tests == ["tests/server"]
    assert unmapped == ["tests/fixtures/pg.py"]


# ── planning ──────────────────────────────────────────────────────────────────


def test_python_plan_runs_ruff_on_changed_files_and_pytest_on_mapped_tests(tree):
    plan = verify.plan_python(["backend/server/api/guild.py", "backend/orphan.py", "backend/gone.py"], tree)
    by_name = {s.name: s for s in plan.stages}

    assert [s.name for s in plan.stages] == ["ruff-format", "ruff-check", "pyright", "pytest"]
    assert by_name["ruff-format"].cmd[:6] == ("uv", "run", "--frozen", "ruff", "format", "--check")
    # deleted files are not handed to ruff
    assert by_name["ruff-format"].cmd[-2:] == ("backend/server/api/guild.py", "backend/orphan.py")
    assert by_name["ruff-check"].cmd[3:5] == ("ruff", "check")
    assert by_name["pyright"].cmd == ("uv", "run", "--frozen", "pyright")
    assert by_name["pytest"].cmd[-2:] == ("tests/server/test_guild.py", "tests/server/test_guild_history.py")
    assert by_name["pytest"].keep == "tail"
    assert plan.notes == ["no mapped tests: backend/orphan.py", "no mapped tests: backend/gone.py"]


def test_python_plan_skips_pyright_without_backend_changes_and_ruff_without_python(tree):
    plan = verify.plan_python(["tests/server/test_users.py"], tree)
    skipped = {s.name: s.skip for s in plan.stages if s.skip}
    assert "pyright" in skipped and "backend" in skipped["pyright"]

    sql_only = verify.plan_python(["db/migrations/0099_x.sql"], tree)
    assert [(s.name, bool(s.skip)) for s in sql_only.stages] == [("ruff", True), ("pyright", True), ("pytest", False)]


def test_python_plan_is_one_skip_line_when_nothing_python_changed(tree):
    plan = verify.plan_python(["frontend/src/App.tsx", "README.md"], tree)
    assert [(s.name, s.skip) for s in plan.stages] == [("python", "no python-side changes")]


def test_frontend_plan_only_when_frontend_changed(tree):
    assert [s.name for s in verify.plan_frontend(["backend/orphan.py"], tree, _tool).stages] == ["frontend"]

    plan = verify.plan_frontend(["frontend/src/App.tsx", "frontend/src/gone.tsx", "frontend/x.css"], tree, _tool)
    by_name = {s.name: s for s in plan.stages}
    assert [s.name for s in plan.stages] == ["tsc", "vitest", "eslint"]
    assert all(s.cwd == "frontend" for s in plan.stages)
    assert by_name["tsc"].cmd == ("/fake/bin/tsc", "-b")
    assert by_name["vitest"].cmd[:3] == ("/fake/bin/vitest", "related", "src/App.tsx")
    assert "--run" in by_name["vitest"].cmd
    assert by_name["eslint"].cmd == ("/fake/bin/eslint", "src/App.tsx")


def test_frontend_plan_marks_missing_node_tools_unavailable(tree):
    plan = verify.plan_frontend(["frontend/src/App.tsx"], tree, _no_tool)
    assert all(s.skip and s.unavailable for s in plan.stages)
    assert "node_modules" in plan.stages[0].skip


def test_side_flags_restrict_the_plan(tree):
    changed = ["backend/server/api/guild.py", "frontend/src/App.tsx"]
    py_only = verify.plan_changed(changed, tree, _tool, fe=False)
    fe_only = verify.plan_changed(changed, tree, _tool, py=False)
    assert {s.name for s in py_only.stages} == {"ruff-format", "ruff-check", "pyright", "pytest"}
    assert {s.name for s in fe_only.stages} == {"tsc", "vitest", "eslint"}


def test_all_plan_follows_the_pre_push_hook_then_eslint_and_build():
    plan = verify.plan_all(_tool)
    assert [s.name for s in plan.stages] == [
        "tsc",
        "vitest",
        "ruff-format",
        "ruff-check",
        "pyright",
        "pytest",
        "eslint",
        "vite-build",
    ]
    assert plan.stages[-1].cmd == ("/fake/bin/vite", "build")


def test_all_plan_runs_exactly_the_commands_in_the_pre_push_hook():
    """Drift guard: every check .githooks/pre-push runs appears in --all, in the same order."""
    hook = (REPO / ".githooks" / "pre-push").read_text(encoding="utf-8")
    hook_cmds = [m.group(1).split() for m in re.finditer(r"^if ! (.+?); then$", hook, re.MULTILINE)]
    hook_cmds = [c for c in hook_cmds if c[0] != "command"]
    assert len(hook_cmds) == 6, hook_cmds

    planned = []
    for stage in verify.plan_all(_tool).stages[:6]:
        cmd = [a for a in stage.cmd if a != "--output-format=concise"]
        if cmd[0].startswith("/fake/bin/"):
            cmd[0] = "./node_modules/.bin/" + cmd[0].rsplit("/", 1)[1]
        planned.append(cmd)
    assert planned == hook_cmds


# ── output ────────────────────────────────────────────────────────────────────


def test_trim_output_head_keeps_first_lines_and_the_final_count_line():
    text = "\n".join(f"a.py:{i}: E1 bad" for i in range(100)) + "\nFound 100 errors.\n"
    lines = verify.trim_output(text, 5, "head")
    assert lines[:4] == [f"a.py:{i}: E1 bad" for i in range(4)]
    assert lines[4] == "... (96 lines omitted)"
    assert lines[5] == "Found 100 errors."


def test_trim_output_tail_keeps_the_failure_summary():
    text = "\n".join(f"line {i}" for i in range(50)) + "\nFAILED tests/x.py::test_a - assert 1 == 2\n1 failed in 0.1s"
    lines = verify.trim_output(text, 3, "tail")
    assert lines == [
        "... (49 earlier lines omitted)",
        "line 49",
        "FAILED tests/x.py::test_a - assert 1 == 2",
        "1 failed in 0.1s",
    ]


def test_trim_output_strips_colour_blank_and_progress_lines_and_cuts_long_lines():
    text = "\x1b[31mred\x1b[0m\n\n....F..                [ 42%]\n" + "x" * 1000
    lines = verify.trim_output(text, 40)
    assert lines[0] == "red"
    assert len(lines) == 2 and lines[1].endswith(" ...") and len(lines[1]) < 400


def test_format_result_shapes():
    ok = verify.Result(verify.Stage("pyright", ("uv",)), "PASS", 3.21)
    skip = verify.Result(verify.Stage("pytest", skip="no mapped tests"), "SKIP")
    bad = verify.Result(verify.Stage("ruff-check", ("uv",)), "FAIL", 0.44, "a.py:1:1: F401 unused\nFound 1 error.")
    assert verify.format_result(ok, 40) == ["PASS  pyright  3.2s"]
    assert verify.format_result(skip, 40) == ["SKIP  pytest  no mapped tests"]
    assert verify.format_result(bad, 40) == ["FAIL  ruff-check  0.4s", "  a.py:1:1: F401 unused", "  Found 1 error."]


def test_summary_exit_codes():
    passed = verify.Result(verify.Stage("a", ("x",)), "PASS", 1.0)
    failed = verify.Result(verify.Stage("b", ("x",)), "FAIL", 1.0)
    missing = verify.Result(verify.Stage("tsc", skip="missing", unavailable=True), "SKIP")
    skipped = verify.Result(verify.Stage("c", skip="nothing to do"), "SKIP")

    assert verify.summarise([passed, skipped], 2.0) == ("verify: OK  (1 passed, 0 failed, 1 skipped)  2.0s", 0)
    line, code = verify.summarise([passed, failed, missing], 2.0)
    assert code == 1 and line.startswith("verify: FAIL  b  ")
    line, code = verify.summarise([passed, missing], 2.0)
    assert code == 2 and "tools missing for tsc" in line
    assert "1 not run (--fail-fast)" in verify.summarise([failed], 1.0, not_run=1)[0]


# ── main ──────────────────────────────────────────────────────────────────────


def _fake_runner(failing: set[str], seen: list[list[str]]):
    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "replace"
        tool = cmd[3] if cmd[0] == "uv" else Path(cmd[0]).name
        rc = 1 if tool in failing else 0
        return subprocess.CompletedProcess(cmd, rc, stdout=f"{tool} says no\n" if rc else "noise\n", stderr=None)

    return fake_run


def test_dry_run_prints_the_plan_and_runs_nothing(monkeypatch, tree, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run must not run anything")

    monkeypatch.setattr(verify.subprocess, "run", boom)
    code = verify.main(["--dry-run", "--files", "backend/server/api/guild.py", "backend/orphan.py"], root=tree)
    out = capsys.readouterr().out.splitlines()

    assert code == 0
    assert out[0] == "no mapped tests: backend/orphan.py"
    assert out[1].startswith("PLAN  ruff-format  uv run --frozen ruff format --check")
    assert out[-2].startswith("PLAN  pytest  uv run --frozen pytest --tb=short -q tests/server/test_guild.py")
    assert out[-1] == "SKIP  frontend  nothing under frontend/ changed"


def test_run_prints_one_line_per_stage_and_only_failing_output(monkeypatch, tree, capsys):
    seen: list[list[str]] = []
    monkeypatch.setattr(verify.subprocess, "run", _fake_runner({"ruff"}, seen))
    code = verify.main(["--py", "--files", "backend/server/api/guild.py"], root=tree)
    out = capsys.readouterr().out.splitlines()

    assert code == 1
    assert re.fullmatch(r"FAIL  ruff-format  \d+\.\ds", out[0])
    assert out[1] == "  ruff says no"
    assert re.fullmatch(r"FAIL  ruff-check  \d+\.\ds", out[2])
    assert re.fullmatch(r"PASS  pyright  \d+\.\ds", out[4])  # stages keep running after a failure
    assert re.fullmatch(r"PASS  pytest  \d+\.\ds", out[5])
    assert "noise" not in "\n".join(out)
    assert out[-1].startswith("verify: FAIL  ruff-format, ruff-check  (2 passed, 2 failed, 0 skipped)")
    assert len(seen) == 4


def test_fail_fast_stops_after_the_first_failure(monkeypatch, tree, capsys):
    seen: list[list[str]] = []
    monkeypatch.setattr(verify.subprocess, "run", _fake_runner({"ruff"}, seen))
    code = verify.main(["--py", "--fail-fast", "--files", "backend/server/api/guild.py"], root=tree)
    out = capsys.readouterr().out

    assert code == 1
    assert len(seen) == 1
    assert "3 not run (--fail-fast)" in out


def test_missing_frontend_tools_make_the_run_incomplete(monkeypatch, tree, capsys):
    monkeypatch.setattr(verify.subprocess, "run", _fake_runner(set(), []))
    code = verify.main(["--fe", "--files", "frontend/src/App.tsx"], root=tree)  # tree has no node_modules
    out = capsys.readouterr().out

    assert code == 2
    assert "SKIP  tsc  frontend/node_modules/.bin/tsc missing" in out
    assert "verify: INCOMPLETE" in out


def test_nothing_changed_is_ok(monkeypatch, tree, capsys):
    monkeypatch.setattr(verify, "git_changed", lambda root, base=None: [])
    assert verify.main([], root=tree) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_git_failure_is_an_environment_error(monkeypatch, tree, capsys):
    def broken(root, base=None):
        raise RuntimeError("diff --name-only HEAD failed: not a git repository")

    monkeypatch.setattr(verify, "git_changed", broken)
    assert verify.main([], root=tree) == 2
    assert capsys.readouterr().err.strip() == "verify: diff --name-only HEAD failed: not a git repository"


def test_all_rejects_a_change_set(tree, capsys):
    assert verify.main(["--all", "--files", "x.py"], root=tree) == 2
    assert "--all" in capsys.readouterr().err
