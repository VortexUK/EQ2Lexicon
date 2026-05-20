from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from web.db import upsert_user

router = APIRouter(tags=["auth"])

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI", "http://localhost:8000/api/auth/callback"
)
_DISCORD_API = "https://discord.com/api/v10"
_SCOPES = "identify"

_ADMIN_IDS: frozenset[str] = frozenset(
    filter(None, os.getenv("ADMIN_DISCORD_IDS", "").split(","))
)


class UserResponse(BaseModel):
    id: str
    username: str
    global_name: str | None = None
    avatar: str | None = None
    is_admin: bool = False


@router.get("/auth/login")
async def login() -> RedirectResponse:
    """Redirect the browser to Discord's OAuth2 authorisation page."""
    params = urlencode(
        {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "response_type": "code",
            "scope": _SCOPES,
        }
    )
    return RedirectResponse(f"https://discord.com/api/oauth2/authorize?{params}")


@router.get("/auth/callback")
async def callback(code: str, request: Request) -> RedirectResponse:
    """Exchange the OAuth2 code for a token, fetch user info, store in session."""
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
            raise HTTPException(status_code=400, detail="Failed to exchange OAuth code")
        access_token = token_resp.json()["access_token"]

        user_resp = await client.get(
            f"{_DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Discord user")
        user = user_resp.json()

    request.session["user"] = {
        "id": user["id"],
        "username": user["username"],
        "global_name": user.get("global_name"),
        "avatar": user.get("avatar"),
    }

    # Persist / update user record in our DB
    await upsert_user(
        discord_id=user["id"],
        discord_name=user.get("global_name") or user["username"],
        discord_username=user["username"],
        avatar=user.get("avatar"),
    )

    return RedirectResponse("/")


@router.get("/auth/me", response_model=UserResponse)
async def me(request: Request) -> UserResponse:
    """Return the currently logged-in user, or 401 if not authenticated."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserResponse(**user, is_admin=user["id"] in _ADMIN_IDS)


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    request.session.clear()
    return JSONResponse({"ok": True})
