"""GET /api/zones/raid-bosses — the desktop parser's raid-boss sync — and
the cached name builder behind it."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.eq2db.zones import catalogue as zones_db
from backend.server.api import rankings

# Only the columns the query touches — the real schema has many more.
_MINI_SCHEMA = """
CREATE TABLE zones (id INTEGER PRIMARY KEY);
CREATE TABLE zone_types (zone_id INTEGER, type TEXT);
CREATE TABLE zone_encounters (id INTEGER PRIMARY KEY, zone_id INTEGER);
CREATE TABLE zone_encounter_mobs (encounter_id INTEGER, mob_name_lower TEXT);
"""


def _seed(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_MINI_SCHEMA)
        conn.executemany("INSERT INTO zones VALUES (?)", [(1,), (2,), (3,)])
        conn.executemany(
            "INSERT INTO zone_types VALUES (?, ?)",
            [(1, "raid_x4"), (2, "raid_x2"), (3, "dungeon"), (1, "instance")],
        )
        conn.executemany("INSERT INTO zone_encounters VALUES (?, ?)", [(10, 1), (11, 1), (20, 2), (30, 3)])
        conn.executemany(
            "INSERT INTO zone_encounter_mobs VALUES (?, ?)",
            [
                (10, "trakanon"),
                (11, "hoshkar"),
                (11, "Silverwing"),  # curator typo in case — normalised
                (20, "the herald of wuoshi’s"),  # curly apostrophe → '
                (20, "trakanon"),  # duplicate across zones collapses
                (30, "dungeon boss"),  # not a raid zone → excluded
            ],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def raid_db(tmp_path, monkeypatch):
    db = tmp_path / "zones.db"
    _seed(db)
    monkeypatch.setattr(zones_db, "path", db)
    rankings._raid_boss_names.cache_clear()
    yield db
    rankings._raid_boss_names.cache_clear()


def test_names_are_raid_only_normalised_deduped_and_sorted(raid_db):
    assert rankings._raid_boss_names() == ("hoshkar", "silverwing", "the herald of wuoshi's", "trakanon")


def test_missing_zones_db_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(zones_db, "path", tmp_path / "nope.db")
    rankings._raid_boss_names.cache_clear()
    try:
        assert rankings._raid_boss_names() == ()
        assert rankings.raid_boss_pack()["bosses"] == []
    finally:
        rankings._raid_boss_names.cache_clear()


def test_version_is_a_content_hash(raid_db):
    first = rankings.raid_boss_pack()
    assert first["version"] == rankings.raid_boss_pack()["version"]
    assert len(first["version"]) == 12
    # A roster edit changes the stamp once the cache is invalidated.
    conn = sqlite3.connect(raid_db)
    conn.execute("INSERT INTO zone_encounter_mobs VALUES (10, 'new boss')")
    conn.commit()
    conn.close()
    assert rankings.raid_boss_pack()["version"] == first["version"]  # cached
    rankings.invalidate_zones_cache()
    second = rankings.raid_boss_pack()
    assert second["version"] != first["version"]
    assert "new boss" in second["bosses"]


@pytest.mark.asyncio
async def test_route_is_public_and_cacheable(app, raid_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/zones/raid-bosses")
    assert r.status_code == 200
    body = r.json()
    assert body["bosses"] == ["hoshkar", "silverwing", "the herald of wuoshi's", "trakanon"]
    assert body["version"] == rankings.raid_boss_pack()["version"]
    assert r.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.asyncio
async def test_route_wins_over_the_zone_name_lookup(app):
    """/zones/raid-bosses must not fall into /zones/{name} (a 404 lookup)."""
    with patch("backend.server.api.zones.raid_boss_pack", return_value={"version": "abc", "bosses": []}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/raid-bosses")
    assert r.status_code == 200 and r.json() == {"version": "abc", "bosses": []}
