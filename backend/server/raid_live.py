"""Twitch-verified "Raiding live" signal.

A background poller (models census_health.poll_loop) checks which raid teams are
BOTH inside a scheduled raid window (in the team's timezone, small grace) AND
actually live on Twitch (Helix API). The result is cached per world; the
``/api/raiding-live`` endpoint reads it.

Degrades gracefully: with no ``TWITCH_CLIENT_ID`` / ``TWITCH_CLIENT_SECRET`` the
poller no-ops and the live list stays empty — the schedule feature is
unaffected.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiohttp

_log = logging.getLogger(__name__)

_POLL_INTERVAL = 90  # seconds
_GRACE_MIN = 15  # a stream a bit early/late still counts as "raiding"
_WEEK_MIN = 7 * 1440

# world -> list of live-team dicts. Replaced wholesale each refresh.
_live_by_world: dict[str, list[dict]] = {}

_token: str | None = None
_token_expiry: float = 0.0


def _creds() -> tuple[str | None, str | None]:
    return os.getenv("TWITCH_CLIENT_ID"), os.getenv("TWITCH_CLIENT_SECRET")


def is_configured() -> bool:
    cid, secret = _creds()
    return bool(cid and secret)


def get_live(world: str) -> list[dict]:
    """The teams currently raiding + live on the given world (cache read)."""
    return _live_by_world.get(world, [])


def _reset_for_test() -> None:
    global _token, _token_expiry
    _live_by_world.clear()
    _token, _token_expiry = None, 0.0


# ---------------------------------------------------------------------------
# Schedule-window check (pure; `now` injectable for tests)
# ---------------------------------------------------------------------------


def _slot_active_now(days: list[int], start_min: int, end_min: int, now_weekday: int, now_minute: int) -> bool:
    """True if (now_weekday, now_minute) falls within [start-grace, end+grace]
    on any of the slot's days. Uses week-minutes so day-of-week, a window that
    crosses midnight, and the grace wrap-around are all handled uniformly."""
    dur = (end_min - start_min) % 1440  # 1..300
    length = dur + 2 * _GRACE_MIN
    now_wm = (now_weekday - 1) * 1440 + now_minute
    for d in days:
        lo = (d - 1) * 1440 + start_min - _GRACE_MIN
        if (now_wm - lo) % _WEEK_MIN <= length:
            return True
    return False


def team_scheduled_now(team: dict, now: datetime | None = None) -> bool:
    """Is this team inside one of its raid windows right now (team's tz)?"""
    try:
        zi = ZoneInfo(team.get("primary_tz") or "UTC")
    except Exception:
        return False
    local = (now or datetime.now(tz=UTC)).astimezone(zi)
    wd = local.isoweekday()
    mn = local.hour * 60 + local.minute
    for r in team.get("raids", []):
        days = [int(d) for d in str(r.get("days", "")).split(",") if d]
        if days and _slot_active_now(days, r["start_min"], r["end_min"], wd, mn):
            return True
    return False


# ---------------------------------------------------------------------------
# Twitch Helix
# ---------------------------------------------------------------------------


async def _get_token() -> str | None:
    global _token, _token_expiry
    now = time.time()
    if _token and now < _token_expiry - 60:
        return _token
    cid, secret = _creds()
    if not (cid and secret):
        return None
    try:
        async with (
            aiohttp.ClientSession() as s,
            s.post(
                "https://id.twitch.tv/oauth2/token",
                data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r,
        ):
            if r.status != 200:
                _log.warning("[raid-live] token request failed: HTTP %s", r.status)
                return None
            data = await r.json()
    except Exception as exc:
        _log.warning("[raid-live] token request error: %s", exc)
        return None
    _token = data.get("access_token")
    _token_expiry = now + int(data.get("expires_in", 3600))
    return _token


async def _fetch_live(logins: list[str], token: str) -> dict[str, dict]:
    """Return {login: {viewer_count, title, started_at}} for the logins that are
    live. Batched at Helix's 100-login limit."""
    cid, _ = _creds()
    out: dict[str, dict] = {}
    headers = {"Client-Id": cid or "", "Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as s:
        for i in range(0, len(logins), 100):
            params = [("user_login", login) for login in logins[i : i + 100]]
            try:
                async with s.get(
                    "https://api.twitch.tv/helix/streams",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status != 200:
                        _log.warning("[raid-live] streams query failed: HTTP %s", r.status)
                        continue
                    data = await r.json()
            except Exception as exc:
                _log.warning("[raid-live] streams query error: %s", exc)
                continue
            for stream in data.get("data", []):
                login = (stream.get("user_login") or "").lower()
                if login:
                    out[login] = {
                        "viewer_count": stream.get("viewer_count"),
                        "title": stream.get("title"),
                        "started_at": stream.get("started_at"),
                    }
    return out


def build_live_map(candidates: list[dict], live: dict[str, dict]) -> dict[str, list[dict]]:
    """Map scheduled-now candidate teams whose login is live → world → entries.
    Pure (no I/O) so it's unit-testable."""
    by_world: dict[str, list[dict]] = defaultdict(list)
    for t in candidates:
        login = (t.get("twitch_login") or "").lower()
        if login and login in live:
            by_world[t["world"]].append(
                {
                    "guild_name": t["guild_name"],
                    "team_name": t["name"],
                    "twitch_login": login,
                    "twitch_url": f"https://twitch.tv/{login}",
                    **live[login],
                }
            )
    return dict(by_world)


async def refresh() -> None:
    """One poll: find scheduled-now teams, verify live on Twitch, update cache."""
    global _live_by_world
    if not is_configured():
        return
    from backend.server.db.raid_schedule import list_all_teams_with_twitch  # local: avoid import cycle

    teams = await list_all_teams_with_twitch()
    candidates = [t for t in teams if t.get("twitch_login") and team_scheduled_now(t)]
    if not candidates:
        _live_by_world = {}
        return
    token = await _get_token()
    if not token:
        return
    logins = list({t["twitch_login"].lower() for t in candidates})
    live = await _fetch_live(logins, token)
    _live_by_world = build_live_map(candidates, live)


async def poll_loop() -> None:
    """Background task: refresh now, then every _POLL_INTERVAL. Never raises."""
    while True:
        try:
            await refresh()
        except Exception:  # pragma: no cover - defensive
            _log.exception("[raid-live] poll error")
        await asyncio.sleep(_POLL_INTERVAL)
