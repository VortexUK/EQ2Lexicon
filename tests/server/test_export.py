"""Read-only export API (/api/export/v1/*) — issue #219.

Auth = bearer token + admin-granted 'api' role. Rankings tests drive the
board builder over fixture kills; the abilities test goes end-to-end
through the REAL ingest path into the shared test parses DB.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.db import init_db
from tests.fixtures.users_db import point_users_db_at
from tests.server._parses_ingest_fixtures import _fake_require_user, _minimal_payload, _signed_post_kwargs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    point_users_db_at(monkeypatch, users_db)


@pytest.fixture
def isolated_parses_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private parses DB for the ingest-backed e2e test. Writing to the
    session-shared parses DB shifts encounter ids for every later test —
    the parses-list tests' lazy is_player backfill resolves mocked ids
    against the real DB, so a stray extra row breaks them at a distance."""
    from backend.server.parses import db as parses_db

    p = tmp_path / "parses.db"
    monkeypatch.setattr(parses_db, "DB_PATH", p)
    monkeypatch.setattr(parses_db.store, "path", p)
    return p


def _grant_api(users_db: Path, discord_id: str = "discord-123") -> None:
    import sqlite3

    with sqlite3.connect(users_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_roles (discord_id, role, granted_by) VALUES (?, 'api', 'test')",
            (discord_id,),
        )


def _auth_patch():
    return patch("backend.server.api.export.require_user_session_or_token", _fake_require_user)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/export/v1/filters")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_export_requires_api_role(app):
    with _auth_patch():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/export/v1/filters")
    assert r.status_code == 403
    assert "api" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------


def _kill(kill_id: int, combatants: list[dict]) -> dict:
    return {
        "id": kill_id,
        "scope": "raid",
        "zone": "Veeshan's Peak",
        "title": "Phara Dar",
        "player_count": 24,
        "started_at": 1_788_000_000,
        "duration_s": 300,
        "combatants": combatants,
    }


def _combatant(name: str, cls: str, encdps: float) -> dict:
    return {"name": name, "cls": cls, "ally": 1, "encdps": encdps, "enchps": 10.0, "ilvl": 350.0, "level": 80}


@pytest.mark.asyncio
async def test_export_rankings_rows_and_class_filter(app, users_db):
    _grant_api(users_db)
    kills = [
        _kill(1, [_combatant("Topwiz", "Wizard", 50000), _combatant("Healy", "Templar", 4000)]),
        _kill(2, [_combatant("Topwiz", "Wizard", 61000), _combatant("Otherwiz", "Wizard", 30000)]),
    ]
    with _auth_patch(), patch("backend.server.api.export._cached_kills", return_value=kills):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(
                "/api/export/v1/rankings",
                params={"size": "raid", "zone": "Veeshan's Peak", "boss": "Phara Dar", "metric": "dps"},
            )
            r_wiz = await c.get(
                "/api/export/v1/rankings",
                params={
                    "size": "raid",
                    "zone": "Veeshan's Peak",
                    "boss": "Phara Dar",
                    "metric": "dps",
                    "class": "Wizard",
                },
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema_version"] == 1
    assert [row["name"] for row in body["rows"]] == ["Topwiz", "Otherwiz", "Healy"]
    top = body["rows"][0]
    assert top["rank"] == 1 and top["value"] == 61000  # best parse per character
    assert top["encounter_id"] == 2 and top["percentile"] == 100
    assert [row["name"] for row in r_wiz.json()["rows"]] == ["Topwiz", "Otherwiz"]


@pytest.mark.asyncio
async def test_export_rankings_rejects_bad_metric(app, users_db):
    _grant_api(users_db)
    with _auth_patch():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(
                "/api/export/v1/rankings",
                params={"size": "raid", "zone": "Z", "boss": "B", "metric": "speed"},
            )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Abilities — end-to-end through the real ingest path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_abilities_end_to_end(app, users_db, isolated_parses_db):
    _grant_api(users_db)
    payload = _minimal_payload(encid="EXPORT01")
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ingest = await c.post("/api/parses/ingest", **_signed_post_kwargs(payload))
    assert ingest.status_code == 201, ingest.text
    enc_id = ingest.json()["encounter_id"]

    with _auth_patch():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"/api/export/v1/parses/{enc_id},999999/abilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["missing"] == [999999]
    assert len(body["parses"]) == 1
    parse = body["parses"][0]
    assert parse["id"] == enc_id and parse["title"] == "a krait patriarch"
    # Player combatants only — the krait patriarch (enemy) is excluded.
    assert [p["name"] for p in parse["players"]] == ["Menludiir"]
    player = parse["players"][0]
    assert player["cls"] is None or isinstance(player["cls"], str)
    ability_names = {a["name"] for a in player["abilities"]}
    assert "Smite" in ability_names
    assert "All" not in ability_names  # the rollup row never reaches the DB
    heal_names = {a["name"] for a in player["heals"]}
    assert "Reverence" in heal_names
    # No uploader identity anywhere in the response.
    assert "source_dsn" not in r.text and "discord" not in r.text


@pytest.mark.asyncio
async def test_export_abilities_validates_ids(app, users_db):
    _grant_api(users_db)
    with _auth_patch():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r_bad = await c.get("/api/export/v1/parses/notanid/abilities")
            r_many = await c.get("/api/export/v1/parses/" + ",".join(map(str, range(21))) + "/abilities")
    assert r_bad.status_code == 400
    assert r_many.status_code == 400
