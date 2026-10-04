"""Account erasure: the store function over both databases, the admin route,
and the self-service route. The privacy policy promises exactly what these
tests pin down: rows that ARE the person go, rows that merely name them as
an actor are tombstoned, uploads stay as guild records without the Discord
identity, and nobody else's data moves."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient
from psycopg.types.json import Json

from backend.server.db import erasure as erasure_mod
from backend.server.db.erasure import DELETED_SOURCE_DSN, DELETED_USER_ID, erase_user_sync
from tests.fixtures.pg import pg_conn
from tests.fixtures.users import make_fake_admin

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"
VICTIM = "111"
OTHER = "222"
NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def users_db(users_schema: str) -> str:
    """Isolated leased schema per test (conftest ``users_schema``), aliased
    so tests can keep naming it ``users_db``."""
    return users_schema


@pytest.fixture
def parses_db(parses_db_path: str) -> str:
    """Leased parses schema — erasure reads ``parses.db.SCHEMA`` at call
    time, and the ``parses_db_path`` fixture already re-points it."""
    return parses_db_path


def _seed_users(schema: str) -> None:
    with pg_conn(schema) as c:
        for uid, name in ((VICTIM, "Victim"), (OTHER, "Other")):
            c.execute(
                "INSERT INTO users (discord_id, discord_name, discord_username, first_seen, last_seen, access_status) "
                "VALUES (%s, %s, %s, %s, %s, 'approved')",
                (uid, name, name.lower(), NOW, NOW),
            )
        c.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, token_prefix, created_at) VALUES (%s, 'act', 'h1', 'eq2c_aaaa', %s)",
            (VICTIM, NOW),
        )
        c.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, token_prefix, created_at) VALUES (%s, 'act', 'h2', 'eq2c_bbbb', %s)",
            (OTHER, NOW),
        )
        c.execute("INSERT INTO user_roles (discord_id, role, granted_by) VALUES (%s, 'supporter', %s)", (VICTIM, OTHER))
        c.execute(
            "INSERT INTO user_roles (discord_id, role, granted_by) VALUES (%s, 'contributor', %s)", (OTHER, VICTIM)
        )
        c.execute(
            "INSERT INTO character_claims (discord_id, character_name, status, requested_at, reviewed_by, world) "
            "VALUES (%s, 'Sihtric', 'approved', %s, %s, 'Varsoon')",
            (VICTIM, NOW, OTHER),
        )
        c.execute(
            "INSERT INTO character_claims (discord_id, character_name, status, requested_at, reviewed_by, world) "
            "VALUES (%s, 'Alt', 'approved', %s, %s, 'Varsoon')",
            (OTHER, NOW, VICTIM),
        )
        c.execute(
            "INSERT INTO character_favorites (discord_id, character_name, world) VALUES (%s, 'Alt', 'Varsoon')",
            (VICTIM,),
        )
        c.execute("INSERT INTO download_events (discord_id, slug) VALUES (%s, 'act-plugin')", (VICTIM,))
        c.execute(
            "INSERT INTO attendance_sessions (world, guild_name, session_day, seq, started_at, ended_at, uploaders) "
            "VALUES ('Varsoon', 'Exordium', '2026-09-20', 0, %s, %s, %s)",
            (NOW, NOW + 3600, Json({VICTIM: NOW, OTHER: NOW})),
        )
        sid = c.execute("SELECT id FROM attendance_sessions").fetchone()["id"]
        c.execute(
            "INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen) "
            "VALUES (%s, %s, 'voice', %s, %s)",
            (sid, VICTIM, NOW, NOW),
        )
        c.execute(
            "INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen) "
            "VALUES (%s, %s, 'voice', %s, %s)",
            (sid, OTHER, NOW, NOW),
        )
        c.execute(
            "INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen) "
            "VALUES (%s, 'Sihtric', 'raid', %s, %s)",
            (sid, NOW, NOW),
        )
        c.execute(
            "INSERT INTO attendance_overrides (session_id, character_name, category, set_by) VALUES (%s, 'Alt', 'afk', %s)",
            (sid, VICTIM),
        )
        c.execute(
            "INSERT INTO guild_settings (world, guild_name, officers_can_delete_parses, updated_by) "
            "VALUES ('Varsoon', 'Exordium', 0, %s)",
            (VICTIM,),
        )
        c.execute(
            "INSERT INTO guild_recruitment (world, guild_id, guild_name, recruiting, description, updated_by, "
            "logo, logo_media_type, logo_uploaded_by, logo_uploaded_at) "
            "VALUES ('Varsoon', 42, 'Exordium', 1, 'We raid.', %s, %s, 'image/webp', %s, %s)",
            (VICTIM, b"\x01", VICTIM, NOW),
        )


def _seed_parses(schema: str) -> None:
    with pg_conn(schema) as c:
        for i, (dsn, hidden_by) in enumerate(
            ((f"plugin:{VICTIM}", None), (f"plugin:{OTHER}", VICTIM), (f"plugin:{OTHER}", OTHER)), start=1
        ):
            c.execute(
                "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, duration_s, success_level, "
                "source_dsn, uploaded_by, guild_name, ingested_at, hidden_at, hidden_by) "
                "VALUES ('Varsoon', %s, 'Tarinax', 'Deathtoll', %s, %s, 60, 1, %s, 'Sihtric', 'Exordium', %s, %s, %s)",
                (f"enc{i}", NOW, NOW + 60, dsn, NOW, NOW if hidden_by else None, hidden_by),
            )
        c.execute(
            "INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn) VALUES ('Varsoon', 'enc1', 1, %s, %s)",
            (NOW, f"plugin:{VICTIM}"),
        )
        c.execute(
            "INSERT INTO tamper_reports (world, act_encid, title, started_at, ended_at, duration_s, uploader_logger_name, uploader_discord_id, uploader_discord_name, "
            "guild_name, reason, reported_at, payload_json) VALUES ('Varsoon', 'enc1', 'Tarinax', 1800000000, 1800000060, 60, 'Sihtric', %s, 'Victim', 'Exordium', "
            "'stale_encounter', %s, '{}')",
            (VICTIM, NOW),
        )
        c.execute(
            "INSERT INTO tamper_reports (world, act_encid, title, started_at, ended_at, duration_s, uploader_logger_name, uploader_discord_id, uploader_discord_name, "
            "guild_name, reason, reported_at, payload_json) VALUES ('Varsoon', 'enc2', 'Tarinax', 1800000000, 1800000060, 60, 'Alt', %s, 'Other', 'Exordium', "
            "'stale_encounter', %s, '{}')",
            (OTHER, NOW),
        )


def _count(schema: str, sql: str, *params) -> int:
    """parses schema (Postgres) — ``%s`` placeholders, COUNT aliased AS n."""
    with pg_conn(schema) as c:
        return c.execute(sql, params).fetchone()["n"]


def _count_u(schema: str, sql: str, *params) -> int:
    """users schema (Postgres) — ``%s`` placeholders, COUNT aliased AS n."""
    with pg_conn(schema) as c:
        return c.execute(sql, params).fetchone()["n"]


# ---------------------------------------------------------------------------
# Store function
# ---------------------------------------------------------------------------


def test_erase_removes_owned_rows_tombstones_authorship_and_strips_uploads(users_db, parses_db):
    _seed_users(users_db)
    _seed_parses(parses_db)

    result = erase_user_sync(VICTIM, users_schema=users_db, parses_schema=parses_db, now=NOW)

    assert result.found is True
    # Owned rows gone; the other user's rows intact.
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", VICTIM) == 0
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", OTHER) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM api_tokens WHERE user_id = %s", VICTIM) == 0
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM api_tokens WHERE user_id = %s", OTHER) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM user_roles WHERE discord_id = %s", VICTIM) == 0
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM character_claims WHERE discord_id = %s", VICTIM) == 0
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM character_claims WHERE discord_id = %s", OTHER) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM character_favorites WHERE discord_id = %s", VICTIM) == 0
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM download_events WHERE discord_id = %s", VICTIM) == 0
    # Authorship tombstoned to the placeholder row, which now exists.
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", DELETED_USER_ID) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM character_claims WHERE reviewed_by = %s", VICTIM) == 0
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM character_claims WHERE reviewed_by = %s", DELETED_USER_ID) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM user_roles WHERE granted_by = %s", DELETED_USER_ID) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM attendance_overrides WHERE set_by = %s", DELETED_USER_ID) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM guild_settings WHERE updated_by = %s", DELETED_USER_ID) == 1
    # Recruitment: both author columns tombstoned; the guild's profile text
    # and logo blob stay (guild assets, not the person's data).
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM guild_recruitment WHERE updated_by = %s", DELETED_USER_ID) == 1
    assert (
        _count_u(users_db, "SELECT COUNT(*) AS n FROM guild_recruitment WHERE logo_uploaded_by = %s", DELETED_USER_ID)
        == 1
    )
    with pg_conn(users_db) as c:
        row = c.execute("SELECT logo, description FROM guild_recruitment").fetchone()
    assert bytes(row["logo"]) == b"\x01" and row["description"] == "We raid."
    assert result.tombstoned["character_claims.reviewed_by"] == 1
    # Voice rows for the victim gone, the other person's and the raid row kept.
    assert (
        _count_u(users_db, "SELECT COUNT(*) AS n FROM attendance_observations WHERE character_name = %s", VICTIM) == 0
    )
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM attendance_observations WHERE character_name = %s", OTHER) == 1
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM attendance_observations WHERE kind = 'raid'") == 1
    assert result.voice_observations_deleted == 1
    # Uploaders audit scrubbed (jsonb — comes back parsed).
    with pg_conn(users_db) as c:
        uploaders = c.execute("SELECT uploaders FROM attendance_sessions").fetchone()["uploaders"]
    assert uploaders == {OTHER: NOW}
    assert result.sessions_scrubbed == 1
    # parses schema: uploads stay, identity stripped; hidden_by cleared; reports gone.
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM encounters") == 3
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM encounters WHERE source_dsn = %s", DELETED_SOURCE_DSN) == 1
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM encounters WHERE source_dsn = %s", f"plugin:{OTHER}") == 2
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM encounters WHERE hidden_by = %s", VICTIM) == 0
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM encounters WHERE hidden_by = %s", OTHER) == 1
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM ingest_log WHERE source_dsn = %s", DELETED_SOURCE_DSN) == 1
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM tamper_reports WHERE uploader_discord_id = %s", VICTIM) == 0
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM tamper_reports WHERE uploader_discord_id = %s", OTHER) == 1
    assert result.parses_anonymised == 1
    assert result.tamper_reports_deleted == 1


def test_erase_is_idempotent_and_never_touches_the_tombstone(users_db, parses_db):
    _seed_users(users_db)
    assert erase_user_sync(VICTIM, users_schema=users_db, parses_schema=parses_db, now=NOW).found is True
    again = erase_user_sync(VICTIM, users_schema=users_db, parses_schema=parses_db, now=NOW)
    assert again.found is False
    assert erase_user_sync(DELETED_USER_ID, users_schema=users_db, parses_schema=parses_db).found is False
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", DELETED_USER_ID) == 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    payload = base64.b64encode(json.dumps({"user": user}).encode()).decode()
    return {"session": itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()}


_ADMIN = make_fake_admin(id="admin-1", username="boss")


async def test_admin_erase_route(app, users_db, parses_db):
    _seed_users(users_db)
    _seed_parses(parses_db)
    with (
        patch("backend.server.api.admin._require_admin", lambda request=None: _ADMIN),
        patch("backend.server.api.admin.audit_log") as audit,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete(f"/api/admin/users/{VICTIM}")
            gone = await client.delete(f"/api/admin/users/{VICTIM}")
            selfie = await client.delete("/api/admin/users/admin-1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["found"] is True and body["parses_anonymised"] == 1
    assert gone.status_code == 404
    assert selfie.status_code == 400
    assert audit.call_args.args[0] == "user_erased"
    assert audit.call_args.kwargs["discord_id"] == VICTIM
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", VICTIM) == 0


async def test_admin_erase_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete(f"/api/admin/users/{VICTIM}")
    assert r.status_code in (401, 403)


async def test_self_service_erase_requires_typed_username_and_clears_session(app, users_db, parses_db):
    _seed_users(users_db)
    _seed_parses(parses_db)
    cookies = _cookies({"id": VICTIM, "username": "victim", "global_name": "Victim", "avatar": None})
    with patch("backend.server.api.auth.audit_log") as audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
            wrong = await client.request("DELETE", "/api/auth/me", json={"confirm": "someone-else"})
            assert wrong.status_code == 400
            assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", VICTIM) == 1
            ok = await client.request("DELETE", "/api/auth/me", json={"confirm": " Victim "})
            assert ok.status_code == 200
            assert ok.json()["ok"] is True
    # Session cleared: Starlette's SessionMiddleware answers an emptied
    # session with a null cookie that expires in 1970.
    set_cookie = ok.headers.get("set-cookie", "")
    assert "session=null" in set_cookie and "1970" in set_cookie
    assert audit.call_args.args[0] == "user_erased"
    assert audit.call_args.kwargs["self_service"] is True
    assert _count_u(users_db, "SELECT COUNT(*) AS n FROM users WHERE discord_id = %s", VICTIM) == 0
    assert _count(parses_db, "SELECT COUNT(*) AS n FROM encounters WHERE source_dsn = %s", DELETED_SOURCE_DSN) == 1


async def test_self_service_erase_requires_login(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.request("DELETE", "/api/auth/me", json={"confirm": "x"})
    assert r.status_code == 401
