"""Tests for the shared CensusClient lifecycle (web/lib/census_lifecycle)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from backend.server.core import census_lifecycle


@pytest.fixture(autouse=True)
async def _reset() -> AsyncGenerator[None]:
    """Close any open client sessions on the running loop before AND after
    each test. Critical for CI on Linux: aiohttp's __del__ on a leaked
    session calls logger.error('Unclosed client session') during GC at
    process exit — by which time pytest has closed stdout, raising
    ValueError: I/O operation on closed file and failing the test session.
    Closing properly here keeps the process exit clean."""
    await census_lifecycle.aclose_all()
    yield
    await census_lifecycle.aclose_all()


@pytest.mark.asyncio
async def test_get_shared_returns_same_instance_within_loop() -> None:
    c1 = await census_lifecycle.get_shared_census_client()
    c2 = await census_lifecycle.get_shared_census_client()
    assert c1 is c2


@pytest.mark.asyncio
async def test_context_manager_yields_shared() -> None:
    flat = await census_lifecycle.get_shared_census_client()
    async with census_lifecycle.shared_census_client() as ctx:
        assert ctx is flat


@pytest.mark.asyncio
async def test_aclose_all_clears_map() -> None:
    await census_lifecycle.get_shared_census_client()
    await census_lifecycle.aclose_all()
    assert census_lifecycle._clients == {}


def test_per_loop_isolation() -> None:
    """Two different event loops get two different singletons. Bound to id(loop)
    so the second loop's call doesn't reuse the first loop's aiohttp session.

    Each loop's session is closed via aclose_all() before the loop is closed —
    otherwise the aiohttp ClientSession leaks to GC and fires its 'Unclosed
    client session' destructor warning at process exit, which fails CI on
    Linux (see _reset fixture docstring)."""

    async def _get() -> int:
        return id(await census_lifecycle.get_shared_census_client())

    loop1 = asyncio.new_event_loop()
    loop2 = asyncio.new_event_loop()
    try:
        c1_id = loop1.run_until_complete(_get())
        c2_id = loop2.run_until_complete(_get())
        assert c1_id != c2_id
    finally:
        # Close the sessions on each loop BEFORE closing the loop itself.
        # aclose_all() iterates _clients (which has both entries now);
        # running it on each loop closes that loop's session and pops it.
        loop1.run_until_complete(census_lifecycle.aclose_all())
        loop2.run_until_complete(census_lifecycle.aclose_all())
        loop1.close()
        loop2.close()
