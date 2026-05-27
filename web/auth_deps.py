"""
Shared auth dependencies for FastAPI routes.

`require_user_session` — session-cookie only (the existing pattern).
`require_user_session_or_token` — session cookie OR Authorization: Bearer.
                                  Used by endpoints meant for the ACT plugin
                                  (and other external integrations).
`is_admin` / `require_admin` — admin allow-list driven by the
                               ADMIN_DISCORD_IDS env var (comma-separated
                               Discord IDs).

Roles model
-----------

There are three role sources that grant content-edit access. They layer
inside `require_editor`:

  * **admin**       — env-driven (`ADMIN_DISCORD_IDS`). Cheapest check.
                      Intentionally outside the DB so a wipe can't lock you
                      out.
  * **contributor** — DB-driven via the `user_roles` table. Admin-grantable
                      from the UI. Generic enough that future roles
                      (`moderator`, `editor`, …) slot in as new role strings
                      without a schema change.
  * **officer**     — dynamic. Computed at request time from the user's
                      primary character's guild rank against
                      ``_OFFICER_RANKS`` (see ``web/routes/guild.py``).
                      Never persisted — Census is the source of truth.

`KNOWN_ROLES` is the route-layer allowlist for grant/revoke endpoints; new
roles get added there before they're meaningful anywhere else.

TODO(future): per-role permission system. Currently each role-aware dep
hardcodes which roles it accepts. When the codebase grows >1 capability
dimensions, layer a `role_permissions` table and have the deps consult it.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

from web import db as users_db

# Admin allow-list. Comma-separated env var of Discord IDs. Frozen at import
# time — a config change requires a process restart, which is fine for our
# deploy model (Railway redeploys on push).
ADMIN_IDS: frozenset[str] = frozenset(filter(None, os.getenv("ADMIN_DISCORD_IDS", "").split(",")))
if not ADMIN_IDS:
    logging.getLogger(__name__).warning(
        "ADMIN_DISCORD_IDS is not set — admin-only endpoints will return 403 for every caller."
    )


def require_user_session(request: Request) -> dict:
    """Require a logged-in session. Returns the session user dict.

    Shape:  {"id": "<discord_id>", "username": "...", ...}
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_user_session_or_token(request: Request) -> dict:
    """Accept either a session cookie OR an `Authorization: Bearer <token>`
    header. Returns a normalised dict:

        {"id": "<discord_id>", "username": "...", "auth_source": "session"|"token"}

    For token auth we also bump last_used_at on the token row.
    """
    # Prefer session cookie if present — cheaper, no DB hit.
    user = request.session.get("user")
    if user:
        return {**user, "auth_source": "session"}

    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    raw_token = auth_header[len("Bearer ") :].strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    row = await users_db.lookup_api_token(raw_token)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    if row.get("access_status") not in ("approved", None):
        # Tokens minted before the user is approved still resolve, but we
        # gate on access_status here so a token from a denied/pending user
        # can't be used for writes.
        raise HTTPException(status_code=403, detail="Account not approved")

    return {
        "id": row["user_id"],
        "username": row.get("discord_username") or row.get("discord_name") or row["user_id"],
        "discord_name": row.get("discord_name"),
        "auth_source": "token",
        "token_id": row["token_id"],
        "token_name": row.get("token_name"),
    }


def is_admin(user: dict | None) -> bool:
    """True iff the session user's Discord ID is in ADMIN_IDS."""
    return bool(user and user.get("id") in ADMIN_IDS)


def require_admin(request: Request) -> dict:
    """Require a logged-in admin. 401 if no session, 403 if not in
    ADMIN_DISCORD_IDS. Returns the session user dict."""
    user = require_user_session(request)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# DB-driven roles (see `user_roles` schema in web/db.py)
# ---------------------------------------------------------------------------

# Allowlist for grant/revoke routes. Routes reject unknown role names with a
# 400 — keeps the table free of typo'd "Contibutor" rows that'd silently grant
# nothing. Add a new role here AND wire its capability into the appropriate
# require_X dep before exposing it in the admin UI.
KNOWN_ROLES: frozenset[str] = frozenset({"contributor"})


async def is_contributor(user: dict | None) -> bool:
    """True iff the session user holds the 'contributor' DB role."""
    if not user:
        return False
    return await users_db.has_role(user["id"], "contributor")


async def require_editor(request: Request) -> dict:
    """Gate for content-edit endpoints (raid strategies, zone overviews).

    Allows the request through if the session user is any of:
      * admin (env-driven via ADMIN_DISCORD_IDS), or
      * a contributor (DB-granted via the `user_roles` table), or
      * an officer (dynamic — computed from their primary character's guild
        rank by `_officer_chars` in web/routes/guild.py).

    The officer check is the most expensive (cache hit then potentially a
    Census round-trip) — admin and contributor get checked first so the hot
    path is cheap. The officer branch lives in this module's caller, kept
    imported lazily to avoid a routes→auth circular import.
    """
    user = require_user_session(request)
    if is_admin(user):
        return user
    if await is_contributor(user):
        return user

    # Officer check — lazy import to avoid the routes→auth_deps circular
    # dependency that a top-level import would create.
    from web.routes.guild import _officer_chars
    from web.routes.raid_strategies import _resolve_primary_guild_cached

    discord_id = user["id"]
    guild_name = await _resolve_primary_guild_cached(discord_id)
    if guild_name:
        officer_chars = await _officer_chars(discord_id, guild_name)
        if officer_chars:
            return user

    raise HTTPException(
        status_code=403,
        detail="Editing requires admin, contributor, or officer rank.",
    )
