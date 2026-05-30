"""Process-wide shared CensusClient lifecycle.

Audit BE-010: 18 sites hand-rolled ``CensusClient(...); try: ...; finally:
await client.close()``. Each invocation built a new ``aiohttp.ClientSession``
+ TraceConfig; each Census call paid a TLS handshake. ``aiohttp``'s own
docs warn against this pattern — a long-lived ``ClientSession`` is the
intended shape.

This module owns the singleton + its lifecycle. Two equivalent call shapes:

  async with shared_census_client() as c:
      char = await c.get_character(name, world)

  # Or, for one-line migration of a single `client = CensusClient(...)`:
  client = await get_shared_census_client()
  char = await client.get_character(name, world)
  # NB: do NOT await client.close() — the lifecycle is owned by this module.

The singleton is keyed by the running event loop, because:
  - pytest-asyncio creates a fresh loop per test
  - an aiohttp.ClientSession opened on a closed loop raises RuntimeError on
    next use
So a per-test-loop rebuild is necessary for the tests to stay green. In prod
the loop is created once at startup and never closed mid-process, so the
rebuild path is effectively dead.

Shutdown: the FastAPI lifespan (web/app.py) calls ``aclose_all()`` so the
process exits cleanly without aiohttp's "Unclosed client session" warning.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from census.client import CensusClient
from web.config import SERVICE_ID

_log = logging.getLogger(__name__)

# Loop → CensusClient. dict keyed on id(loop) so a stale (closed) loop
# entry GCs once the loop object itself goes (tests).
_clients: dict[int, CensusClient] = {}


async def get_shared_census_client() -> CensusClient:
    """Return the singleton CensusClient for the current running event loop.

    Creates it lazily on first call. Do NOT close the returned client; its
    lifecycle is owned by this module. The aiohttp session is bound to the
    running event loop — calling this from a different loop returns a
    different singleton (per-loop scoping).
    """
    loop = asyncio.get_running_loop()
    key = id(loop)
    client = _clients.get(key)
    if client is None:
        client = CensusClient(service_id=SERVICE_ID)
        _clients[key] = client
        _log.debug("[census-lifecycle] Created shared CensusClient for loop %d", key)
    return client


@asynccontextmanager
async def shared_census_client() -> AsyncIterator[CensusClient]:
    """Context-manager flavour. Preferred for new code.

    Idiomatically reads as ``async with shared_census_client() as c:`` so
    new contributors don't accidentally write ``await c.close()`` at the
    end — the context manager makes lifecycle ownership explicit (it
    belongs to this module, not the caller).
    """
    yield await get_shared_census_client()


async def aclose_all() -> None:
    """Close every per-loop singleton — called from the FastAPI lifespan
    shutdown handler so the process exits without aiohttp's "Unclosed client
    session" warning. Safe to call multiple times."""
    for key, client in list(_clients.items()):
        try:
            await client.close()
        except Exception:
            _log.exception("[census-lifecycle] Error closing CensusClient for loop %d", key)
        _clients.pop(key, None)


def _reset_for_test() -> None:
    """Clear the singleton map without calling close() — used by tests that
    swap the underlying ``CensusClient`` for a mock. The closed-loop entries
    GC naturally once the test's loop is collected."""
    _clients.clear()
