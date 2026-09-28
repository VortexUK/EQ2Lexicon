"""Site-wide settings: the store, the admin PUT (validation + audit), and the
/api/server bootstrap carrying discord_invite_url."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import server_context
from backend.server.db import init_db
from backend.server.db.site_settings import DISCORD_INVITE_URL_KEY, SiteSettingsStore
from backend.server.db.site_settings import store as site_settings
from tests.fixtures.users import make_fake_admin
from tests.fixtures.users_db import point_users_db_at

_ADMIN = make_fake_admin(id="admin-1", username="boss")


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)
    server_context.load_registry()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_store_round_trip_and_clear(users_db):
    s = SiteSettingsStore(users_db)
    assert await s.get_setting(DISCORD_INVITE_URL_KEY) is None
    await s.set_setting(DISCORD_INVITE_URL_KEY, " https://discord.gg/abc ", updated_by="admin-1")
    assert await s.get_setting(DISCORD_INVITE_URL_KEY) == "https://discord.gg/abc"
    await s.set_setting(DISCORD_INVITE_URL_KEY, "https://discord.gg/def", updated_by="admin-2")
    assert await s.all_settings() == {DISCORD_INVITE_URL_KEY: "https://discord.gg/def"}
    await s.set_setting(DISCORD_INVITE_URL_KEY, None, updated_by="admin-1")
    assert await s.get_setting(DISCORD_INVITE_URL_KEY) is None
    await s.set_setting(DISCORD_INVITE_URL_KEY, "   ", updated_by="admin-1")
    assert await s.all_settings() == {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def _put(app, body: dict, *, admin: bool = True):
    patches = [patch("backend.server.api.admin.audit_log")]
    if admin:
        patches.append(patch("backend.server.api.admin._require_admin", lambda request=None: _ADMIN))
    with patches[0] as audit:
        if admin:
            with patches[1]:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    r = await client.put("/api/admin/site-settings", json=body)
        else:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.put("/api/admin/site-settings", json=body)
    return r, audit


async def test_put_requires_admin(app):
    r, audit = await _put(app, {"discord_invite_url": "https://discord.gg/abc"}, admin=False)
    assert r.status_code in (401, 403)
    audit.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    [
        "http://discord.gg/abc",
        "https://evil.com/discord.gg/abc",
        "https://discord.gg/",
        "https://discord.gg/abc?x=<script>",
        "javascript:alert(1)",
        "discord.gg/abc",
    ],
)
async def test_put_rejects_anything_but_a_real_invite(app, bad):
    r, audit = await _put(app, {"discord_invite_url": bad})
    assert r.status_code == 400, bad
    audit.assert_not_called()
    assert await site_settings.get_setting(DISCORD_INVITE_URL_KEY) is None


async def test_put_saves_audits_and_the_bootstrap_reflects_it(app):
    r, audit = await _put(app, {"discord_invite_url": "https://discord.com/invite/abc123"})
    assert r.status_code == 200
    assert r.json() == {"discord_invite_url": "https://discord.com/invite/abc123"}
    audit.assert_called_once()
    assert audit.call_args.args[0] == "site_settings_updated"
    assert audit.call_args.kwargs["key"] == DISCORD_INVITE_URL_KEY
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        boot = await client.get("/api/server", headers={"host": "wuoshi.eq2lexicon.com"})
        with patch("backend.server.api.admin._require_admin", lambda request=None: _ADMIN):
            got = await client.get("/api/admin/site-settings")
    assert boot.status_code == 200
    assert boot.json()["discord_invite_url"] == "https://discord.com/invite/abc123"
    assert got.json() == {"discord_invite_url": "https://discord.com/invite/abc123"}

    cleared, _ = await _put(app, {"discord_invite_url": None})
    assert cleared.status_code == 200 and cleared.json() == {"discord_invite_url": None}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        boot = await client.get("/api/server")
    assert boot.json()["discord_invite_url"] is None


async def test_bootstrap_carries_null_when_unset(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        boot = await client.get("/api/server")
    assert boot.status_code == 200
    assert boot.json()["discord_invite_url"] is None
