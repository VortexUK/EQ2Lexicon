"""scripts/tools/codemap.py: static route/store/sql/page/loop/table index over a miniature repo.

Pure: the fixture tree mirrors how the real app assembles routers (a ``_ROUTERS`` loop with
``prefix="/api"``, package routers, nested ``include_router``), and ``tables`` is fed fake rows.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
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


codemap = _load("codemap")

_FILES = {
    "backend/__init__.py": "",
    "backend/db_catalogue.py": """
        class SchemaBound:
            def __init__(self, schema):
                self.schema = schema

        class PgStoreBase(SchemaBound):
            def _db(self): ...

        class PgCatalogue(SchemaBound):
            def init_db(self): ...
    """,
    "backend/server/__init__.py": "",
    "backend/server/app.py": """
        import asyncio
        from fastapi import FastAPI
        from backend.server.api.act_triggers import router as act_router
        from backend.server.api.health import prewarm_health
        from backend.server.api.health import router as health_router
        from backend.server.api.parses import router as parses_router


        def create_app():
            async def lifespan(_app):
                from backend.server import census_health, refresh_queue

                async def _leader_singletons():
                    from backend.server.core import leader

                    if not await leader.wait_until_leader():
                        return
                    await asyncio.gather(refresh_queue.worker_loop(), _cleanup_loop())

                tasks = [
                    asyncio.create_task(prewarm_health(), name="prewarm-health"),
                    asyncio.create_task(census_health.poll_loop(), name="census-health-poll"),
                    asyncio.create_task(_leader_singletons(), name="leader-singletons"),
                ]
                yield

            async def _cleanup_loop():
                while True:
                    await asyncio.sleep(1)

            app = FastAPI(lifespan=lifespan)
            _ROUTERS = [
                health_router,
                parses_router,
                act_router,
            ]
            for _r in _ROUTERS:
                app.include_router(_r, prefix="/api")

            @app.get("/metrics", include_in_schema=False)
            def metrics_endpoint():
                return ""

            _STATIC_MOUNTS = [("/icons", 1), ("/aa-assets", 2)]
            for _mount_path, _dir in _STATIC_MOUNTS:
                app.mount(_mount_path, StaticFiles(directory=_dir), name="x")
            return app


        app = create_app()
    """,
    "backend/server/census_health.py": """
        async def poll_loop():
            while True:
                pass
    """,
    "backend/server/refresh_queue.py": """
        X = 1


        async def worker_loop():
            while True:
                pass
    """,
    "backend/server/api/__init__.py": "",
    "backend/server/api/health.py": """
        from fastapi import APIRouter

        router = APIRouter(tags=["health"])


        @router.get("/health")
        async def health():
            return {}


        async def prewarm_health():
            return None
    """,
    "backend/server/api/parses/__init__.py": """
        from fastapi import APIRouter

        router = APIRouter(prefix="/parses", tags=["parses"])

        from backend.server.api.parses import list as _list  # noqa: E402,F401
    """,
    "backend/server/api/parses/list.py": """
        from backend.server.api.parses import router  # the package-level router

        PARSE = "/{encounter_id}"


        @router.get("")
        async def list_parses():
            return []


        @router.delete(
            PARSE,
            status_code=204,
        )
        @limiter.limit("5/minute")
        async def delete_parse(encounter_id: int):
            return None
    """,
    "backend/server/api/act/__init__.py": """
        from fastapi import APIRouter

        from backend.server.api.act import triggers

        router = APIRouter()
        router.include_router(triggers.router, prefix="/act")
    """,
    "backend/server/api/act/triggers.py": """
        from fastapi import APIRouter

        router = APIRouter(prefix="/triggers")


        @router.api_route("/{trigger_id}", methods=["PUT", "PATCH"])
        async def update_trigger(trigger_id: int):
            return None


        @router.websocket("/live")
        async def live(ws):
            return None
    """,
    "backend/server/api/act_triggers.py": """
        from backend.server.api.act import router  # noqa: F401
    """,
    "backend/server/api/orphan.py": """
        from fastapi import APIRouter

        router = APIRouter()


        @router.post("/orphan")
        async def orphan():
            return None
    """,
    "backend/server/db/__init__.py": 'SCHEMA = "users"\n',
    "backend/server/db/users.py": """
        from backend.db_catalogue import PgStoreBase
        from backend.server.db import SCHEMA


        class UsersStore(PgStoreBase):
            def __init__(self, schema: str = SCHEMA) -> None:
                super().__init__(schema)

            async def upsert_user(self, discord_id):
                return None

            async def get_user(self, discord_id):
                return None

            def _private(self):
                return None

            @staticmethod
            def hash_name(name):
                return name
    """,
    "backend/server/db/users.sql": """
        -- Users domain
        -- :name upsert_user
        INSERT INTO users (id) VALUES (%s);

        -- :name get_user
        SELECT * FROM users WHERE id = %s;
    """,
    "backend/eq2db/zones.py": """
        from backend.db_catalogue import PgCatalogue

        SCHEMA = "zones"


        class ZoneCatalogue(PgCatalogue):
            READY_TABLE = "zones"

            def __init__(self, schema: str = SCHEMA) -> None:
                super().__init__(schema)

            def find_by_name(self, name):
                return None


        class EventZoneCatalogue(ZoneCatalogue):
            def list_by_event(self, event):
                return []


        class NotAStore:
            def nothing(self):
                return None
    """,
    "backend/bot/cogs/voice.py": """
        from discord.ext import tasks

        POLL_INTERVAL_S = 120


        class VoiceAttendance:
            @tasks.loop(seconds=POLL_INTERVAL_S)
            async def poll(self):
                return None
    """,
    "frontend/src/App.tsx": """
        import { lazy, Suspense } from 'react'
        import { Routes, Route } from 'react-router-dom'
        import HomePage from './pages/HomePage'
        import { CharacterSearchPage, GuildSearchPage } from './pages/SearchPage'
        import UserWidget from './components/UserWidget'

        const AdminPage         = lazy(() => import('./pages/AdminPage'))
        const GhostPage = lazy(() => import('./pages/GhostPage'))

        function App() {
          return (
            <Routes>
              <Route element={<Layout />}>
                <Route path="/" element={<HomePage />} />
                <Route path="/characters" element={<CharacterSearchPage />} />
                <Route path="/admin"   element={<AdminPage />} />
                <Route path="/raids/:name/:bossName"    element={<AdminPage />} />
                <Route path="/ghost" element={<GhostPage />} />
                <Route path="*" element={<Inline />} />
              </Route>
            </Routes>
          )
        }
    """,
    "frontend/src/pages/HomePage.tsx": "export default function HomePage() { return null }\n",
    "frontend/src/pages/SearchPage.tsx": "export function CharacterSearchPage() { return null }\n",
    "frontend/src/pages/AdminPage.tsx": "export default function AdminPage() { return null }\n",
}


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("repo")
    for rel, body in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = textwrap.dedent(body).lstrip("\n")
        # App.tsx in the real repo starts with a BOM; keep the fixture honest about that.
        path.write_text(text, encoding="utf-8-sig" if rel.endswith("App.tsx") else "utf-8")
    return root


def _source_line(root: Path, location: str) -> str:
    file, _, line = location.rpartition(":")
    return (root / file).read_text(encoding="utf-8").splitlines()[int(line) - 1].strip()


# ── routes ────────────────────────────────────────────────────────────────────


def test_routes_carry_the_real_served_path(tree):
    routes = codemap.collect_routes(codemap.Repo(tree))
    table = {(r.method, r.path): r for r in routes}

    assert set(table) == {
        ("GET", "/api/health"),
        ("GET", "/api/parses"),  # APIRouter(prefix=...) + a handler in a sibling module
        ("DELETE", "/api/parses/{encounter_id}"),  # path given through a module constant
        ("PUT,PATCH", "/api/act/triggers/{trigger_id}"),  # nested include_router(prefix=...) via a re-export
        ("WS", "/api/act/triggers/live"),
        ("GET", "/metrics"),  # registered straight on the app inside create_app()
        ("MOUNT", "/icons"),
        ("MOUNT", "/aa-assets"),
        ("POST", "/orphan"),
    }
    assert table[("GET", "/api/parses")].handler == "list_parses"
    assert table[("GET", "/api/parses")].file == "backend/server/api/parses/list.py"
    assert table[("POST", "/orphan")].note == "[not included in app]"
    assert all(not r.note for key, r in table.items() if key != ("POST", "/orphan"))


def test_route_line_points_at_the_route_decorator(tree):
    routes = {r.handler: r for r in codemap.collect_routes(codemap.Repo(tree))}
    for handler in ("health", "delete_parse", "update_trigger", "metrics_endpoint"):
        r = routes[handler]
        assert _source_line(tree, f"{r.file}:{r.line}").startswith(("@router.", "@app.")), handler


def test_format_routes_and_filter(tree):
    lines = codemap.format_routes(codemap.collect_routes(codemap.Repo(tree)))
    hit = codemap.select(lines, "PARSES")
    assert hit == [
        "GET    /api/parses  backend/server/api/parses/list.py:6  list_parses",
        "DELETE /api/parses/{encounter_id}  backend/server/api/parses/list.py:11  delete_parse",
    ]
    assert codemap.select(lines, "delete ") == [hit[1]]
    assert codemap.select(lines, "orphan")[0].endswith("orphan  [not included in app]")


# ── stores ────────────────────────────────────────────────────────────────────


def test_stores_resolve_schema_base_and_public_methods(tree):
    stores = {s.name: s for s in codemap.collect_stores(codemap.Repo(tree))}

    assert set(stores) == {"UsersStore", "ZoneCatalogue", "EventZoneCatalogue"}  # bases + NotAStore excluded
    users = stores["UsersStore"]
    assert (users.schema, users.base, users.file) == ("users", "PgStoreBase", "backend/server/db/users.py")
    assert [m for m, _ in users.methods] == ["upsert_user", "get_user", "hash_name"]
    assert _source_line(tree, f"{users.file}:{users.line}") == "class UsersStore(PgStoreBase):"
    assert stores["ZoneCatalogue"].schema == "zones"
    # derives through another repo class and has no __init__: schema + base are inherited
    assert (stores["EventZoneCatalogue"].schema, stores["EventZoneCatalogue"].base) == ("zones", "PgCatalogue")


def test_format_stores_filter_matches_methods_and_caps_the_list(tree):
    stores = codemap.collect_stores(codemap.Repo(tree))

    by_method = codemap.format_stores(stores, "GET_USER")
    assert len(by_method) == 1
    line = by_method[0]
    assert line.startswith(
        "UsersStore  backend/server/db/users.py:5  schema=users  base=PgStoreBase  methods: get_user:"
    )
    assert line.endswith("upsert_user, hash_name")

    capped = codemap.format_stores(stores, "usersstore", max_methods=1)
    assert capped[0].endswith("methods: upsert_user, (+2 more)")
    assert len(codemap.format_stores(stores)) == 3
    assert codemap.format_stores(stores, "nomatch") == []


# ── sql / pages / loops ───────────────────────────────────────────────────────


def test_sql_blocks_with_line_numbers(tree):
    assert codemap.collect_sql(tree) == [
        "upsert_user  backend/server/db/users.sql:2",
        "get_user  backend/server/db/users.sql:5",
    ]


def test_pages_resolve_files_and_load_mode(tree):
    assert codemap.collect_pages(tree) == [
        "/  ->  frontend/src/pages/HomePage.tsx  (eager)",
        "/characters  ->  frontend/src/pages/SearchPage.tsx  (eager, CharacterSearchPage)",
        "/admin  ->  frontend/src/pages/AdminPage.tsx  (lazy)",
        "/raids/:name/:bossName  ->  frontend/src/pages/AdminPage.tsx  (lazy)",
        "/ghost  ->  frontend/src/pages/GhostPage (file not found)  (lazy)",
        "*  ->  frontend/src/App.tsx  (eager, Inline defined inline)",
    ]


def test_pages_without_app_file_is_an_environment_error(tmp_path):
    with pytest.raises(codemap.EnvError):
        codemap.collect_pages(tmp_path)


def test_loops_split_per_process_leader_only_and_bot(tree):
    assert codemap.collect_loops(codemap.Repo(tree)) == [
        "prewarm-health  backend/server/api/health.py:11  prewarm_health  [per-process, once]",
        "census-health-poll  backend/server/census_health.py:1  poll_loop  [per-process, loop]",
        "leader-singletons  backend/server/app.py:13  _leader_singletons  [per-process, waits for the leader lease]",
        "refresh_queue.worker_loop  backend/server/refresh_queue.py:4  [leader-only, loop]",
        "_cleanup_loop  backend/server/app.py:27  [leader-only, loop]",
        "VoiceAttendance.poll  backend/bot/cogs/voice.py:8  [bot, tasks.loop seconds=POLL_INTERVAL_S]",
    ]


# ── tables ────────────────────────────────────────────────────────────────────

_ROWS = [
    ("users", "claims", "id", "int4", True),
    ("users", "claims", "discord_id", "text", False),
    ("users", "claims", "ranks", "_int4", False),
    ("users_s4242_0", "claims", "id", "int4", True),
    ("zones", "zones", "id", "int8", True),
    ("zones", "zones", "name", "text", False),
]


def test_scratch_schemas_are_recognised():
    assert codemap.is_scratch_schema("users_s12345_0")
    assert codemap.is_scratch_schema("parses_s9_12")
    assert not codemap.is_scratch_schema("users")
    assert not codemap.is_scratch_schema("raid_strategies")


def test_format_tables_marks_keys_arrays_and_drops_scratch():
    assert codemap.format_tables(_ROWS) == [
        "users.claims: id* int4, discord_id text, ranks int4[]",
        "zones.zones: id* int8, name text",
    ]


def test_format_tables_filters_on_table_or_column_and_caps_columns():
    assert codemap.format_tables(_ROWS, "ZONES.") == ["zones.zones: id* int8, name text"]
    assert codemap.format_tables(_ROWS, "discord") == ["users.claims: id* int4, discord_id text, ranks int4[]"]
    assert codemap.format_tables(_ROWS, "claims", max_cols=1) == ["users.claims: id* int4, (+2 cols)"]
    assert codemap.format_tables(_ROWS, "nomatch") == []


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres:postgres@localhost:5432/eq2lexicon_test",
        "postgresql://postgres:postgres@127.0.0.1/eq2lexicon_test",
        "dbname=eq2lexicon_test user=postgres",
    ],
)
def test_local_dsn_is_accepted(dsn):
    codemap.check_local_dsn(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@aws-0-eu-west-2.pooler.supabase.com:5432/postgres",
        "postgresql://u:p@localhost,db.example.com/postgres",
        "host=localhost hostaddr=10.0.0.5 dbname=x",
    ],
)
def test_remote_dsn_is_refused_without_echoing_credentials(dsn):
    with pytest.raises(codemap.EnvError) as excinfo:
        codemap.check_local_dsn(dsn)
    assert "u:p" not in str(excinfo.value)


def test_test_dsn_resolution_order(tmp_path):
    assert codemap.resolve_test_dsn(tmp_path, {}) == codemap.DEFAULT_TEST_DSN
    (tmp_path / ".env").write_text("TEST_DATABASE_URL=postgresql://a@localhost/from_file\n", encoding="utf-8")
    assert codemap.resolve_test_dsn(tmp_path, {}) == "postgresql://a@localhost/from_file"
    env = {"TEST_DATABASE_URL": "postgresql://a@localhost/from_env"}
    assert codemap.resolve_test_dsn(tmp_path, env) == "postgresql://a@localhost/from_env"


# ── output cap + main ─────────────────────────────────────────────────────────


def test_cap_adds_the_narrow_the_filter_trailer():
    lines = [f"row {i}" for i in range(10)]
    assert codemap.cap(lines, 3) == ["row 0", "row 1", "row 2", "... 7 more, narrow the filter"]
    assert codemap.cap(lines, 10) == lines
    assert codemap.cap(lines, 0) == lines


def test_main_prints_matches_and_caps(tree, capsys):
    assert codemap.main(["routes", "parses"], root=tree) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "GET    /api/parses  backend/server/api/parses/list.py:6  list_parses",
        "DELETE /api/parses/{encounter_id}  backend/server/api/parses/list.py:11  delete_parse",
    ]

    assert codemap.main(["routes", "--max-lines", "2"], root=tree) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 3 and out[-1] == "... 7 more, narrow the filter"


def test_main_no_match_exits_1_with_one_line(tree, capsys):
    assert codemap.main(["sql", "nomatch"], root=tree) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "codemap: no sql match 'nomatch' (2 total)"


def test_main_tables_uses_fetched_rows(tree, capsys, monkeypatch):
    monkeypatch.setattr(codemap, "fetch_columns", lambda dsn: _ROWS)
    assert codemap.main(["tables", "zones"], root=tree) == 0
    assert capsys.readouterr().out.strip() == "zones.zones: id* int8, name text"


def test_main_tables_refuses_a_remote_database_before_connecting(tree, capsys, monkeypatch):
    import psycopg

    def no_connect(*args, **kwargs):
        raise AssertionError("must not connect to a non-local host")

    monkeypatch.setattr(psycopg, "connect", no_connect)
    remote = "postgresql://user:hunter2@db.example.com:5432/postgres"
    monkeypatch.setattr(codemap, "resolve_test_dsn", lambda root: remote)
    assert codemap.main(["tables"], root=tree) == 2
    err = capsys.readouterr().err
    assert err.startswith("codemap: refusing to query host 'db.example.com'")
    assert "hunter2" not in err


def test_main_tables_unreachable_database_is_one_line_exit_2(tree, capsys, monkeypatch):
    def unreachable(dsn):
        raise codemap.EnvError("test database unreachable (connection refused); is local PostgreSQL running?")

    monkeypatch.setattr(codemap, "fetch_columns", unreachable)
    assert codemap.main(["tables"], root=tree) == 2
    err = capsys.readouterr().err.strip()
    assert err == "codemap: test database unreachable (connection refused); is local PostgreSQL running?"
