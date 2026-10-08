"""/lexicon link requires the caller to be an officer of the EQ2 guild on the
site (an approved claim on an officer-ranked roster member), not merely
manage_guild in some Discord server — a link routes that server's voice
attendance into the guild's sessions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.bot.cogs import lexicon

_MEMBERS = [
    SimpleNamespace(name="Menludiir", rank_id=0),
    SimpleNamespace(name="Sihtric", rank_id=1),
    SimpleNamespace(name="Pleb", rank_id=4),
]


def _claims(*names: str) -> dict:
    return {"approved": [{"character_name": n} for n in names], "pending": None}


@pytest.mark.asyncio
async def test_officer_claim_passes_and_member_claim_does_not():
    with patch("backend.server.db.get_active_claims", new=AsyncMock(return_value=_claims("sihtric"))):
        assert await lexicon._caller_is_guild_officer("u1", "Varsoon", _MEMBERS) is True
    with patch("backend.server.db.get_active_claims", new=AsyncMock(return_value=_claims("Pleb"))):
        assert await lexicon._caller_is_guild_officer("u1", "Varsoon", _MEMBERS) is False
    with patch("backend.server.db.get_active_claims", new=AsyncMock(return_value=_claims())):
        assert await lexicon._caller_is_guild_officer("u1", "Varsoon", _MEMBERS) is False


@pytest.mark.asyncio
async def test_site_admin_passes_without_a_claim():
    with (
        patch("backend.server.auth_deps.ADMIN_IDS", frozenset({"admin-9"})),
        patch("backend.server.db.get_active_claims", new=AsyncMock(side_effect=AssertionError("not called"))),
    ):
        assert await lexicon._caller_is_guild_officer("admin-9", "Varsoon", _MEMBERS) is True


@pytest.mark.asyncio
async def test_link_command_refuses_non_officers_and_links_for_officers():
    bot = SimpleNamespace(
        census=SimpleNamespace(get_guild=AsyncMock(return_value=SimpleNamespace(name="Exordium", members=_MEMBERS)))
    )
    cog = lexicon.LexiconCog(bot)  # type: ignore[arg-type]
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        guild_id=123,
        user=SimpleNamespace(id=456),
    )
    world = SimpleNamespace(value="Varsoon")
    upsert = AsyncMock()

    with (
        patch.object(lexicon, "_caller_is_guild_officer", new=AsyncMock(return_value=False)),
        patch.object(lexicon.links_store, "upsert_link", new=upsert),
    ):
        await cog.link.callback(cog, interaction, world, "Exordium")  # type: ignore[attr-defined]
    upsert.assert_not_awaited()
    assert "Only an officer" in interaction.followup.send.await_args.args[0]

    with (
        patch.object(lexicon, "_caller_is_guild_officer", new=AsyncMock(return_value=True)),
        patch.object(lexicon.links_store, "upsert_link", new=upsert),
    ):
        await cog.link.callback(cog, interaction, world, "Exordium")  # type: ignore[attr-defined]
    upsert.assert_awaited_once_with("123", "Varsoon", "Exordium", "456")
