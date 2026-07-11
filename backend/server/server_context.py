"""Per-request active-server resolution.

A single deployment serves multiple EQ2 servers, one per subdomain
(varsoon.eq2lexicon.com / wuoshi.eq2lexicon.com). Middleware resolves the
request's Host to the active server and stores it on a contextvar; the rest of
the code reads current_world()/current_server() instead of a fixed env world.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from contextvars import ContextVar, Token
from dataclasses import dataclass
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from backend.census.config import WORLD as _DEFAULT_WORLD
from backend.server import db
from backend.server.core.request_context import world_var as _logging_world_var


@dataclass(frozen=True)
class Server:
    world: str
    subdomain: str
    display_name: str
    max_level: int
    current_xpac: str | None
    launch_dt: str | None
    is_default: bool = False


_by_subdomain: dict[str, Server] = {}
_by_world: dict[str, Server] = {}

_active_server: ContextVar[Server | None] = ContextVar("active_server", default=None)

_ALLOW_OVERRIDE = os.getenv("ENV", "dev").lower() not in ("prod", "production")


def _to_server(row: dict) -> Server:
    return Server(
        world=row["world"],
        subdomain=row["subdomain"],
        display_name=row["display_name"],
        max_level=row["max_level"],
        current_xpac=row["current_xpac"],
        launch_dt=row["launch_dt"],
        is_default=bool(row.get("is_default", False)),
    )


def load_registry() -> None:
    """(Re)load the servers registry from the DB. Call at startup + after edits."""
    rows = db.list_servers_sync()
    _by_subdomain.clear()
    _by_world.clear()
    for row in rows:
        srv = _to_server(row)
        _by_subdomain[srv.subdomain.lower()] = srv
        _by_world[srv.world] = srv


def default_server() -> Server:
    # 1. Prefer the admin-chosen default (is_default=True in the registry).
    for srv in _by_world.values():
        if srv.is_default:
            return srv
    # 2. Fall back to the EQ2_WORLD-matching server.
    srv = _by_world.get(_DEFAULT_WORLD)
    if srv is not None:
        return srv
    # 3. First server in the registry (alphabetical by insertion order).
    if _by_world:
        return next(iter(_by_world.values()))
    # 4. Synthesised fallback when the registry is completely empty.
    return Server(_DEFAULT_WORLD, _DEFAULT_WORLD.lower(), _DEFAULT_WORLD, 50, None, None)


def _subdomain_of(host: str) -> str:
    host = (host or "").split(":")[0].strip().lower()
    if not host:
        return ""
    return host.split(".")[0]


def resolve_host(host: str, override: str | None = None) -> Server:
    """Resolve a Host header (and optional non-prod override) to a Server."""
    if override and _ALLOW_OVERRIDE:
        srv = _by_subdomain.get(override.lower()) or _by_world.get(override)
        if srv is not None:
            return srv
    srv = _by_subdomain.get(_subdomain_of(host))
    return srv if srv is not None else default_server()


def current_server() -> Server:
    return _active_server.get() or default_server()


def current_world() -> str:
    return current_server().world


def set_active_server(server: Server) -> Token[Server | None]:
    return _active_server.set(server)


def reset_active_server(token: Token[Server | None]) -> None:
    _active_server.reset(token)


@contextlib.contextmanager
def active_server(record: Server) -> Iterator[None]:
    """Push ``record`` onto the per-request server contextvar; pop on exit."""
    token = _active_server.set(record)
    try:
        yield
    finally:
        _active_server.reset(token)


def get_server() -> Server:
    """FastAPI dependency: the active server for the request."""
    return current_server()


def list_public_servers() -> list[dict]:
    """Public server list for the frontend switcher."""
    return [{"world": s.world, "subdomain": s.subdomain, "display_name": s.display_name} for s in _by_world.values()]


class ServerContextMiddleware:
    """Pure-ASGI middleware: resolve Host -> active server for the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        override = headers.get("x-server")
        if override is None:
            qs = scope.get("query_string", b"").decode()
            override = parse_qs(qs).get("server", [None])[0]
        server = resolve_host(host, override)
        world_token = _logging_world_var.set(server.world)
        try:
            with active_server(server):
                await self.app(scope, receive, send)
        finally:
            _logging_world_var.reset(world_token)
