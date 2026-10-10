"""Run the right checks for a change set and print only what failed.

One line per stage (``PASS`` / ``SKIP`` / ``FAIL``); a failing stage is followed
by its output trimmed to ``--max-lines``. Exit 0 = all ran and passed, 1 = a
check failed, 2 = usage error or a needed tool is missing.

Examples:
    uv run --frozen python scripts/tools/verify.py                      # uncommitted + untracked changes
    uv run --frozen python scripts/tools/verify.py --base origin/main   # ...plus commits since origin/main
    uv run --frozen python scripts/tools/verify.py --py --fail-fast     # python side only, stop at first failure
    uv run --frozen python scripts/tools/verify.py --all                # pre-push pipeline + eslint + vite build
    uv run --frozen python scripts/tools/verify.py --dry-run --files backend/server/api/guild.py frontend/src/App.tsx

Test mapping in ``--changed`` mode (see ``map_tests``): ``backend/**/foo.py`` ->
``tests/**/test_foo*.py``; changed tests run themselves; migrations, ``.sql``
sidecars, ``backend/server/db/**`` and the agent docs add their fixed tests. A
changed backend file with no test prints ``no mapped tests: <file>``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

UV = ("uv", "run", "--frozen")
#: Above this many file arguments a stage falls back to the whole tree (Windows command-line limit).
MAX_FILE_ARGS = 150
MAX_LINE_CHARS = 300

#: One line per finding instead of ruff's default code frames; the verdict is unchanged.
_CONCISE = "--output-format=concise"
_SRC_EXT = (".ts", ".tsx", ".js", ".jsx")
_LINT_EXT = (".ts", ".tsx")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
#: pytest -q progress rows ("....F..  [ 42%]") and dot-reporter rows carry nothing the summary does not.
_PROGRESS_RE = re.compile(r"^(?:[.FEsxX]+\s+\[\s*\d+%\]|[.·]+)$")

ToolFinder = Callable[[str], str | None]


@dataclass(frozen=True)
class Stage:
    """One check. ``skip`` set = not run; ``unavailable`` = skipped because a tool is missing."""

    name: str
    cmd: tuple[str, ...] = ()
    cwd: str = "."
    keep: str = "head"  # which end of a failing stage's output survives trimming
    skip: str = ""
    unavailable: bool = False


@dataclass
class Plan:
    stages: list[Stage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Result:
    stage: Stage
    status: str  # PASS | FAIL | SKIP
    seconds: float = 0.0
    output: str = ""


# ── change set ────────────────────────────────────────────────────────────────


def normalise_paths(paths: Sequence[str]) -> list[str]:
    """Repo-relative posix paths, de-duplicated, order-stable."""
    seen: dict[str, None] = {}
    for raw in paths:
        p = raw.strip().replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        if p:
            seen.setdefault(p, None)
    return list(seen)


def git_changed(root: Path, base: str | None = None) -> list[str]:
    """Changed files: working tree vs HEAD, untracked, and (with ``base``) ``base...HEAD``."""
    cmds = [
        ["git", "-c", "core.quotepath=off", "diff", "--name-only", "HEAD"],
        ["git", "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard"],
    ]
    if base:
        cmds.append(["git", "-c", "core.quotepath=off", "diff", "--name-only", f"{base}...HEAD"])
    out: list[str] = []
    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd, cwd=root, capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL
            )
        except OSError as exc:
            raise RuntimeError(f"cannot run git: {exc}") from exc
        if proc.returncode != 0:
            first = (proc.stderr or proc.stdout).strip().splitlines()[:1]
            raise RuntimeError(f"{' '.join(cmd[3:])} failed: {first[0] if first else proc.returncode}")
        out.extend(proc.stdout.splitlines())
    return normalise_paths(out)


# ── test mapping ──────────────────────────────────────────────────────────────


def _stems(path: str) -> list[str]:
    """Names a source file's tests may be called after: ``foo.py`` -> foo;
    ``pkg/__init__.py`` -> pkg; ``_shared.py`` -> _shared, shared."""
    p = Path(path)
    stem = p.parent.name if p.stem == "__init__" else p.stem
    bare = stem.lstrip("_")
    return [stem] if bare == stem or not bare else [stem, bare]


def map_tests(changed: Sequence[str], root: Path) -> tuple[list[str], list[str]]:
    """Map changed paths to pytest targets. Returns ``(tests, unmapped)`` where
    ``unmapped`` are changed backend/test-support files no rule covered."""
    tests_dir = root / "tests"
    all_tests = (
        sorted(p.relative_to(root).as_posix() for p in tests_dir.rglob("test_*.py")) if tests_dir.is_dir() else []
    )

    def by_stem(path: str) -> set[str]:
        return {t for t in all_tests for s in _stems(path) if Path(t).name.startswith(f"test_{s}")}

    def fixed(*paths: str) -> set[str]:
        return {p for p in paths if (root / p).is_file()}

    tests: set[str] = set()
    unmapped: list[str] = []
    for path in changed:
        name = path.rsplit("/", 1)[-1]
        found: set[str] = set()
        report = False
        if path.startswith("tests/"):
            if name.startswith("test_") and name.endswith(".py"):
                found |= fixed(path)
            elif name == "conftest.py" and path != "tests/conftest.py":
                found.add(path.rsplit("/", 1)[0])
            elif name.endswith(".py") and name != "__init__.py":
                report = True  # shared fixture/helper: its dependants are not known statically
        elif path.startswith("db/migrations/"):
            found |= fixed("tests/test_pg_migrate.py")
        elif path.startswith("backend/"):
            if path.startswith("backend/server/db/"):
                found |= fixed("tests/server/test_db_facade.py")
            if name.endswith(".sql"):
                found |= fixed("tests/test_sql_blocks.py", "tests/test_sql_loader.py")
                found |= by_stem(path[:-4] + ".py")
            elif name.endswith(".py"):
                found |= by_stem(path)
                report = True
        elif path.startswith("scripts/") and name.endswith(".py"):
            found |= by_stem(path)
        if path == "CLAUDE.md" or path.startswith(".claude/") or path == "scripts/tools/check_agent_docs.py":
            found |= fixed("tests/test_agent_docs.py")
        if report and not found:
            unmapped.append(path)
        tests |= found
    return sorted(tests), unmapped


# ── planning ──────────────────────────────────────────────────────────────────


def find_node_tool(root: Path) -> ToolFinder:
    """Resolver for ``frontend/node_modules/.bin/<name>`` (``.cmd`` shim on Windows)."""

    def find(name: str) -> str | None:
        path = root / "frontend" / "node_modules" / ".bin" / (f"{name}.cmd" if os.name == "nt" else name)
        return str(path) if path.is_file() else None

    return find


def _node_stage(tool: ToolFinder, name: str, binary: str, args: Sequence[str], keep: str = "head") -> Stage:
    exe = tool(binary)
    if exe is None:
        reason = f"frontend/node_modules/.bin/{binary} missing (run npm install in frontend/)"
        return Stage(name, skip=reason, unavailable=True)
    return Stage(name, (exe, *args), cwd="frontend", keep=keep)


def _file_args(files: Sequence[str], fallback: str) -> tuple[str, ...]:
    return tuple(files) if len(files) <= MAX_FILE_ARGS else (fallback,)


def plan_python(changed: Sequence[str], root: Path) -> Plan:
    py_files = [f for f in changed if f.endswith(".py") and not f.startswith("frontend/") and (root / f).is_file()]
    tests, unmapped = map_tests(changed, root)
    plan = Plan(notes=[f"no mapped tests: {f}" for f in unmapped])
    if "tests/conftest.py" in changed:
        plan.notes.append("tests/conftest.py changed: every test depends on it, use --all for the full suite")
    if not (py_files or tests or unmapped or "pyproject.toml" in changed):
        plan.stages.append(Stage("python", skip="no python-side changes"))
        return plan
    if py_files:
        args = _file_args(py_files, ".")
        plan.stages.append(Stage("ruff-format", (*UV, "ruff", "format", "--check", "--force-exclude", _CONCISE, *args)))
        plan.stages.append(Stage("ruff-check", (*UV, "ruff", "check", "--force-exclude", _CONCISE, *args)))
    else:
        plan.stages.append(Stage("ruff", skip="no python files changed"))
    if "pyproject.toml" in changed or any(f.startswith("backend/") and f.endswith(".py") for f in changed):
        plan.stages.append(Stage("pyright", (*UV, "pyright")))
    else:
        plan.stages.append(Stage("pyright", skip="no backend/ python changed (pyright only covers backend/)"))
    if tests:
        targets = _file_args(tests, "tests")
        plan.stages.append(Stage("pytest", (*UV, "pytest", "--tb=short", "-q", *targets), keep="tail"))
    else:
        plan.stages.append(Stage("pytest", skip="no mapped tests"))
    return plan


def plan_frontend(changed: Sequence[str], root: Path, tool: ToolFinder) -> Plan:
    touched = [f for f in changed if f.startswith("frontend/")]
    if not touched:
        return Plan([Stage("frontend", skip="nothing under frontend/ changed")])
    rel = [f[len("frontend/") :] for f in touched if (root / f).is_file()]
    src = [f for f in rel if f.endswith(_SRC_EXT)]
    lint = [f for f in rel if f.endswith(_LINT_EXT)]
    plan = Plan([_node_stage(tool, "tsc", "tsc", ("-b",))])
    if src and len(src) <= MAX_FILE_ARGS:
        args = ("related", *src, "--run", "--reporter=dot", "--passWithNoTests")
        plan.stages.append(_node_stage(tool, "vitest", "vitest", args, keep="tail"))
    elif src:
        plan.stages.append(_node_stage(tool, "vitest", "vitest", ("run", "--reporter=dot"), keep="tail"))
    else:
        plan.stages.append(Stage("vitest", skip="no frontend source files changed"))
    if lint:
        plan.stages.append(_node_stage(tool, "eslint", "eslint", _file_args(lint, "src")))
    else:
        plan.stages.append(Stage("eslint", skip="no .ts/.tsx files changed"))
    return plan


def plan_changed(changed: Sequence[str], root: Path, tool: ToolFinder, *, py: bool = True, fe: bool = True) -> Plan:
    plan = Plan()
    for part in ([plan_python(changed, root)] if py else []) + ([plan_frontend(changed, root, tool)] if fe else []):
        plan.stages += part.stages
        plan.notes += part.notes
    return plan


def plan_all(tool: ToolFinder, *, py: bool = True, fe: bool = True) -> Plan:
    """The .githooks/pre-push pipeline in its order, then ESLint and ``vite build``."""
    stages: list[Stage] = []
    if fe:
        stages.append(_node_stage(tool, "tsc", "tsc", ("-b",)))
        stages.append(_node_stage(tool, "vitest", "vitest", ("run", "--reporter=dot"), keep="tail"))
    if py:
        stages.append(Stage("ruff-format", (*UV, "ruff", "format", "--check", _CONCISE, ".")))
        stages.append(Stage("ruff-check", (*UV, "ruff", "check", _CONCISE, ".")))
        stages.append(Stage("pyright", (*UV, "pyright")))
        stages.append(Stage("pytest", (*UV, "pytest", "--tb=short", "-q"), keep="tail"))
    if fe:
        stages.append(_node_stage(tool, "eslint", "eslint", ("src",)))
        stages.append(_node_stage(tool, "vite-build", "vite", ("build",), keep="tail"))
    return Plan(stages)


# ── running + reporting ───────────────────────────────────────────────────────


def trim_output(text: str, max_lines: int, keep: str = "head") -> list[str]:
    """Strip colour/blank/progress lines and cut to ``max_lines``. ``keep="tail"``
    keeps the end (pytest/vitest failure summaries); ``"head"`` keeps the first
    lines plus the final one, where linters print their error count."""
    lines = []
    for raw in text.splitlines():
        line = _ANSI_RE.sub("", raw).rstrip()
        if not line.strip() or _PROGRESS_RE.match(line.strip()):
            continue
        lines.append(line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " ...")
    max_lines = max(1, max_lines)
    if len(lines) <= max_lines:
        return lines
    omitted = len(lines) - max_lines
    if keep == "tail":
        return [f"... ({omitted} earlier lines omitted)", *lines[-max_lines:]]
    return [*lines[: max_lines - 1], f"... ({omitted} lines omitted)", lines[-1]]


def display_cmd(stage: Stage, root: Path) -> str:
    """The command as a human would type it: repo-relative paths, forward slashes."""
    prefix = str(root)
    parts = []
    for arg in stage.cmd:
        if arg.startswith(prefix):
            arg = arg[len(prefix) :].lstrip("\\/")
            if stage.cwd != "." and arg.replace("\\", "/").startswith(stage.cwd + "/"):
                arg = arg[len(stage.cwd) + 1 :]
        parts.append(arg.replace("\\", "/"))
    where = "" if stage.cwd == "." else f"(cd {stage.cwd}) "
    return where + " ".join(parts)


def _env() -> dict[str, str]:
    env = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0")
    if os.name == "nt" and shutil.which("node") is None:
        # Git Bash does not always inherit the Node install dir (the pre-push hook adds it too).
        node_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs"
        if node_dir.is_dir():
            env["PATH"] = f"{node_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def run_stage(stage: Stage, root: Path, timeout: float) -> Result:
    if stage.skip:
        return Result(stage, "SKIP")
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            list(stage.cmd),
            cwd=root / stage.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_env(),
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return Result(stage, "FAIL", time.perf_counter() - start, f"{partial}\ntimed out after {timeout:.0f}s")
    except OSError as exc:
        missing = Stage(stage.name, skip=f"cannot run {stage.cmd[0]}: {exc.strerror or exc}", unavailable=True)
        return Result(missing, "SKIP")
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return Result(stage, status, time.perf_counter() - start, proc.stdout or "")


def format_result(result: Result, max_lines: int) -> list[str]:
    stage = result.stage
    if result.status == "SKIP":
        return [f"SKIP  {stage.name}  {stage.skip}"]
    lines = [f"{result.status}  {stage.name}  {result.seconds:.1f}s"]
    if result.status == "FAIL":
        body = trim_output(result.output, max_lines, stage.keep) or ["(no output)"]
        lines += [f"  {line}" for line in body]
    return lines


def summarise(results: Sequence[Result], seconds: float, not_run: int = 0) -> tuple[str, int]:
    """Final line + exit code (1 = a check failed, 2 = a needed tool was missing)."""
    failed = [r.stage.name for r in results if r.status == "FAIL"]
    missing = [r.stage.name for r in results if r.stage.unavailable]
    passed = sum(r.status == "PASS" for r in results)
    skipped = sum(r.status == "SKIP" for r in results)
    counts = f"{passed} passed, {len(failed)} failed, {skipped} skipped"
    if not_run:
        counts += f", {not_run} not run (--fail-fast)"
    if failed:
        return f"verify: FAIL  {', '.join(failed)}  ({counts})  {seconds:.1f}s", 1
    if missing:
        return f"verify: INCOMPLETE  tools missing for {', '.join(missing)}  ({counts})  {seconds:.1f}s", 2
    return f"verify: OK  ({counts})  {seconds:.1f}s", 0


def format_plan(plan: Plan, root: Path, max_lines: int) -> list[str]:
    lines = cap_notes(plan.notes, max_lines)
    for stage in plan.stages:
        lines.append(
            f"SKIP  {stage.name}  {stage.skip}" if stage.skip else f"PLAN  {stage.name}  {display_cmd(stage, root)}"
        )
    return lines


def cap_notes(notes: Sequence[str], max_lines: int) -> list[str]:
    if len(notes) <= max_lines:
        return list(notes)
    return [*notes[:max_lines], f"... ({len(notes) - max_lines} more notes omitted)"]


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="Run the checks that match a change set; print one line per stage and only failing output.",
        epilog="Exit codes: 0 all passed, 1 a check failed, 2 usage error or a needed tool is missing.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--changed",
        action="store_true",
        help="check only what the changed files need (default): uncommitted + untracked",
    )
    mode.add_argument("--all", action="store_true", help="the full pre-push pipeline, then eslint and vite build")
    side = parser.add_mutually_exclusive_group()
    side.add_argument("--py", action="store_true", help="python side only")
    side.add_argument("--fe", action="store_true", help="frontend side only")
    parser.add_argument("--base", metavar="REF", help="also include files changed in REF...HEAD (e.g. origin/main)")
    parser.add_argument("--files", nargs="+", metavar="PATH", help="use these paths as the change set instead of git")
    parser.add_argument("--dry-run", action="store_true", help="print the planned stages and commands, run nothing")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first failing stage")
    parser.add_argument(
        "--max-lines", type=int, default=40, metavar="N", help="lines of output kept per failing stage (default 40)"
    )
    parser.add_argument("--timeout", type=float, default=1800, metavar="SEC", help="per-stage timeout (default 1800)")
    return parser


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=stream.encoding if stream.isatty() else "utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    args = build_parser().parse_args(argv)
    _utf8_stdio()
    if args.all and (args.base or args.files):
        print("verify: --all checks everything; drop --base/--files", file=sys.stderr)
        return 2
    py, fe = not args.fe, not args.py
    tool = find_node_tool(root)
    if args.all:
        plan = plan_all(tool, py=py, fe=fe)
    else:
        try:
            changed = normalise_paths(args.files) if args.files else git_changed(root, args.base)
        except RuntimeError as exc:
            print(f"verify: {exc}", file=sys.stderr)
            return 2
        if not changed:
            print("verify: OK  nothing changed (use --base REF, --files or --all)")
            return 0
        plan = plan_changed(changed, root, tool, py=py, fe=fe)

    if args.dry_run:
        print("\n".join(format_plan(plan, root, args.max_lines)))
        return 0

    for note in cap_notes(plan.notes, args.max_lines):
        print(note)
    started = time.perf_counter()
    results: list[Result] = []
    for stage in plan.stages:
        result = run_stage(stage, root, args.timeout)
        results.append(result)
        print("\n".join(format_result(result, args.max_lines)), flush=True)
        if args.fail_fast and result.status == "FAIL":
            break
    line, code = summarise(results, time.perf_counter() - started, not_run=len(plan.stages) - len(results))
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
