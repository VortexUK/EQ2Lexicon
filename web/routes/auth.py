from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from web.auth_deps import ADMIN_IDS as _ADMIN_IDS  # canonical source; auth_deps logs the "not set" warning once
from web.db import get_user_access_status, list_roles_for_user, upsert_user
from web.lib.audit_log import audit_log
from web.lib.log_safety import scrub as _scrub

_log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
_DISCORD_API = "https://discord.com/api/v10"
_SCOPES = "identify"


def _is_allowed_return_host(host: str | None) -> bool:
    """True if `host` is the configured parent domain or one of its subdomains.

    Discord OAuth requires a single pre-registered ``redirect_uri``, so the
    callback always lands on the parent domain regardless of which subdomain
    the user started on. To send them back where they came from we stash the
    originating host in the session — but only if it's one we trust, to
    prevent an open-redirect that abuses the post-callback hop.

    In prod, the parent comes from ``SESSION_COOKIE_DOMAIN`` (e.g.
    ``.eq2lexicon.com``). In dev that env var is unset; we accept localhost
    so a dev redirect doesn't blank out the test loop."""
    if not host:
        return False
    host = host.split(":")[0].strip().lower()  # strip port
    parent = os.getenv("SESSION_COOKIE_DOMAIN", "").lstrip(".").lower().strip()
    if not parent:
        return host in ("localhost", "127.0.0.1")
    return host == parent or host.endswith("." + parent)


def _post_login_redirect(return_host: str | None, target: str) -> RedirectResponse:
    """Build the post-callback redirect. If we stashed a valid originating
    host at login time, redirect to it absolutely so the browser crosses
    back to the subdomain; otherwise a relative ``/`` keeps the user on
    whichever domain the callback fired on (parent in prod, localhost in dev)."""
    if _is_allowed_return_host(return_host):
        return RedirectResponse(f"https://{return_host}{target}")
    return RedirectResponse(target)


class UserResponse(BaseModel):
    id: str
    username: str
    global_name: str | None = None
    avatar: str | None = None
    is_admin: bool = False
    access_status: str = "approved"
    # DB-granted roles only (e.g. 'contributor'). Does NOT include 'admin'
    # (that's exposed via is_admin) or 'officer' (dynamic — would require a
    # Census round-trip on every /auth/me call; frontend treats Edit buttons
    # as admin-or-contributor and accepts that non-admin officers won't see
    # the button until they're also granted contributor).
    static_roles: list[str] = []


@router.get("/auth/login")
async def login(request: Request) -> RedirectResponse:
    """Redirect the browser to Discord's OAuth2 authorisation page."""
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    # Remember which subdomain the user started on so the callback can send
    # them back. The session cookie spans the parent domain (see
    # SESSION_COOKIE_DOMAIN), so this survives Discord's hop to the
    # eq2lexicon.com callback. Untrusted hosts (not under our parent) are
    # ignored — the callback then falls back to a relative redirect.
    origin_host = request.url.hostname
    if _is_allowed_return_host(origin_host):
        request.session["return_host"] = origin_host
    params = urlencode(
        {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
        }
    )
    return RedirectResponse(f"https://discord.com/api/oauth2/authorize?{params}")


@router.get("/auth/callback")
async def callback(code: str, state: str | None = None, *, request: Request) -> RedirectResponse:
    """Exchange the OAuth2 code for a token, fetch user info, store in session."""
    expected_state = request.session.pop("oauth_state", None)
    return_host = request.session.pop("return_host", None)
    if not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — please try logging in again")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"{_DISCORD_API}/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            _log.warning(
                "[auth] OAuth token exchange failed: HTTP %s — %s",
                token_resp.status_code,
                _scrub(token_resp.text[:200]),
            )
            raise HTTPException(status_code=400, detail="Failed to exchange OAuth code")
        access_token = token_resp.json()["access_token"]

        user_resp = await client.get(
            f"{_DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            _log.warning(
                "[auth] OAuth user-info fetch failed: HTTP %s — %s",
                user_resp.status_code,
                _scrub(user_resp.text[:200]),
            )
            raise HTTPException(status_code=400, detail="Failed to fetch Discord user")
        user = user_resp.json()

    request.session["user"] = {
        "id": user["id"],
        "username": user["username"],
        "global_name": user.get("global_name"),
        "avatar": user.get("avatar"),
    }

    # Persist / update user record in our DB.
    # Admin IDs are always force-approved — protects against DB wipe lockout.
    access_status = await upsert_user(
        discord_id=user["id"],
        discord_name=user.get("global_name") or user["username"],
        discord_username=user["username"],
        avatar=user.get("avatar"),
        admin_ids=_ADMIN_IDS,
    )

    audit_log(
        "login",
        actor=user["id"],
        username=user["username"],
        access_status=access_status,
    )
    # Approved users go straight to the app; others land on an access page
    # so the frontend can show the appropriate message. Either way, send them
    # back to the subdomain they started on (if we stashed one and trust it).
    target = "/" if access_status == "approved" else f"/?access={access_status}"
    return _post_login_redirect(return_host, target)


@router.get("/auth/me", response_model=UserResponse)
async def me(request: Request) -> UserResponse:
    """Return the currently logged-in user, or 401 if not authenticated."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Admin IDs are always approved regardless of DB state
    if user["id"] in _ADMIN_IDS:
        access_status = "approved"
    else:
        access_status = await get_user_access_status(user["id"])
    static_roles = await list_roles_for_user(user["id"])
    return UserResponse(
        **user,
        is_admin=user["id"] in _ADMIN_IDS,
        access_status=access_status,
        static_roles=static_roles,
    )


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    user = request.session.get("user")
    if user:
        audit_log("logout", actor=user["id"])
    request.session.clear()
    return JSONResponse({"ok": True})
