"""discord_guild_links store — the bot's per-Discord-guild registry."""

from __future__ import annotations

from pathlib import Path

import pytest

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


_GID = "648253204760625160"


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip():
    await links_db.upsert_link(_GID, "Wuoshi", "Paragon", linked_by="u1")
    link = await links_db.get_link(_GID)
    assert link is not None
    assert (link["world"], link["guild_name"], link["linked_by"]) == ("Wuoshi", "Paragon", "u1")
    assert link["voice_channel_id"] is None


@pytest.mark.asyncio
async def test_relink_updates_mapping_but_preserves_voice_channel():
    await links_db.upsert_link(_GID, "Wuoshi", "Paragon", linked_by="u1")
    assert await links_db.set_voice_channel(_GID, "111222333") is True
    # An officer fixing the guild name must not lose the voice config.
    await links_db.upsert_link(_GID, "Varsoon", "Exordium", linked_by="u2")
    link = await links_db.get_link(_GID)
    assert (link["world"], link["guild_name"], link["linked_by"]) == ("Varsoon", "Exordium", "u2")
    assert link["voice_channel_id"] == "111222333"


@pytest.mark.asyncio
async def test_set_voice_channel_requires_link():
    assert await links_db.set_voice_channel("999", "111") is False


@pytest.mark.asyncio
async def test_clear_voice_channel():
    await links_db.upsert_link(_GID, "Wuoshi", "Paragon", linked_by="u1")
    await links_db.set_voice_channel(_GID, "111222333")
    assert await links_db.set_voice_channel(_GID, None) is True
    link = await links_db.get_link(_GID)
    assert link["voice_channel_id"] is None


@pytest.mark.asyncio
async def test_list_voice_links_filters_unconfigured():
    await links_db.upsert_link(_GID, "Wuoshi", "Paragon", linked_by="u1")
    await links_db.upsert_link("222", "Wuoshi", "Otherguild", linked_by="u1")
    await links_db.set_voice_channel(_GID, "111222333")
    rows = await links_db.list_voice_links()
    assert [r["discord_guild_id"] for r in rows] == [_GID]
    assert rows[0]["voice_channel_id"] == "111222333"


@pytest.mark.asyncio
async def test_delete_link():
    await links_db.upsert_link(_GID, "Wuoshi", "Paragon", linked_by="u1")
    assert await links_db.delete_link(_GID) is True
    assert await links_db.get_link(_GID) is None
    assert await links_db.delete_link(_GID) is False
