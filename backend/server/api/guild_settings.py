"""Guild settings API — public read, LEADER-or-admin write.

Leader means Census rank_id 0 (stricter than the officer ranks). Switches:
``officers_can_delete_parses`` gates officer parse deletion only (uploaders
can always delete their own uploads, admins are never gated), and
``officer_rank_ids`` says which Census ranks the site treats as officers for
this guild (default 0 and 1; rank 0 is always included).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.server.api.guild import _LEADER_RANK, _leader_chars, _validate_guild_name, invalidate_officer_ranks
from backend.server.auth_deps import is_admin
from backend.server.constants import OFFICER_RANK_IDS
from backend.server.core.audit_log import audit_log
from backend.server.db import get_display_names_for_discord_ids
from backend.server.db.guild_settings import store as guild_settings_db
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["guild"])


#: Census guilds have at most eight ranks (ids 0..7); allow headroom.
_MAX_RANK_ID = 15


class GuildSettingsResponse(BaseModel):
    officers_can_delete_parses: bool
    # Effective officer ranks (Census rank ids; 0 = leader, always included)
    # and whether the leader set them explicitly or the site default applies.
    officer_rank_ids: list[int] = sorted(OFFICER_RANK_IDS)
    officer_rank_ids_custom: bool = False
    updated_at: int | None = None
    updated_by_name: str | None = None


class GuildSettingsInput(BaseModel):
    officers_can_delete_parses: bool
    # Omit / null = keep the site default. Rank 0 is added server-side.
    officer_rank_ids: list[int] | None = Field(default=None, max_length=_MAX_RANK_ID + 1)


def _normalise_rank_ids(ids: list[int] | None) -> list[int] | None:
    if ids is None:
        return None
    cleaned = {int(i) for i in ids}
    if any(i < 0 or i > _MAX_RANK_ID for i in cleaned):
        raise HTTPException(status_code=400, detail=f"officer_rank_ids must be between 0 and {_MAX_RANK_ID}")
    cleaned.add(_LEADER_RANK)  # the leader is always an officer
    return sorted(cleaned)


async def _to_response(settings: dict, *, include_names: bool = True) -> GuildSettingsResponse:
    name: str | None = None
    if include_names and settings.get("updated_by"):
        names = await get_display_names_for_discord_ids([settings["updated_by"]])
        name = names.get(settings["updated_by"])
    custom = settings.get("officer_rank_ids")
    return GuildSettingsResponse(
        officers_can_delete_parses=settings["officers_can_delete_parses"],
        officer_rank_ids=sorted(custom) if custom is not None else sorted(OFFICER_RANK_IDS),
        officer_rank_ids_custom=custom is not None,
        updated_at=settings.get("updated_at"),
        updated_by_name=name,
    )


@router.get("/guild/{guild_name}/settings", response_model=GuildSettingsResponse)
async def get_guild_settings(request: Request, guild_name: str) -> GuildSettingsResponse:
    """Public — anyone may read a guild's switches. The officer's Discord
    display name is only shown to signed-in readers."""
    _validate_guild_name(guild_name)
    settings = await guild_settings_db.get_settings(current_world(), guild_name)
    return await _to_response(settings, include_names=bool(request.session.get("user")))


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
    rank_ids = _normalise_rank_ids(body.officer_rank_ids)
    stored = await guild_settings_db.upsert_settings(
        world,
        guild_name,
        officers_can_delete_parses=body.officers_can_delete_parses,
        officer_rank_ids=rank_ids,
        updated_by=user["id"],
    )
    invalidate_officer_ranks(guild_name, world)
    audit_log(
        "guild_settings_updated",
        actor=user["id"],
        guild=guild_name,
        officers_can_delete_parses=body.officers_can_delete_parses,
        officer_rank_ids=",".join(str(i) for i in rank_ids) if rank_ids is not None else "default",
    )
    return await _to_response(stored)
