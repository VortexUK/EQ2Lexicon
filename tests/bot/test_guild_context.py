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


@pytest.mark.asyncio
async def test_is_guild_officer_pins_the_link_world(monkeypatch):
    """The site's _officer_chars reads current_world() (request contextvar)
    — the bot must pin the LINK's world around the call, and reset after."""
    from backend.bot.guild_context import GuildContext, is_guild_officer
    from backend.server import server_context

    wuoshi = server_context.Server(
        world="Wuoshi", subdomain="wuoshi", display_name="Wuoshi", max_level=80, current_xpac="RoK", launch_dt=None
    )
    monkeypatch.setattr(server_context, "server_for_world", lambda w: wuoshi if w == "Wuoshi" else None)

    seen: dict = {}

    async def fake_officer_chars(discord_id, guild_name):
        seen["world"] = server_context.current_world()
        seen["args"] = (discord_id, guild_name)
        return {"menludiir"}

    monkeypatch.setattr("backend.server.api.guild._officer_chars", fake_officer_chars)
    ctx = GuildContext(world="Wuoshi", guild_name="Paragon", voice_channel_id=None, linked=True)

    assert await is_guild_officer("368755", ctx) is True
    assert seen["world"] == "Wuoshi"
    assert seen["args"] == ("368755", "Paragon")
    # Contextvar restored — the default server is back after the call.
    assert server_context.current_world() != "" and server_context._active_server.get() is None


@pytest.mark.asyncio
async def test_is_guild_officer_denies_on_failure_and_unlinked(monkeypatch):
    from backend.bot.guild_context import GuildContext, is_guild_officer

    async def boom(discord_id, guild_name):
        raise RuntimeError("census down")

    monkeypatch.setattr("backend.server.api.guild._officer_chars", boom)
    linked = GuildContext(world="Wuoshi", guild_name="Paragon", voice_channel_id=None, linked=True)
    unlinked = GuildContext(world="Wuoshi", guild_name=None, voice_channel_id=None, linked=False)
    assert await is_guild_officer("1", linked) is False  # failure -> deny, no raise
    assert await is_guild_officer("1", unlinked) is False
