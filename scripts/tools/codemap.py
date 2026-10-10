"""Static index of the codebase: jump straight to ``file:line`` instead of grepping.

Source is parsed with ``ast`` / regex; the application is never imported.
Every subcommand takes an optional case-insensitive substring filter and caps
its output (``--max-lines``, default 120). Exit 0 = rows printed, 1 = nothing
matched, 2 = usage or environment error.

Examples:
    uv run --frozen python scripts/tools/codemap.py routes parses        # METHOD /api/path file:line handler
    uv run --frozen python scripts/tools/codemap.py routes "delete "     # filter matches any part of the row
    uv run --frozen python scripts/tools/codemap.py stores guild_history # store classes + methods (name:line on a hit)
    uv run --frozen python scripts/tools/codemap.py sql claim            # -- :name blocks in the .sql sidecars
    uv run --frozen python scripts/tools/codemap.py pages raid           # frontend routes -> page file
    uv run --frozen python scripts/tools/codemap.py loops                # lifespan tasks, leader-only loops, bot loops
    uv run --frozen python scripts/tools/codemap.py tables users.claims  # columns from the local TEST database

Notes: a route's line is its ``@router.<method>`` decorator; routers that are
never included in the app are listed with ``[not included in app]``; ``tables``
marks primary-key columns with ``*`` and only ever talks to localhost.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DSN = "postgresql://postgres:postgres@localhost:5432/eq2lexicon_test"
APP_MODULE = "backend.server.app"

_HTTP = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
_STORE_ROOT = "SchemaBound"
_STORE_BASES = ("PgStoreBase", "PgCatalogue", "SchemaBound")
_SQL_NAME_RE = re.compile(r"^\s*--\s*:name\s+([a-z_][a-z0-9_]*)\s*$", re.IGNORECASE)
_SCRATCH_RE = re.compile(r".+_s\d+_\d+")


class EnvError(Exception):
    """Environment problem (missing file, unreachable database): exit code 2."""


# ── Python source index ───────────────────────────────────────────────────────


@dataclass
class Module:
    name: str
    path: Path
    tree: ast.Module
    is_package: bool
    #: local name -> (module, attr); attr None means the name IS that module.
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    #: name -> every value assigned to it, any scope (app.py builds ``app`` inside create_app()).
    assigns: dict[str, list[ast.expr]] = field(default_factory=dict)
    #: for-loop variable -> the literal elements it iterates over.
    bindings: dict[str, list[ast.expr]] = field(default_factory=dict)
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)


class Repo:
    """Lazy ``ast`` index over the repo's Python modules, with just enough name
    resolution to follow ``from x import router`` re-exports."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, Module | None] = {}

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def module_names(self, package: str = "backend") -> list[str]:
        base = self.root / package
        names = []
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            parts = list(path.relative_to(self.root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            names.append(".".join(parts))
        return names

    def module(self, name: str) -> Module | None:
        if name not in self._cache:
            self._cache[name] = self._load(name)
        return self._cache[name]

    def _load(self, name: str) -> Module | None:
        stem = self.root.joinpath(*name.split("."))
        for path, is_pkg in ((stem / "__init__.py", True), (stem.with_suffix(".py"), False)):
            if path.is_file():
                break
        else:
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return None
        mod = Module(name, path, tree, is_pkg)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = _import_base(mod, node)
                for alias in node.names:
                    mod.imports.setdefault(alias.asname or alias.name, (base, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    mod.imports.setdefault(alias.asname or target, (target, None))
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                mod.assigns.setdefault(node.targets[0].id, []).append(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                mod.assigns.setdefault(node.target.id, []).append(node.value)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                mod.funcs.setdefault(node.name, node)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                mod.classes[node.name] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.For | ast.AsyncFor):
                _bind_loop(mod, node)
        return mod

    # -- name resolution ------------------------------------------------------

    def module_of(self, mod: Module, node: ast.expr) -> str | None:
        """The dotted module an expression refers to (``triggers`` / ``pkg.sub``), if any."""
        if isinstance(node, ast.Name):
            target = mod.imports.get(node.id)
            if target is None:
                return None
            base, attr = target
            if attr is None:
                return base
            candidate = f"{base}.{attr}" if base else attr
            return candidate if self.module(candidate) else None
        if isinstance(node, ast.Attribute):
            base = self.module_of(mod, node.value)
            candidate = f"{base}.{node.attr}" if base else None
            return candidate if candidate and self.module(candidate) else None
        return None

    def resolve_name(self, module: str, name: str, depth: int = 0) -> tuple[str, str] | None:
        """Follow imports until the module that actually defines ``name``."""
        mod = self.module(module)
        if mod is None or depth > 12:
            return None
        if name in mod.assigns or name in mod.funcs or name in mod.classes:
            return module, name
        target = mod.imports.get(name)
        if target is None or target[1] is None:
            return None
        return self.resolve_name(target[0], target[1], depth + 1)

    def resolve(self, mod: Module, node: ast.expr) -> tuple[str, str] | None:
        """``router`` / ``triggers.router`` -> (defining module, name)."""
        if isinstance(node, ast.Name):
            return self.resolve_name(mod.name, node.id)
        if isinstance(node, ast.Attribute):
            base = self.module_of(mod, node.value)
            return self.resolve_name(base, node.attr) if base else None
        return None

    def const_str(self, mod: Module, node: ast.expr | None) -> str | None:
        """A string literal, or a name bound to one (locally or via an import)."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name | ast.Attribute):
            target = self.resolve(mod, node)
            owner = self.module(target[0]) if target else None
            for value in owner.assigns.get(target[1], []) if owner and target else []:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
        return None

    def expand(self, mod: Module, node: ast.expr) -> list[ast.expr]:
        """A loop variable becomes the literal elements it iterates over."""
        if isinstance(node, ast.Name) and node.id in mod.bindings:
            return mod.bindings[node.id]
        return [node]


def _import_base(mod: Module, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parts = mod.name.split(".")
    if not mod.is_package:
        parts = parts[:-1]
    parts = parts[: len(parts) - (node.level - 1)]
    return ".".join([*parts, *([node.module] if node.module else [])])


def _bind_loop(mod: Module, node: ast.For | ast.AsyncFor) -> None:
    source = node.iter
    if isinstance(source, ast.Name):
        literals = [v for v in mod.assigns.get(source.id, []) if isinstance(v, ast.List | ast.Tuple)]
        source = literals[0] if literals else None
    if not isinstance(source, ast.List | ast.Tuple):
        return
    if isinstance(node.target, ast.Name):
        mod.bindings.setdefault(node.target.id, list(source.elts))
    elif isinstance(node.target, ast.Tuple):
        for i, target in enumerate(node.target.elts):
            if isinstance(target, ast.Name):
                column = [e.elts[i] for e in source.elts if isinstance(e, ast.List | ast.Tuple) and len(e.elts) > i]
                mod.bindings.setdefault(target.id, column)


def _call_name(node: ast.expr) -> str:
    """``APIRouter(...)`` / ``fastapi.APIRouter(...)`` -> ``APIRouter``; '' if not a call."""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _dotted(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse handles every expr we pass
        return "?"


# ── routes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    file: str
    line: int
    handler: str
    note: str = ""


def collect_routes(repo: Repo, package: str = "backend") -> list[Route]:
    """Every served HTTP route with its real path (include + router prefixes applied)."""
    routers: dict[tuple[str, str], tuple[str, bool]] = {}  # key -> (own prefix, is FastAPI app)
    decorated: dict[tuple[str, str], list[Route]] = {}
    includes: dict[tuple[str, str], list[tuple[tuple[str, str], str]]] = {}
    mods = [m for m in (repo.module(n) for n in repo.module_names(package)) if m is not None]

    for mod in mods:
        for name, values in mod.assigns.items():
            for value in values:
                kind = _call_name(value)
                if kind in ("APIRouter", "FastAPI") and isinstance(value, ast.Call):
                    prefix = repo.const_str(mod, _kwarg(value, "prefix")) or ""
                    routers.setdefault((mod.name, name), (prefix, kind == "FastAPI"))

    for mod in mods:
        file = repo.rel(mod.path)
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for dec in node.decorator_list:
                    route = _decorator_route(repo, mod, dec, routers)
                    if route:
                        key, methods, path = route
                        decorated.setdefault(key, []).append(Route(methods, path, file, dec.lineno, node.name))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
                owner = repo.resolve(mod, node.func.value)
                if owner not in routers:
                    continue
                if node.func.attr == "include_router":
                    prefix = repo.const_str(mod, _kwarg(node, "prefix")) or ""
                    for child_node in repo.expand(mod, node.args[0]):
                        child = repo.resolve(mod, child_node)
                        if child in routers:
                            includes.setdefault(owner, []).append((child, prefix))
                elif node.func.attr == "mount":
                    target = _call_name(node.args[1]) if len(node.args) > 1 else ""
                    for path_node in repo.expand(mod, node.args[0]):
                        path = repo.const_str(mod, path_node) or "<dynamic>"
                        row = Route("MOUNT", path, file, node.lineno, target or "static")
                        decorated.setdefault(owner, []).append(row)

    out: list[Route] = []
    reached: set[tuple[str, str]] = set()

    def emit(key: tuple[str, str], prefix: str, trail: frozenset[tuple[str, str]]) -> None:
        if key in trail:
            return
        reached.add(key)
        base = prefix + routers[key][0]
        for r in decorated.get(key, []):
            out.append(Route(r.method, base + r.path, r.file, r.line, r.handler))
        for child, inc_prefix in includes.get(key, []):
            emit(child, base + inc_prefix, trail | {key})

    for key, (_prefix, is_app) in routers.items():
        if is_app:
            emit(key, "", frozenset())
    for key, rows in decorated.items():
        if key not in reached:
            prefix = routers[key][0]
            out.extend(
                Route(r.method, prefix + r.path, r.file, r.line, r.handler, "[not included in app]") for r in rows
            )
    return sorted(set(out), key=lambda r: (r.path, r.method, r.file, r.line))


def _decorator_route(
    repo: Repo, mod: Module, dec: ast.expr, routers: dict[tuple[str, str], tuple[str, bool]]
) -> tuple[tuple[str, str], str, str] | None:
    if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
        return None
    attr = dec.func.attr
    if attr not in _HTTP and attr not in ("websocket", "api_route"):
        return None
    key = repo.resolve(mod, dec.func.value)
    if key not in routers:
        return None
    path_node = dec.args[0] if dec.args else _kwarg(dec, "path")
    path = repo.const_str(mod, path_node) if path_node is not None else None
    if attr == "api_route":
        listed = _kwarg(dec, "methods")
        names = [e.value for e in listed.elts if isinstance(e, ast.Constant)] if isinstance(listed, ast.List) else []
        method = ",".join(str(n).upper() for n in names) or "GET"
    else:
        method = "WS" if attr == "websocket" else attr.upper()
    return key, method, path if path is not None else "<dynamic>"


def format_routes(routes: Iterable[Route]) -> list[str]:
    return [
        f"{r.method:<6} {r.path}  {r.file}:{r.line}  {r.handler}" + (f"  {r.note}" if r.note else "") for r in routes
    ]


# ── stores ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Store:
    name: str
    file: str
    line: int
    schema: str | None
    base: str
    methods: tuple[tuple[str, int], ...]


def collect_stores(repo: Repo, package: str = "backend") -> list[Store]:
    """Classes deriving (transitively) from ``SchemaBound`` - i.e. every
    ``PgStoreBase`` / ``PgCatalogue`` store plus the few bound to it directly."""
    classes: dict[str, tuple[Module, ast.ClassDef]] = {}
    for name in repo.module_names(package):
        mod = repo.module(name)
        if mod is not None:
            for cls_name, node in mod.classes.items():
                classes.setdefault(cls_name, (mod, node))

    def bases(node: ast.ClassDef) -> list[str]:
        return [
            b.id if isinstance(b, ast.Name) else b.attr for b in node.bases if isinstance(b, ast.Name | ast.Attribute)
        ]

    def lineage(name: str, seen: tuple[str, ...] = ()) -> list[str]:
        """Ancestor names, nearest first (repo classes only)."""
        if name not in classes or name in seen:
            return []
        out: list[str] = []
        for base in bases(classes[name][1]):
            out += [base, *lineage(base, (*seen, name))]
        return out

    def schema_of(name: str, seen: tuple[str, ...] = ()) -> str | None:
        if name not in classes or name in seen:
            return None
        mod, node = classes[name]
        init = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
        if init is not None:
            args = init.args
            positional = [*args.posonlyargs, *args.args]
            defaults: dict[str, ast.expr] = dict(
                zip([a.arg for a in positional][len(positional) - len(args.defaults) :], args.defaults, strict=False)
            )
            defaults.update(
                {a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=False) if d is not None}
            )
            if "schema" in defaults:
                return repo.const_str(mod, defaults["schema"])
            for call in ast.walk(init):  # super().__init__("users")
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "__init__":
                    if call.args and (value := repo.const_str(mod, call.args[0])):
                        return value
        return next((s for b in bases(node) if (s := schema_of(b, (*seen, name)))), None)

    stores = []
    for name, (mod, node) in classes.items():
        ancestors = lineage(name)
        if _STORE_ROOT not in ancestors or name in _STORE_BASES:
            continue
        methods = tuple(
            (n.name, n.lineno)
            for n in node.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and not n.name.startswith("_")
        )
        base = next((a for a in ancestors if a in _STORE_BASES), _STORE_ROOT)
        stores.append(Store(name, repo.rel(mod.path), node.lineno, schema_of(name), base, methods))
    return sorted(stores, key=lambda s: (s.file, s.line))


def format_stores(stores: Iterable[Store], flt: str = "", max_methods: int = 12) -> list[str]:
    """One line per store. With a filter, a store matches on its name/file/schema
    or on a method name; matching methods are listed first as ``name:line``."""
    needle = flt.lower()
    lines = []
    for s in stores:
        head = f"{s.name}  {s.file}:{s.line}  schema={s.schema or '?'}  base={s.base}"
        hits = [m for m in s.methods if needle and needle in m[0].lower()]
        if needle and needle not in head.lower() and not hits:
            continue
        rest = [m for m in s.methods if m not in hits]
        shown = [f"{n}:{ln}" for n, ln in hits] + [n for n, _ in rest]
        if max_methods and len(shown) > max(max_methods, len(hits)):
            keep = max(max_methods, len(hits))
            shown = [*shown[:keep], f"(+{len(shown) - keep} more)"]
        lines.append(f"{head}  methods: {', '.join(shown) if shown else '-'}")
    return lines


# ── sql sidecars ──────────────────────────────────────────────────────────────


def collect_sql(root: Path, package: str = "backend") -> list[str]:
    """``block_name  file:line`` for every ``-- :name`` block (backend/sql_loader.py format)."""
    lines = []
    for path in sorted((root / package).rglob("*.sql")):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, raw in enumerate(text.splitlines(), start=1):
            m = _SQL_NAME_RE.match(raw)
            if m:
                lines.append(f"{m.group(1)}  {rel}:{lineno}")
    return lines


# ── frontend pages ────────────────────────────────────────────────────────────

_IMPORT_RE = re.compile(r"^import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s+['\"](\.[^'\"]+)['\"]", re.MULTILINE)
_LAZY_RE = re.compile(r"\b(\w+)\s*=\s*lazy\(\s*\(\s*\)\s*=>\s*import\(\s*['\"](\.[^'\"]+)['\"]\s*\)")
_ROUTE_RE = re.compile(r"<Route\b")
_PATH_RE = re.compile(r"\bpath=\{?\s*['\"`]([^'\"`]*)['\"`]")
_ELEMENT_RE = re.compile(r"\belement=\{\s*<\s*([A-Za-z_][\w.]*)")
_TS_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts")


def collect_pages(root: Path, app_file: str = "frontend/src/App.tsx") -> list[str]:
    """``/path  ->  page file  (lazy|eager)`` from the router in App.tsx."""
    app = root / app_file
    if not app.is_file():
        raise EnvError(f"{app_file} not found")
    text = app.read_text(encoding="utf-8-sig")
    components: dict[str, tuple[str, str]] = {}  # component -> (module spec, lazy|eager)
    for m in _IMPORT_RE.finditer(text):
        default, named, spec = m.groups()
        if default:
            components[default] = (spec, "eager")
        for part in (named or "").split(","):
            local = part.split(" as ")[-1].strip()
            if local:
                components[local] = (spec, "eager")
    for m in _LAZY_RE.finditer(text):
        components[m.group(1)] = (m.group(2), "lazy")

    def resolve(spec: str) -> tuple[str, bool]:
        """(repo-relative file, exists) for an import specifier such as ``./pages/HomePage``."""
        rel = os.path.normpath(os.path.join(os.path.dirname(app_file), spec)).replace("\\", "/")
        for suffix in _TS_SUFFIXES:
            if (root / (rel + suffix)).is_file():
                return rel + suffix, True
        return rel, False

    lines = []
    starts = [m.start() for m in _ROUTE_RE.finditer(text)]
    for i, start in enumerate(starts):
        tag = text[start : starts[i + 1] if i + 1 < len(starts) else len(text)]
        tag = tag.split("</Route>")[0]
        path, element = _PATH_RE.search(tag), _ELEMENT_RE.search(tag)
        if not path or not element:
            continue  # pathless layout route
        component = element.group(1)
        if component in components:
            spec, mode = components[component]
            file, exists = resolve(spec)
            detail = mode if Path(file).stem == component else f"{mode}, {component}"
            if not exists:
                file += " (file not found)"
        else:
            file, detail = app_file, f"eager, {component} defined inline"
        lines.append(f"{path.group(1)}  ->  {file}  ({detail})")
    return lines


# ── background loops ──────────────────────────────────────────────────────────


def collect_loops(repo: Repo, app_module: str = APP_MODULE, bot_package: str = "backend/bot") -> list[str]:
    """Tasks the app lifespan starts (``asyncio.create_task`` in app.py), the
    loops a leader-gated task fans out to, and the bot's ``tasks.loop`` pollers."""
    mod = repo.module(app_module)
    if mod is None:
        raise EnvError(f"{app_module.replace('.', '/')}.py not found")
    lines = []

    def site(expr: ast.expr) -> tuple[str, str, ast.AST | None]:
        """(file:line, function name, def node) for a coroutine function expression."""
        if isinstance(expr, ast.Name) and expr.id in mod.funcs:
            node = mod.funcs[expr.id]
            return f"{repo.rel(mod.path)}:{node.lineno}", node.name, node
        target = repo.resolve(mod, expr)
        owner = repo.module(target[0]) if target else None
        if owner and target and target[1] in owner.funcs:
            node = owner.funcs[target[1]]
            return f"{repo.rel(owner.path)}:{node.lineno}", node.name, node
        return f"{repo.rel(mod.path)}:{expr.lineno}", _dotted(expr), None

    def kind(node: ast.AST | None) -> str:
        if node is None:
            return "?"
        return "loop" if any(isinstance(n, ast.While) for n in ast.walk(node)) else "once"

    tasks = [n for n in ast.walk(mod.tree) if _call_name(n) == "create_task" and isinstance(n, ast.Call) and n.args]
    for call in sorted(tasks, key=lambda n: n.lineno):
        coro = call.args[0]
        if not isinstance(coro, ast.Call):
            continue
        where, func_name, node = site(coro.func)
        label = repo.const_str(mod, _kwarg(call, "name")) or func_name
        fan_out = [
            arg
            for n in (ast.walk(node) if node is not None else [])
            if _call_name(n) == "gather" and isinstance(n, ast.Call)
            for arg in n.args
            if isinstance(arg, ast.Call)
        ]
        if not fan_out:
            lines.append(f"{label}  {where}  {func_name}  [per-process, {kind(node)}]")
            continue
        gated = node is not None and any(isinstance(n, ast.Attribute) and "leader" in n.attr for n in ast.walk(node))
        lines.append(
            f"{label}  {where}  {func_name}  [per-process, {'waits for the leader lease' if gated else 'group'}]"
        )
        for child in fan_out:
            c_where, _c_name, c_node = site(child.func)
            tag = "leader-only" if gated else f"via {label}"
            lines.append(f"{_dotted(child.func)}  {c_where}  [{tag}, {kind(c_node)}]")

    bot_dir = repo.root / bot_package
    for path in sorted(bot_dir.rglob("*.py")) if bot_dir.is_dir() else []:
        bot_mod = repo.module(".".join(path.relative_to(repo.root).with_suffix("").parts))
        if bot_mod is None:
            continue
        owners: list[tuple[str, ast.AST]] = [("", bot_mod.tree)]
        owners += [(f"{n.name}.", n) for n in ast.walk(bot_mod.tree) if isinstance(n, ast.ClassDef)]
        for qual, owner in owners:
            for node in ast.iter_child_nodes(owner):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and _dotted(dec.func).endswith("tasks.loop"):
                        every = ", ".join(f"{kw.arg}={_dotted(kw.value)}" for kw in dec.keywords if kw.arg)
                        where = f"{repo.rel(path)}:{node.lineno}"
                        lines.append(f"{qual}{node.name}  {where}  [bot, tasks.loop {every}]")
    return lines


# ── tables (local TEST database) ──────────────────────────────────────────────


def is_scratch_schema(name: str) -> bool:
    """``users_s12345_0``-style schemas leased by the test suite."""
    return bool(_SCRATCH_RE.fullmatch(name))


def check_local_dsn(dsn: str) -> None:
    """Refuse anything but a localhost database: this tool must never read production."""
    try:
        from psycopg.conninfo import conninfo_to_dict

        params = conninfo_to_dict(dsn)
    except Exception as exc:
        raise EnvError(f"cannot parse TEST_DATABASE_URL: {type(exc).__name__}") from exc
    hosts = [str(params.get(key)) for key in ("host", "hostaddr") if params.get(key)] or ["localhost"]
    for host in hosts:
        if host not in ("localhost", "127.0.0.1"):
            raise EnvError(f"refusing to query host {host!r}: tables only reads localhost/127.0.0.1")


def resolve_test_dsn(root: Path, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    if env.get("TEST_DATABASE_URL"):
        return env["TEST_DATABASE_URL"]
    env_file = root / ".env"
    if env_file.is_file():
        try:
            from dotenv import dotenv_values

            value = dotenv_values(env_file).get("TEST_DATABASE_URL")
            if value:
                return value
        except ImportError:
            pass
    return DEFAULT_TEST_DSN


_COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.udt_name
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE t.table_type = 'BASE TABLE'
  AND c.table_schema <> 'information_schema' AND left(c.table_schema, 3) <> 'pg_'
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""
_PK_SQL = """
SELECT n.nspname, c.relname, a.attname
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE i.indisprimary
"""


def fetch_columns(dsn: str) -> list[tuple[str, str, str, str, bool]]:
    """(schema, table, column, type, is_pk) rows from a localhost database."""
    check_local_dsn(dsn)
    try:
        import psycopg
    except ImportError as exc:
        raise EnvError("psycopg is not installed (run through `uv run --frozen`)") from exc
    try:
        with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
            columns = conn.execute(_COLUMNS_SQL).fetchall()
            pks = {tuple(row) for row in conn.execute(_PK_SQL).fetchall()}
    except psycopg.Error as exc:
        reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        raise EnvError(f"test database unreachable ({reason}); is local PostgreSQL running?") from exc
    return [(s, t, c, typ, (s, t, c) in pks) for s, t, c, typ in columns]


def format_tables(rows: Iterable[tuple[str, str, str, str, bool]], flt: str = "", max_cols: int = 40) -> list[str]:
    """``schema.table: col type, ...`` (``*`` = primary key). The filter matches
    ``schema.table`` or a column name; scratch schemas are dropped."""
    needle = flt.lower()
    tables: dict[str, list[str]] = {}
    matched: set[str] = set()
    for schema, table, column, typ, is_pk in rows:
        if is_scratch_schema(schema):
            continue
        key = f"{schema}.{table}"
        typ = f"{typ[1:]}[]" if typ.startswith("_") else typ
        tables.setdefault(key, []).append(f"{column}{'*' if is_pk else ''} {typ}")
        if not needle or needle in key.lower() or needle in column.lower():
            matched.add(key)
    lines = []
    for key, cols in tables.items():
        if key not in matched:
            continue
        if max_cols and len(cols) > max_cols:
            cols = [*cols[:max_cols], f"(+{len(cols) - max_cols} cols)"]
        lines.append(f"{key}: {', '.join(cols)}")
    return lines


# ── output ────────────────────────────────────────────────────────────────────


def select(lines: Sequence[str], flt: str = "") -> list[str]:
    needle = flt.lower()
    return [line for line in lines if needle in line.lower()] if needle else list(lines)


def cap(lines: Sequence[str], max_lines: int) -> list[str]:
    if max_lines <= 0 or len(lines) <= max_lines:
        return list(lines)
    return [*lines[:max_lines], f"... {len(lines) - max_lines} more, narrow the filter"]


def run(command: str, flt: str, root: Path, *, max_methods: int = 12, max_cols: int = 40) -> tuple[list[str], int]:
    """Rows for a subcommand plus the unfiltered total (for the no-match message)."""
    if command == "routes":
        everything = format_routes(collect_routes(Repo(root)))
    elif command == "stores":
        stores = collect_stores(Repo(root))
        return format_stores(stores, flt, max_methods), len(stores)
    elif command == "sql":
        everything = collect_sql(root)
    elif command == "pages":
        everything = collect_pages(root)
    elif command == "loops":
        everything = collect_loops(Repo(root))
    elif command == "tables":
        rows = fetch_columns(resolve_test_dsn(root))
        return format_tables(rows, flt, max_cols), len(format_tables(rows))
    else:  # pragma: no cover - argparse restricts the choices
        raise EnvError(f"unknown command {command}")
    return select(everything, flt), len(everything)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codemap.py",
        description="Static index of routes, stores, SQL blocks, pages, background loops and tables.",
        epilog="Exit codes: 0 rows printed, 1 nothing matched, 2 usage or environment error.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("filter", nargs="?", default="", help="case-insensitive substring; matches any part of a row")
    common.add_argument("--max-lines", type=int, default=120, metavar="N", help="output cap (default 120, 0 = no cap)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    sub.add_parser("routes", parents=[common], help="METHOD  /served/path  file:line  handler")
    stores = sub.add_parser("stores", parents=[common], help="store/catalogue classes: file:line, schema, methods")
    stores.add_argument("--methods", type=int, default=12, metavar="N", help="methods listed per class (0 = all)")
    sub.add_parser("sql", parents=[common], help="block_name  file:line for the -- :name SQL sidecar blocks")
    sub.add_parser("pages", parents=[common], help="/path  ->  frontend page file  (lazy|eager)")
    sub.add_parser("loops", parents=[common], help="background tasks/loops: name  file:line  [scope, kind]")
    tables = sub.add_parser(
        "tables", parents=[common], help="schema.table: col type, ... from the local TEST database (* = primary key)"
    )
    tables.add_argument("--cols", type=int, default=40, metavar="N", help="columns listed per table (0 = all)")
    return parser


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=stream.encoding if stream.isatty() else "utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    try:
        lines, total = run(
            args.command,
            args.filter,
            root,
            max_methods=getattr(args, "methods", 12),
            max_cols=getattr(args, "cols", 40),
        )
    except EnvError as exc:
        print(f"codemap: {exc}", file=sys.stderr)
        return 2
    if not lines:
        print(f"codemap: no {args.command} match {args.filter!r} ({total} total)", file=sys.stderr)
        return 1
    print("\n".join(cap(lines, args.max_lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
