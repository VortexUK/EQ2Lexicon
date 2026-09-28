"""Guild settings API — public read, LEADER-or-admin write.

The only switch today is ``officers_can_delete_parses``. It exists because
guilds have drama: an officer who leaves in a huff (or, on 2026-09-27, one
who just mis-read a delete button) could remove the guild's whole parse
history. The guild leader — Census rank_id 0, distinct from the other
officer ranks for the first time here — decides whether officers get that
power at all. Uploaders can always delete their OWN uploads; admins are
never gated.

Read is public: the value is not sensitive (it is already observable from
which trash buttons /parses renders) and the raid-schedule GET set the
precedent. Write mirrors raid_schedule's officer-gated PUT, one rank
stricter.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.server.api.guild import _leader_chars, _validate_guild_name
from backend.server.auth_deps import is_admin
from backend.server.core.audit_log import audit_log
from backend.server.db import get_display_names_for_discord_ids
from backend.server.db.guild_settings import store as guild_settings_db
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["guild"])


class GuildSettingsResponse(BaseModel):
    officers_can_delete_parses: bool
    updated_at: int | None = None
    updated_by_name: str | None = None


class GuildSettingsInput(BaseModel):
    officers_can_delete_parses: bool


async def _to_response(settings: dict) -> GuildSettingsResponse:
    name: str | None = None
    if settings.get("updated_by"):
        names = await get_display_names_for_discord_ids([settings["updated_by"]])
        name = names.get(settings["updated_by"])
    return GuildSettingsResponse(
        officers_can_delete_parses=settings["officers_can_delete_parses"],
        updated_at=settings.get("updated_at"),
        updated_by_name=name,
    )


@router.get("/guild/{guild_name}/settings", response_model=GuildSettingsResponse)
async def get_guild_settings(guild_name: str) -> GuildSettingsResponse:
    """Public — anyone may read a guild's switches."""
    _validate_guild_name(guild_name)
    return await _to_response(await guild_settings_db.get_settings(current_world(), guild_name))


@router.put("/guild/{guild_name}/settings", response_model=GuildSettingsResponse)
@limiter.limit("10/minute")
async def put_guild_settings(request: Request, guild_name: str, body: GuildSettingsInput) -> GuildSettingsResponse:
    """Guild leader (rank 0) or admin only."""
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_admin(user) and not await _leader_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Guild leader access required")

    world = current_world()
    stored = await guild_settings_db.upsert_settings(
        world,
        guild_name,
        officers_can_delete_parses=body.officers_can_delete_parses,
        updated_by=user["id"],
    )
    audit_log(
        "guild_settings_updated",
        actor=user["id"],
        guild=guild_name,
        officers_can_delete_parses=body.officers_can_delete_parses,
    )
    return await _to_response(stored)
