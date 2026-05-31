"""Tests for web/db.py helpers — focused on get_display_names_for_discord_ids."""

from __future__ import annotations

import pytest

from backend.server import db
from backend.server.db import get_display_names_for_discord_ids, upsert_user

_PATH = db.DB_PATH  # redirected to pytest tmpdir by conftest.py


async def _seed(discord_id: str, discord_name: str) -> None:
    await upsert_user(
        discord_id=discord_id,
        discord_name=discord_name,
        discord_username=discord_id,
        avatar=None,
        path=_PATH,
    )


# ---------------------------------------------------------------------------
# get_display_names_for_discord_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_list_returns_empty_dict():
    result = await get_display_names_for_discord_ids([], path=_PATH)
    assert result == {}


@pytest.mark.asyncio
async def test_missing_id_absent_from_result():
    """An id that doesn't exist in users returns no entry."""
    result = await get_display_names_for_discord_ids(["999999999999999999"], path=_PATH)
    assert result == {}


@pytest.mark.asyncio
async def test_present_id_returns_display_name():
    await _seed("111111111111111111", "Sihtric Ironhide")
    result = await get_display_names_for_discord_ids(["111111111111111111"], path=_PATH)
    assert result == {"111111111111111111": "Sihtric Ironhide"}


@pytest.mark.asyncio
async def test_mixed_present_and_missing():
    await _seed("222222222222222222", "Wuoshi Raider")
    result = await get_display_names_for_discord_ids(["222222222222222222", "000000000000000000"], path=_PATH)
    assert "222222222222222222" in result
    assert result["222222222222222222"] == "Wuoshi Raider"
    assert "000000000000000000" not in result


@pytest.mark.asyncio
async def test_multiple_present_ids():
    await _seed("333333333333333333", "Alice")
    await _seed("444444444444444444", "Bob")
    result = await get_display_names_for_discord_ids(["333333333333333333", "444444444444444444"], path=_PATH)
    assert result == {"333333333333333333": "Alice", "444444444444444444": "Bob"}
