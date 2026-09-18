"""Manual Census refresh queue — mitigations + worker lifecycle + routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.server import refresh_queue

_WORLD = "Varsoon"


@pytest.fixture(autouse=True)
def _fresh_queue():
    refresh_queue._reset_for_test()
    with patch("backend.server.refresh_queue.census_health.is_down", return_value=False):
        yield
    refresh_queue._reset_for_test()


def test_enqueue_sets_status_and_is_idempotent():
    r1 = refresh_queue.enqueue("character", "Menlu", _WORLD, "u1")
    assert r1 == {"queued": True, "already_queued": False}
    assert refresh_queue.status("character", "MENLU", _WORLD) == {"queued": True, "running": False}
    # Re-clicking while queued is a no-op, not an error.
    r2 = refresh_queue.enqueue("character", "menlu", _WORLD, "u2")
    assert r2["already_queued"] is True


def test_one_job_per_user():
    refresh_queue.enqueue("character", "Menlu", _WORLD, "u1")
    with pytest.raises(refresh_queue.Refused) as exc:
        refresh_queue.enqueue("guild", "Exordium", _WORLD, "u1")
    assert exc.value.reason == "user_has_queued"


def test_entity_hourly_cap():
    # Each enqueue must complete (drain) before the next counts — simulate by
    # clearing pending between calls; the HISTORY is what the cap reads.
    for i in range(refresh_queue.PER_ENTITY_PER_HOUR):
        refresh_queue.enqueue("character", "Menlu", _WORLD, f"u{i}")
        refresh_queue._pending.clear()
        refresh_queue._by_user.clear()
        while not refresh_queue._queue.empty():
            refresh_queue._queue.get_nowait()
    with pytest.raises(refresh_queue.Refused) as exc:
        refresh_queue.enqueue("character", "Menlu", _WORLD, "u9")
    assert exc.value.reason == "entity_rate"


def test_census_down_refused():
    with (
        patch("backend.server.refresh_queue.census_health.is_down", return_value=True),
        pytest.raises(refresh_queue.Refused) as exc,
    ):
        refresh_queue.enqueue("character", "Menlu", _WORLD, "u1")
    assert exc.value.reason == "census_down"


@pytest.mark.asyncio
async def test_worker_processes_and_clears_state():
    ran: list[tuple[str, str]] = []

    async def _fake_char_refresh(name: str, world: str) -> None:
        ran.append((name, world))

    with (
        patch("backend.server.refresh_queue.census_refresh.run_character_refresh_now", new=_fake_char_refresh),
        patch("backend.server.refresh_queue.WORKER_PACING_S", 0),
    ):
        refresh_queue.enqueue("character", "Menlu", _WORLD, "u1")
        worker = asyncio.create_task(refresh_queue.worker_loop())
        try:
            for _ in range(100):
                if not refresh_queue.status("character", "Menlu", _WORLD)["queued"]:
                    break
                await asyncio.sleep(0.01)
        finally:
            worker.cancel()
    assert ran == [("Menlu", _WORLD)]
    assert refresh_queue.status("character", "Menlu", _WORLD) == {"queued": False, "running": False}
    # The user's one-job slot is freed — they can queue again.
    assert refresh_queue.enqueue("character", "Menlu", _WORLD, "u1")["queued"] is True


@pytest.mark.asyncio
async def test_worker_failure_still_clears_state():
    with (
        patch(
            "backend.server.refresh_queue.census_refresh.run_guild_refresh_now",
            new=AsyncMock(side_effect=RuntimeError("census exploded")),
        ),
        patch("backend.server.refresh_queue.WORKER_PACING_S", 0),
    ):
        refresh_queue.enqueue("guild", "Exordium", _WORLD, "u1")
        worker = asyncio.create_task(refresh_queue.worker_loop())
        try:
            for _ in range(100):
                if not refresh_queue.status("guild", "Exordium", _WORLD)["queued"]:
                    break
                await asyncio.sleep(0.01)
        finally:
            worker.cancel()
    assert refresh_queue.status("guild", "Exordium", _WORLD) == {"queued": False, "running": False}
