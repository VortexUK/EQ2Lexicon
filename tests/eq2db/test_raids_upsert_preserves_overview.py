"""Regression test for the 2026-05-30 'losing raid zone overviews' bug.

When _write_strategy_sync (the encounter-strategy edit path) calls
raids_db.upsert_raid_zone(... source=MANUAL) to auto-create the parent
zone row, it does NOT pass overview_md — so the default (None) flowed
into the ON CONFLICT DO UPDATE clause and clobbered the zone's existing
overview_md with NULL.

The fix wraps every nullable column in COALESCE so None means
'don't touch' instead of 'clobber to NULL'. Destructive writes go
through targeted UPDATEs (e.g. _update_overview_sync), not through
upsert_raid_zone.
"""

from __future__ import annotations

from backend.eq2db import raids as raids_db


def test_upsert_with_none_overview_preserves_existing_overview(tmp_path):
    """The regression scenario: an existing zone has a curator-written
    overview_md. A subsequent upsert_raid_zone call WITHOUT overview_md
    (the strategy-write path) must NOT wipe it to NULL."""
    db = tmp_path / "raids.db"
    conn = raids_db.init_db(db)
    try:
        # 1. Curator writes an overview — overview_md is set.
        raids_db.upsert_raid_zone(
            conn,
            zone_name="Mistmoore's Inner Sanctum",
            expansion_short="EoF",
            overview_md="Bring poison cures. Stagger interrupts on Mob B.",
            source=raids_db.SOURCE_MANUAL,
        )
        row = conn.execute(
            "SELECT overview_md, source FROM raid_zones WHERE zone_name = ?",
            ("Mistmoore's Inner Sanctum",),
        ).fetchone()
        assert row[0] == "Bring poison cures. Stagger interrupts on Mob B."
        assert row[1] == raids_db.SOURCE_MANUAL

        # 2. Curator edits a boss strategy — _write_strategy_sync auto-creates
        #    the zone parent by calling upsert_raid_zone() WITHOUT overview_md.
        #    Before the fix, this nulled overview_md to NULL.
        raids_db.upsert_raid_zone(
            conn,
            zone_name="Mistmoore's Inner Sanctum",
            expansion_short="EoF",
            source=raids_db.SOURCE_MANUAL,
            # overview_md not passed — defaults to None
        )
        row = conn.execute(
            "SELECT overview_md, source FROM raid_zones WHERE zone_name = ?",
            ("Mistmoore's Inner Sanctum",),
        ).fetchone()
        # The fix: overview_md is preserved, not nulled.
        assert row[0] == "Bring poison cures. Stagger interrupts on Mob B."
        assert row[1] == raids_db.SOURCE_MANUAL
    finally:
        conn.close()


def test_upsert_with_none_access_md_preserves_existing(tmp_path):
    """Same defensive contract for access_md."""
    db = tmp_path / "raids.db"
    conn = raids_db.init_db(db)
    try:
        raids_db.upsert_raid_zone(
            conn,
            zone_name="X",
            expansion_short="EoF",
            access_md="Get to the back of the zone via the side passage.",
            source=raids_db.SOURCE_MANUAL,
        )
        raids_db.upsert_raid_zone(
            conn,
            zone_name="X",
            expansion_short="EoF",
            source=raids_db.SOURCE_MANUAL,
        )
        row = conn.execute("SELECT access_md FROM raid_zones WHERE zone_name = ?", ("X",)).fetchone()
        assert row[0] == "Get to the back of the zone via the side passage."
    finally:
        conn.close()


def test_upsert_with_non_none_overview_overwrites(tmp_path):
    """The fix must NOT break the case where a caller DOES want to write
    a fresh overview_md — only None means 'don't touch'. A non-None
    value still overwrites the existing row.

    This is the wiki re-scrape case where source=SCRAPE hits an existing
    source=SCRAPE row: the markdown should be refreshed with the latest
    wiki content."""
    db = tmp_path / "raids.db"
    conn = raids_db.init_db(db)
    try:
        raids_db.upsert_raid_zone(
            conn,
            zone_name="Y",
            expansion_short="EoF",
            overview_md="Old wiki content",
            source=raids_db.SOURCE_SCRAPE,
        )
        raids_db.upsert_raid_zone(
            conn,
            zone_name="Y",
            expansion_short="EoF",
            overview_md="New wiki content",
            source=raids_db.SOURCE_SCRAPE,
        )
        row = conn.execute("SELECT overview_md FROM raid_zones WHERE zone_name = ?", ("Y",)).fetchone()
        assert row[0] == "New wiki content"
    finally:
        conn.close()
