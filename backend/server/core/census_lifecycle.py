"""One shared CensusClient (aiohttp session) per event loop; callers must not
close it; ``aclose_all()`` runs at lifespan shutdown.

  async with shared_census_client() as c:
      char = await c.get_character(name, world)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from backend.census.client import CensusClient
from backend.server.config import SERVICE_ID

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
