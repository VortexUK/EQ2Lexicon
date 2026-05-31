"""Tests for web/lib/executor.run_sync."""

from __future__ import annotations

import threading

import pytest

from backend.server.core.executor import run_sync


def _sync_add(a: int, b: int) -> int:
    return a + b


def _sync_kw(a: int, b: int = 0, c: int = 0) -> int:
    return a + b + c


def _capture_thread() -> int:
    return threading.get_ident()


@pytest.mark.asyncio
async def test_positional() -> None:
    assert await run_sync(_sync_add, 1, 2) == 3


@pytest.mark.asyncio
async def test_keyword() -> None:
    assert await run_sync(_sync_kw, 1, b=2, c=3) == 6


@pytest.mark.asyncio
async def test_runs_off_event_loop_thread() -> None:
    main_tid = threading.get_ident()
    other_tid = await run_sync(_capture_thread)
    assert main_tid != other_tid
