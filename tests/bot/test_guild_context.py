"""backend/bot/guild_context.py — per-Discord-guild world/guild resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.bot.guild_context import FALLBACK_WORLD, resolve_guild_context
from backend.server.db import init_db
from backend.server.db.discord_links import store as links_db
from tests.fixtures.users_db import point_users_db_at


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)


@pytest.mark.asyncio
async def test_dm_context_falls_back():
    ctx = await resolve_guild_context(None)
    assert (ctx.world, ctx.guild_name, ctx.linked) == (FALLBACK_WORLD, None, False)


@pytest.mark.asyncio
async def test_unknown_guild_falls_back():
    ctx = await resolve_guild_context(123456789)
    assert (ctx.world, ctx.guild_name, ctx.linked) == (FALLBACK_WORLD, None, False)
    assert FALLBACK_WORLD == "Wuoshi"  # explicit user decision, 2026-09-04


@pytest.mark.asyncio
async def test_linked_guild_resolves():
    await links_db.upsert_link("42", "Wuoshi", "Paragon", linked_by="u1")
    await links_db.set_voice_channel("42", "999")
    ctx = await resolve_guild_context(42)
    assert (ctx.world, ctx.guild_name, ctx.voice_channel_id, ctx.linked) == ("Wuoshi", "Paragon", "999", True)


@pytest.mark.asyncio
async def test_missing_table_degrades_to_fallback(tmp_path, monkeypatch):
    """The bot may race the web lifespan's init_db on a fresh deploy — a
    missing registry table must resolve to the fallback, never crash."""
    empty = tmp_path / "empty.db"
    empty.touch()
    monkeypatch.setattr(links_db, "path", empty)
    ctx = await resolve_guild_context(42)
    assert (ctx.world, ctx.linked) == (FALLBACK_WORLD, False)
