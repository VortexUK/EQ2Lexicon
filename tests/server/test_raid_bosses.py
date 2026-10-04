"""GET /api/zones/raid-bosses — the desktop parser's raid-boss sync — and
the cached name builder behind it."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api import rankings
from tests.fixtures.pg import pg_conn


def _seed(schema: str) -> None:
    """Seed the leased zones schema with two raid zones, one dungeon, and
    their encounter/mob rosters. Explicit encounter ids need OVERRIDING
    SYSTEM VALUE (identity column); positions satisfy UNIQUE(zone_id,
    position)."""
    with pg_conn(schema) as conn:
        conn.cursor().executemany(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, expansion_confidence) "
            "VALUES (%s, %s, %s, %s, %s, 'category')",
            [
                (1, "Raid Zone X4", "raid zone x4", "RoK", "Rise of Kunark"),
                (2, "Raid Zone X2", "raid zone x2", "RoK", "Rise of Kunark"),
                (3, "Dungeon Zone", "dungeon zone", "RoK", "Rise of Kunark"),
            ],
        )
        conn.cursor().executemany(
            "INSERT INTO zone_types (zone_id, type) VALUES (%s, %s)",
            [(1, "raid_x4"), (2, "raid_x2"), (3, "dungeon"), (1, "instance")],
        )
        conn.cursor().executemany(
            "INSERT INTO zone_encounters (id, zone_id, encounter_name, position) "
            "OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s)",
            [(10, 1, "Trakanon", 1), (11, 1, "Hoshkar", 2), (20, 2, "Herald", 1), (30, 3, "Dungeon Boss", 1)],
        )
        conn.cursor().executemany(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower) VALUES (%s, %s, %s)",
            [
                (10, "trakanon", "trakanon"),
                (11, "hoshkar", "hoshkar"),
                (11, "Silverwing", "Silverwing"),  # curator typo in case — normalised
                (20, "the herald of wuoshi’s", "the herald of wuoshi’s"),  # curly apostrophe → '
                (20, "trakanon", "trakanon"),  # duplicate across zones collapses
                (30, "dungeon boss", "dungeon boss"),  # not a raid zone → excluded
            ],
        )


@pytest.fixture
def raid_db(zones_schema):
    _seed(zones_schema)
    rankings._raid_boss_names.cache_clear()
    yield zones_schema
    rankings._raid_boss_names.cache_clear()


def test_names_are_raid_only_normalised_deduped_and_sorted(raid_db):
    assert rankings._raid_boss_names() == ("hoshkar", "silverwing", "the herald of wuoshi's", "trakanon")


def test_unseeded_zones_schema_is_empty(zones_schema):
    """No curated rosters at all (the old missing-zones.db case) → empty."""
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
    with pg_conn(raid_db) as conn:
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower) "
            "VALUES (10, 'new boss', 'new boss')"
        )
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
