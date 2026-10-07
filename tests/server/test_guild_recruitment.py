"""Guild recruitment: the id-keyed store (profiles survive renames), the profile/logo/
listing routes, the PIL logo pipeline, and the daily census-existence sweep.
Every logo upload/removal is audited with the actor (the abuse trail).
"""

from __future__ import annotations

import base64
import io
import json
from unittest.mock import AsyncMock, patch

import itsdangerous
import pytest
from better_profanity import profanity
from httpx import ASGITransport, AsyncClient
from PIL import Image

import backend.server.api.guild_recruitment as recruitment_api
from backend.server import recruitment_sweep
from backend.server.api.guild_recruitment import RECRUITMENT_TAGS, _process_logo
from backend.server.db.guild_recruitment import store as gr
from tests.fixtures.pg import pg_conn

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"

_WORLD = "Varsoon"  # the test app's default world
_GID = 42


@pytest.fixture(autouse=True)
def users_db(users_schema: str) -> str:
    """Isolated leased schema per test (conftest ``users_schema``), aliased
    so tests can keep naming it ``users_db``."""
    return users_schema


@pytest.fixture(autouse=True)
def _fixed_classes(monkeypatch: pytest.MonkeyPatch):
    """Pin the adventure-class validator so tests never read the classes catalogue."""
    monkeypatch.setattr(recruitment_api, "_adventure_classes", frozenset({"Guardian", "Templar", "Wizard"}))


# A neutral sentinel standing in for profanity (no real slurs in the repo),
# same pattern as test_raid_schedule.py.
_SENTINEL = "zzsentinelzz"


@pytest.fixture
def sentinel_profanity():
    profanity.add_censor_words([_SENTINEL])
    yield _SENTINEL
    profanity.load_censor_words()


async def _save_profile(world=_WORLD, gid=_GID, name="Exordium", **overrides) -> dict:
    kwargs = {
        "recruiting": True,
        "description": "We raid.",
        "classes": ["Guardian"],
        "tags": ["raiding"],
        "contacts": ["Sihtric"],
        "discord_url": "https://discord.gg/abc123",
        "updated_by": "disc-1",
    }
    kwargs.update(overrides)
    return await gr.upsert_profile(world, gid, name, **kwargs)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_defaults_when_no_row(users_db):
    got = await gr.get_profile(_WORLD, "Exordium")
    assert got["guild_id"] is None
    assert got["recruiting"] is False
    assert got["classes"] == [] and got["tags"] == [] and got["contacts"] == []
    assert got["has_logo"] is False and got["logo_uploaded_by"] is None


async def test_upsert_round_trips_and_rename_follows_the_id(users_db):
    stored = await _save_profile()
    assert stored["guild_id"] == _GID
    assert stored["recruiting"] is True and stored["classes"] == ["Guardian"]
    assert stored["updated_by"] == "disc-1" and isinstance(stored["updated_at"], int)
    # Same id under a NEW name — the row moves to the new name.
    renamed = await _save_profile(name="Exordium Reborn", description="Renamed.")
    assert renamed["description"] == "Renamed."
    assert (await gr.get_profile(_WORLD, "Exordium"))["guild_id"] is None  # old name gone
    assert (await gr.get_profile(_WORLD, "Exordium Reborn"))["guild_id"] == _GID


async def test_name_conflict_purges_the_stale_id(users_db):
    """A disbanded guild's recycled name must not block (or leak into) the
    new guild's profile."""
    await _save_profile(gid=1, name="Exordium", description="old guild")
    await _save_profile(gid=2, name="Exordium", description="new guild")
    got = await gr.get_profile(_WORLD, "Exordium")
    assert got["guild_id"] == 2 and got["description"] == "new guild"
    # The stale id-1 row is gone entirely, not renamed.
    assert await gr.list_listed_ids(_WORLD) == [{"guild_id": 2, "guild_name": "Exordium"}]


async def test_profile_and_logo_writes_never_clobber_each_other(users_db):
    await gr.set_logo(_WORLD, _GID, "Exordium", logo=b"webpbytes", media_type="image/webp", uploaded_by="disc-9")
    # set_logo on a guild with no profile row creates one at defaults.
    fresh = await gr.get_profile(_WORLD, "Exordium")
    assert fresh["recruiting"] is False and fresh["has_logo"] is True
    assert fresh["logo_uploaded_by"] == "disc-9"
    # A profile save keeps the logo…
    await _save_profile()
    kept = await gr.get_logo(_WORLD, "Exordium")
    assert kept is not None and kept["logo"] == b"webpbytes"
    # …and a logo replace keeps the profile.
    await gr.set_logo(_WORLD, _GID, "Exordium", logo=b"v2", media_type="image/webp", uploaded_by="disc-9")
    assert (await gr.get_profile(_WORLD, "Exordium"))["description"] == "We raid."


async def test_clear_logo_reports_whether_anything_was_removed(users_db):
    assert await gr.clear_logo(_WORLD, "Exordium") is False
    await gr.set_logo(_WORLD, _GID, "Exordium", logo=b"x", media_type="image/webp", uploaded_by="d")
    assert await gr.clear_logo(_WORLD, "Exordium") is True
    assert await gr.clear_logo(_WORLD, "Exordium") is False
    assert (await gr.get_profile(_WORLD, "Exordium"))["has_logo"] is False


async def test_list_recruiting_is_world_scoped_ordered_and_blob_free(users_db):
    await _save_profile(gid=1, name="Alpha")
    await _save_profile(gid=2, name="Beta")
    await _save_profile(world="Wuoshi", gid=3, name="Elsewhere")
    await _save_profile(gid=4, name="Hidden", recruiting=False)
    # Bump Alpha so it is the most recent edit.
    with pg_conn(users_db) as c:
        c.execute("UPDATE guild_recruitment SET updated_at = updated_at + 60 WHERE guild_id = 1")
    rows = await gr.list_recruiting(_WORLD)
    assert [r["guild_name"] for r in rows] == ["Alpha", "Beta"]
    assert all("logo" not in r for r in rows)
    assert set(await gr.list_listed_worlds()) == {"Varsoon", "Wuoshi"}


async def test_sweep_helpers_rename_and_delist(users_db):
    await _save_profile()
    assert await gr.set_guild_name(_WORLD, _GID, "Exordium") is False  # unchanged
    assert await gr.set_guild_name(_WORLD, _GID, "New Name") is True
    assert (await gr.get_profile(_WORLD, "New Name"))["guild_id"] == _GID
    assert await gr.delist(_WORLD, _GID) is True
    assert await gr.delist(_WORLD, _GID) is False  # already delisted
    assert (await gr.get_profile(_WORLD, "New Name"))["recruiting"] is False
    assert await gr.list_recruiting(_WORLD) == []


# ---------------------------------------------------------------------------
# Logo pipeline
# ---------------------------------------------------------------------------


def _png(width=300, height=180, fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 120, 40)).save(buf, format=fmt)
    return buf.getvalue()


def test_process_logo_reencodes_to_bounded_webp():
    out = _process_logo(_png(300, 180))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    assert img.width == 200 and img.height == 120  # aspect preserved, fits 200 box
    assert len(out) <= 128 * 1024


def test_process_logo_never_upscales_small_images():
    out = _process_logo(_png(64, 64))
    img = Image.open(io.BytesIO(out))
    assert (img.width, img.height) == (64, 64)


def test_process_logo_flattens_an_animated_gif():
    frames = [Image.new("P", (120, 80), i) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100)
    out = _process_logo(buf.getvalue())
    assert Image.open(io.BytesIO(out)).format == "WEBP"


def test_process_logo_rejects_oversized_dimensions_before_decode():
    with pytest.raises(ValueError, match="4096"):
        _process_logo(_png(4097, 10))


def test_process_logo_rejects_garbage_and_disallowed_formats():
    with pytest.raises(ValueError, match="could not be read"):
        _process_logo(b"not an image at all" * 10)
    # A TIFF is a real image but not an allowed source format.
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="TIFF")
    with pytest.raises(ValueError, match="Unsupported image format"):
        _process_logo(buf.getvalue())


# ---------------------------------------------------------------------------
# Routes — shared plumbing
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    payload = base64.b64encode(json.dumps({"user": user}).encode()).decode()
    return {"session": itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()}


_OFFICER = {"id": "officer-1", "username": "officer"}


def _valid_body() -> dict:
    return {
        "recruiting": True,
        "description": "Core raiding guild, Tue/Thu.",
        "classes": ["Guardian", "Templar"],
        "tags": ["raiding", "core"],
        "contacts": ["Sihtric"],
        "discord_url": "https://discord.gg/abc123",
    }


async def _put(app, body, *, officer=True, admin=False, cookies=True, path="/api/guild/Exordium/recruitment"):
    officer_ret = {"sihtric"} if officer else set()
    with (
        patch.object(recruitment_api, "_officer_chars", new=AsyncMock(return_value=officer_ret)),
        patch.object(recruitment_api, "is_admin", return_value=admin),
        patch.object(recruitment_api, "_roster_rank_map", new=AsyncMock(return_value={"sihtric": 1, "alt": 3})),
        patch.object(recruitment_api, "_resolve_guild_id", new=AsyncMock(return_value=_GID)),
        patch.object(recruitment_api, "audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(path, json=body, cookies=_cookies(_OFFICER) if cookies else {})
    return r, audit


# ---------------------------------------------------------------------------
# Routes — profile
# ---------------------------------------------------------------------------


async def test_get_is_public_and_returns_defaults_with_tags(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/guild/Exordium/recruitment")
    assert r.status_code == 200
    body = r.json()
    assert body["recruiting"] is False and body["classes"] == []
    assert body["available_tags"] == list(RECRUITMENT_TAGS)


async def test_put_requires_auth_then_officer(app):
    r, _ = await _put(app, _valid_body(), cookies=False)
    assert r.status_code == 401
    r, audit = await _put(app, _valid_body(), officer=False)
    assert r.status_code == 403
    audit.assert_not_called()
    assert (await gr.get_profile(_WORLD, "Exordium"))["guild_id"] is None  # nothing persisted


async def test_put_by_officer_persists_and_audits(app):
    r, audit = await _put(app, _valid_body())
    assert r.status_code == 200
    body = r.json()
    assert body["recruiting"] is True
    assert body["classes"] == ["Guardian", "Templar"]
    assert body["tags"] == ["core", "raiding"]  # canonical RECRUITMENT_TAGS order
    assert body["contacts"] == ["Sihtric"]
    audit.assert_called_once()
    assert audit.call_args.args[0] == "guild_recruitment_updated"
    kw = audit.call_args.kwargs
    assert kw["guild"] == "Exordium" and kw["guild_id"] == _GID
    assert kw["recruiting"] is True and kw["classes"] == 2 and kw["has_discord"] is True
    stored = await gr.get_profile(_WORLD, "Exordium")
    assert stored["guild_id"] == _GID and stored["updated_by"] == "officer-1"


async def test_put_by_admin_who_is_not_an_officer(app):
    r, _ = await _put(app, _valid_body(), officer=False, admin=True)
    assert r.status_code == 200


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"classes": ["Guardian", "Berserker"]}, "Unknown adventure class"),
        ({"tags": ["raiding", "elite"]}, "Unknown tag"),
        ({"contacts": ["A", "B", "C", "D"]}, "At most 3"),
        ({"contacts": ["Bad Name!"]}, "Invalid character name"),
        ({"contacts": ["Stranger"]}, "not a member of Exordium"),
        ({"discord_url": "https://evil.example/discord.gg/x"}, "Discord link"),
        ({"discord_url": "http://discord.gg/abc"}, "Discord link"),
    ],
)
async def test_put_validation_rejections(app, mutation, expected):
    body = {**_valid_body(), **mutation}
    r, audit = await _put(app, body)
    assert r.status_code == 400
    assert expected in r.json()["detail"]
    audit.assert_not_called()
    assert (await gr.get_profile(_WORLD, "Exordium"))["guild_id"] is None


async def test_put_canonicalises_contact_capitalisation(app):
    body = {**_valid_body(), "contacts": ["sIHTRIC"]}
    r, _ = await _put(app, body)
    assert r.status_code == 200
    assert r.json()["contacts"] == ["Sihtric"]


async def test_put_empty_discord_clears_it(app):
    body = {**_valid_body(), "discord_url": "  "}
    r, _ = await _put(app, body)
    assert r.status_code == 200
    assert r.json()["discord_url"] is None


async def test_put_rejects_blocklisted_description_and_reports(app, sentinel_profanity):
    body = {**_valid_body(), "description": f"we are {sentinel_profanity} good"}
    r, audit = await _put(app, body)
    assert r.status_code == 400
    assert "reported" in r.json()["detail"]
    audit.assert_called_once()
    assert audit.call_args.args[0] == "suspicious_recruitment_text"
    assert audit.call_args.kwargs["field"] == "description"
    assert (await gr.get_profile(_WORLD, "Exordium"))["guild_id"] is None


async def test_put_rejects_a_bad_guild_name(app):
    r, _ = await _put(app, _valid_body(), path="/api/guild/" + "x" * 200 + "/recruitment")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# _resolve_guild_id
# ---------------------------------------------------------------------------


class _ClientCtx:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


async def test_resolve_guild_id_reuses_an_existing_row_without_census(users_db):
    await _save_profile()
    census = AsyncMock()
    with patch.object(recruitment_api, "shared_census_client", lambda: _ClientCtx(census)):
        with patch.object(recruitment_api, "current_world", return_value=_WORLD):
            assert await recruitment_api._resolve_guild_id("Exordium") == _GID
    census.get_guild_id.assert_not_called()


async def test_resolve_guild_id_falls_back_to_census_then_503(users_db):
    census = AsyncMock()
    census.get_guild_id = AsyncMock(return_value=77)
    with patch.object(recruitment_api, "shared_census_client", lambda: _ClientCtx(census)):
        with patch.object(recruitment_api, "current_world", return_value=_WORLD):
            assert await recruitment_api._resolve_guild_id("Fresh Guild") == 77
            census.get_guild_id = AsyncMock(return_value=None)
            with pytest.raises(Exception) as exc:
                await recruitment_api._resolve_guild_id("Fresh Guild")
            assert getattr(exc.value, "status_code", None) == 503


# ---------------------------------------------------------------------------
# Routes — logo
# ---------------------------------------------------------------------------


async def _put_logo(app, image_b64, *, officer=True, admin=False):
    with (
        patch.object(recruitment_api, "_officer_chars", new=AsyncMock(return_value={"sihtric"} if officer else set())),
        patch.object(recruitment_api, "is_admin", return_value=admin),
        patch.object(recruitment_api, "_resolve_guild_id", new=AsyncMock(return_value=_GID)),
        patch.object(recruitment_api, "audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/guild/Exordium/recruitment/logo",
                json={"image_base64": image_b64},
                cookies=_cookies(_OFFICER),
            )
    return r, audit


async def test_logo_upload_stores_webp_and_audits_the_uploader(app):
    r, audit = await _put_logo(app, base64.b64encode(_png(300, 180)).decode())
    assert r.status_code == 200
    assert r.json()["ok"] is True and isinstance(r.json()["logo_uploaded_at"], int)
    audit.assert_called_once()
    assert audit.call_args.args[0] == "guild_logo_uploaded"
    assert audit.call_args.kwargs["actor"] == "officer-1"
    assert audit.call_args.kwargs["stored_bytes"] <= 128 * 1024
    stored = await gr.get_logo(_WORLD, "Exordium")
    assert stored is not None and stored["logo_media_type"] == "image/webp"
    assert Image.open(io.BytesIO(stored["logo"])).format == "WEBP"
    # The uploader is pinned on the row itself too (the audit requirement).
    assert (await gr.get_profile(_WORLD, "Exordium"))["logo_uploaded_by"] == "officer-1"


async def test_logo_upload_authz_and_bad_inputs(app):
    ok_b64 = base64.b64encode(_png(50, 50)).decode()
    r, audit = await _put_logo(app, ok_b64, officer=False)
    assert r.status_code == 403
    audit.assert_not_called()
    r, _ = await _put_logo(app, "@@not-base64@@")
    assert r.status_code == 400
    r, _ = await _put_logo(app, base64.b64encode(b"junkjunkjunk" * 100).decode())
    assert r.status_code == 400
    r, _ = await _put_logo(app, base64.b64encode(b"\0" * (2 * 1024 * 1024 + 1)).decode())
    assert r.status_code == 413
    assert await gr.get_logo(_WORLD, "Exordium") is None


async def test_logo_get_delete_cycle_with_etag_and_idempotent_delete(app):
    await _put_logo(app, base64.b64encode(_png(120, 120)).decode())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        got = await client.get("/api/guild/Exordium/recruitment/logo")
        assert got.status_code == 200
        assert got.headers["content-type"].startswith("image/webp")
        assert got.headers["x-content-type-options"] == "nosniff"
        etag = got.headers["etag"]
        unchanged = await client.get("/api/guild/Exordium/recruitment/logo", headers={"If-None-Match": etag})
        assert unchanged.status_code == 304
    with (
        patch.object(recruitment_api, "_officer_chars", new=AsyncMock(return_value={"sihtric"})),
        patch.object(recruitment_api, "is_admin", return_value=False),
        patch.object(recruitment_api, "audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.delete("/api/guild/Exordium/recruitment/logo", cookies=_cookies(_OFFICER))
            second = await client.delete("/api/guild/Exordium/recruitment/logo", cookies=_cookies(_OFFICER))
    assert first.json()["removed"] is True and second.json()["removed"] is False
    audit.assert_called_once()
    assert audit.call_args.args[0] == "guild_logo_removed"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        gone = await client.get("/api/guild/Exordium/recruitment/logo")
    assert gone.status_code == 404


# ---------------------------------------------------------------------------
# Routes — browse listing
# ---------------------------------------------------------------------------


async def test_recruiting_list_is_world_scoped_with_tags_and_no_counts_without_census(app):
    await _save_profile(gid=1, name="Alpha")
    await _save_profile(world="Wuoshi", gid=2, name="Elsewhere")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/recruiting")
    assert r.status_code == 200
    body = r.json()
    assert [g["guild_name"] for g in body["guilds"]] == ["Alpha"]
    assert body["guilds"][0]["member_count"] is None  # no guild_history rows seeded
    assert body["available_tags"] == list(RECRUITMENT_TAGS)


async def test_recruiting_list_joins_member_counts_from_guild_history(app, census_schema):
    from backend.census.store import CensusStore

    # census_schema re-points the shared store (the instance recruitment_api
    # imported) at the leased schema; seed it through a scoped conn.
    cs = CensusStore(census_schema)
    conn = cs.init_db()
    cs.upsert_guild_history(conn, "Alpha", _WORLD, {"members": 55}, now=1_800_000_000, retention_days=400)
    cs.upsert_guild_history(conn, "Alpha", _WORLD, {"members": 61}, now=1_800_100_000, retention_days=400)
    conn.close()
    await _save_profile(gid=1, name="Alpha")
    await _save_profile(gid=2, name="NoHistory")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/recruiting")
    by_name = {g["guild_name"]: g for g in r.json()["guilds"]}
    assert by_name["Alpha"]["member_count"] == 61  # latest day wins
    assert by_name["NoHistory"]["member_count"] is None


# ---------------------------------------------------------------------------
# Census sweep
# ---------------------------------------------------------------------------


async def test_sweep_renames_delists_and_never_acts_on_census_errors(users_db):
    await _save_profile(gid=1, name="Alpha")
    await _save_profile(gid=2, name="Beta")
    await _save_profile(gid=3, name="Gamma")
    await _save_profile(gid=4, name="Delta")
    answers = {
        1: (True, "Alpha Reborn"),  # renamed in census
        2: (True, None),  # disbanded
        3: (False, None),  # census error — must be left alone
        4: (True, "Bad:Name!!" + "x" * 100),  # census answered garbage — skip
    }
    census = AsyncMock()
    census.get_guild_name_by_id = AsyncMock(side_effect=lambda gid: answers[gid])
    with (
        patch.object(recruitment_sweep, "shared_census_client", lambda: _ClientCtx(census)),
        patch.object(recruitment_sweep.census_health, "is_down", return_value=False),
        patch.object(recruitment_sweep, "audit_log") as audit,
        patch.object(recruitment_sweep, "_PER_GUILD_PACING_S", 0),
    ):
        result = await recruitment_sweep.run_recruitment_sweep()
    assert result == {"checked": 3, "renamed": 1, "delisted": 1}
    assert (await gr.get_profile(_WORLD, "Alpha Reborn"))["guild_id"] == 1
    assert (await gr.get_profile(_WORLD, "Beta"))["recruiting"] is False
    assert (await gr.get_profile(_WORLD, "Gamma"))["recruiting"] is True  # error → untouched
    assert (await gr.get_profile(_WORLD, "Delta"))["recruiting"] is True  # bad name → kept
    actions = sorted(c.args[0] for c in audit.call_args_list)
    assert actions == ["guild_recruitment_delisted", "guild_recruitment_renamed"]


async def test_sweep_skips_entirely_when_census_is_down(users_db):
    await _save_profile()
    with patch.object(recruitment_sweep.census_health, "is_down", return_value=True):
        result = await recruitment_sweep.run_recruitment_sweep()
    assert result["skipped"] == "census down"
    assert (await gr.get_profile(_WORLD, "Exordium"))["recruiting"] is True
