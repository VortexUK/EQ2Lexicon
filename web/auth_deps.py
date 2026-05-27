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

Capabilities + the role → capability map
----------------------------------------

Routes don't gate on roles directly — they gate on **capabilities** via
`require_capability(...)`. The `role_permissions` table (web/db.py) maps
each persistent role to the capabilities it grants. Admin is the synthetic
"all capabilities" branch (so it never appears in the table); officer is
dynamic but does appear in the table so adding a new capability for officer
is a one-row INSERT rather than a code change.

`KNOWN_CAPABILITIES` is the programmer-facing allowlist for capability
strings — guards against typos at route-definition time (`require_capability`
raises if the capability isn't registered here).

Adding a new capability is two lines: register the string in
`KNOWN_CAPABILITIES` and seed any `role_permissions` rows in
`web/db.py:init_db`.
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
# DB-driven roles + capabilities
# (see `user_roles` + `role_permissions` schema in web/db.py)
# ---------------------------------------------------------------------------

# Allowlist for grant/revoke routes. Routes reject unknown role names with a
# 400 — keeps the table free of typo'd "Contibutor" rows that'd silently grant
# nothing. Add a new role here AND seed its role_permissions rows in
# web/db.py:init_db before exposing it in the admin UI.
KNOWN_ROLES: frozenset[str] = frozenset({"contributor"})

# Programmer-facing capability allowlist. `require_capability` raises at
# route-definition time if a typo'd string is used, so a misnamed capability
# can never silently authorize nothing. Adding a new capability is two lines:
# add the string here AND seed its role_permissions rows in db.init_db.
KNOWN_CAPABILITIES: frozenset[str] = frozenset({"edit_content"})


async def is_contributor(user: dict | None) -> bool:
    """True iff the session user holds the 'contributor' DB role.

    Kept for explicit callers / tests; the auth gate uses the
    capability-driven helpers below, not this specific role check."""
    if not user:
        return False
    return await users_db.has_role(user["id"], "contributor")


def require_capability(capability: str):
    """Build a FastAPI ``Depends``-able function that gates the route on a
    named capability.

    Resolution order, cheapest first:
      1. **admin** — env-driven, synthesized to "has every capability".
      2. **DB roles** — single JOIN'd EXISTS over (user_roles ⨝
         role_permissions). Cheap, indexed on both join keys.
      3. **officer** — dynamic. Only runs if ``('officer', capability)`` is
         in role_permissions (skip the Census-fallback path for capabilities
         officers can't do anyway).

    The factory pattern means routes specify *what* they need
    (``require_capability("edit_content")``), not *who* qualifies — the
    role → capability mapping stays purely data."""
    if capability not in KNOWN_CAPABILITIES:
        # Defensive: catches typos at module-import / route-definition time
        # rather than at request time. Programmer error, never user-facing.
        raise ValueError(
            f"Unknown capability {capability!r}. Add it to KNOWN_CAPABILITIES "
            f"and seed role_permissions rows in web/db.py:init_db first."
        )

    async def dep(request: Request) -> dict:
        user = require_user_session(request)
        if is_admin(user):
            return user

        discord_id = user["id"]
        if await users_db.user_has_capability_via_db(discord_id, capability):
            return user

        # Only run the officer (dynamic) check if officers can actually do
        # this capability — saves a get_active_claims + cache lookup on
        # capabilities they wouldn't qualify for anyway.
        if await users_db.role_has_capability("officer", capability):
            # Lazy import to skirt the routes→auth_deps circular dependency.
            from web.routes.guild import _officer_chars
            from web.routes.raid_strategies import _resolve_primary_guild_cached

            guild_name = await _resolve_primary_guild_cached(discord_id)
            if guild_name:
                officer_chars = await _officer_chars(discord_id, guild_name)
                if officer_chars:
                    return user

        raise HTTPException(
            status_code=403,
            detail=f"This action requires the {capability!r} capability.",
        )

    # Set a recognisable __name__ so dependency_overrides keys in tests are
    # legible and so FastAPI's OpenAPI schema shows something useful.
    dep.__name__ = f"require_capability_{capability}"
    return dep


# Module-level named instance: stays singular so test overrides work. Routes
# import this name; a future capability-aware route can either build its own
# (`require_capability("foo")`) or define a sibling alias here.
require_editor = require_capability("edit_content")
