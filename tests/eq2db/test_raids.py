"""Tests for the manual-edit protection in census/raids_db helpers.

The encounter helper (upsert_raid_encounter) has always had this protection;
the zone helper (upsert_raid_zone) gained it alongside the wiki-seed ingest
pipeline so a re-scrape can't overwrite admin/officer-edited zone overviews.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.eq2db.raids import RaidCatalogue
from backend.eq2db.raids import catalogue as raids_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Per-test DB so writes don't leak. Returns the file path; callers open
    + close their own connection via RaidCatalogue(path).init_db()."""
    return tmp_path / "raids.db"


# ---------------------------------------------------------------------------
# upsert_raid_zone — SOURCE_MANUAL preserves markdown on re-scrape
# ---------------------------------------------------------------------------


def test_rescrape_preserves_manual_zone_markdown(db_path: Path):
    """A human-edited zone (overview/background/access) survives a later
    SOURCE_SCRAPE upsert intact. Wiki-owned fields (expansion, wiki_url,
    level_range, last_synced_at) do get refreshed."""
    conn = RaidCatalogue(db_path).init_db()
    try:
        # First write: simulate a route-level manual edit.
        raids_db.upsert_raid_zone(
            conn,
            zone_name="The Emerald Halls",
            expansion_short="EoF",
            overview_md="## Our composition\n\n- 2 tanks, 6 healers, 16 dps",
            background_md="Our notes",
            access_md="Our access",
            source=raids_db.SOURCE_MANUAL,
        )
        # Re-scrape with the same name but new wiki content.
        raids_db.upsert_raid_zone(
            conn,
            zone_name="The Emerald Halls",
            expansion_short="EoF",
            wiki_url="https://eq2.fandom.com/wiki/The_Emerald_Halls",
            overview_md="WIKI OVERVIEW",
            background_md="WIKI BACKGROUND",
            access_md="WIKI ACCESS",
            level_range="80",
            source=raids_db.SOURCE_SCRAPE,
        )
        row = conn.execute(
            "SELECT source, overview_md, background_md, access_md, "
            "  wiki_url, level_range, last_synced_at "
            "FROM raid_zones WHERE zone_name = ?",
            ("The Emerald Halls",),
        ).fetchone()
    finally:
        conn.close()

    # Manual edits survive untouched.
    assert row[0] == "manual"
    assert row[1] == "## Our composition\n\n- 2 tanks, 6 healers, 16 dps"
    assert row[2] == "Our notes"
    assert row[3] == "Our access"
    # Wiki-owned fields DID get refreshed (so wiki_url/level_range stay current).
    assert row[4] == "https://eq2.fandom.com/wiki/The_Emerald_Halls"
    assert row[5] == "80"
    assert row[6] is not None  # last_synced_at stamped


def test_rescrape_refreshes_when_existing_is_also_scrape(db_path: Path):
    """When the existing row is itself a scrape, a fresh scrape overwrites
    its markdown — that's how wiki edits propagate."""
    conn = RaidCatalogue(db_path).init_db()
    try:
        raids_db.upsert_raid_zone(
            conn,
            zone_name="The Emerald Halls",
            expansion_short="EoF",
            overview_md="OLD WIKI",
            source=raids_db.SOURCE_SCRAPE,
        )
        raids_db.upsert_raid_zone(
            conn,
            zone_name="The Emerald Halls",
            expansion_short="EoF",
            overview_md="NEW WIKI",
            source=raids_db.SOURCE_SCRAPE,
        )
        row = conn.execute(
            "SELECT overview_md FROM raid_zones WHERE zone_name = ?",
            ("The Emerald Halls",),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "NEW WIKI"


def test_manual_upsert_creates_new_row(db_path: Path):
    """A first-write with SOURCE_MANUAL inserts cleanly (no existing row to
    protect). Used by the lazy-create path in _write_overview_sync."""
    conn = RaidCatalogue(db_path).init_db()
    try:
        raids_db.upsert_raid_zone(
            conn,
            zone_name="Veeshan's Peak",
            expansion_short="RoK",
            overview_md="hello",
            source=raids_db.SOURCE_MANUAL,
        )
        row = conn.execute(
            "SELECT source, overview_md FROM raid_zones WHERE zone_name = ?",
            ("Veeshan's Peak",),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("manual", "hello")


# ---------------------------------------------------------------------------
# upsert_raid_encounter — already had this protection, just keep a
# regression around it.
# ---------------------------------------------------------------------------


def test_rescrape_preserves_manual_encounter_strategy(db_path: Path):
    conn = RaidCatalogue(db_path).init_db()
    try:
        zone_id = raids_db.upsert_raid_zone(
            conn,
            zone_name="The Emerald Halls",
            expansion_short="EoF",
            source=raids_db.SOURCE_SCRAPE,
        )
        # Manual edit of one encounter
        raids_db.upsert_raid_encounter(
            conn,
            raid_zone_id=zone_id,
            mob_name="Prince Thirneg",
            strategy_md="OUR STRAT",
            source=raids_db.SOURCE_MANUAL,
            edited_by="admin-1",
        )
        # Re-scrape comes in with fresh wiki content for the same encounter
        raids_db.upsert_raid_encounter(
            conn,
            raid_zone_id=zone_id,
            mob_name="Prince Thirneg",
            strategy_md="WIKI STRAT",
            source=raids_db.SOURCE_SCRAPE,
        )
        row = conn.execute(
            "SELECT source, strategy_md FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
            (zone_id, "prince thirneg"),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "manual"
    assert row[1] == "OUR STRAT"
