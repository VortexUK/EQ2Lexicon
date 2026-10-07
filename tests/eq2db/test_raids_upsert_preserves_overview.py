"""upsert_raid_zone treats None as "don't touch" for nullable columns, so the
strategy-edit path (which omits overview_md) can't wipe a zone overview.
"""

from __future__ import annotations

from backend.eq2db.raids import RaidCatalogue
from backend.eq2db.raids import catalogue as raids_db


def test_upsert_with_none_overview_preserves_existing_overview(raids_schema: str):
    """An upsert_raid_zone call WITHOUT overview_md (the strategy-write path)
    must NOT wipe an existing curator-written overview to NULL."""
    conn = RaidCatalogue(raids_schema).init_db()
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
            "SELECT overview_md, source FROM raid_zones WHERE zone_name = %s",
            ("Mistmoore's Inner Sanctum",),
        ).fetchone()
        assert row["overview_md"] == "Bring poison cures. Stagger interrupts on Mob B."
        assert row["source"] == raids_db.SOURCE_MANUAL

        # 2. Curator edits a boss strategy — _write_strategy_sync auto-creates
        #    the zone parent by calling upsert_raid_zone() WITHOUT overview_md.
        raids_db.upsert_raid_zone(
            conn,
            zone_name="Mistmoore's Inner Sanctum",
            expansion_short="EoF",
            source=raids_db.SOURCE_MANUAL,
            # overview_md not passed — defaults to None
        )
        row = conn.execute(
            "SELECT overview_md, source FROM raid_zones WHERE zone_name = %s",
            ("Mistmoore's Inner Sanctum",),
        ).fetchone()
        # overview_md is preserved, not nulled.
        assert row["overview_md"] == "Bring poison cures. Stagger interrupts on Mob B."
        assert row["source"] == raids_db.SOURCE_MANUAL
    finally:
        conn.close()


def test_upsert_with_none_access_md_preserves_existing(raids_schema: str):
    """Same defensive contract for access_md."""
    conn = RaidCatalogue(raids_schema).init_db()
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
        row = conn.execute("SELECT access_md FROM raid_zones WHERE zone_name = %s", ("X",)).fetchone()
        assert row["access_md"] == "Get to the back of the zone via the side passage."
    finally:
        conn.close()


def test_upsert_with_non_none_overview_overwrites(raids_schema: str):
    """The fix must NOT break the case where a caller DOES want to write
    a fresh overview_md — only None means 'don't touch'. A non-None
    value still overwrites the existing row.

    This is the wiki re-scrape case where source=SCRAPE hits an existing
    source=SCRAPE row: the markdown should be refreshed with the latest
    wiki content."""
    conn = RaidCatalogue(raids_schema).init_db()
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
        row = conn.execute("SELECT overview_md FROM raid_zones WHERE zone_name = %s", ("Y",)).fetchone()
        assert row["overview_md"] == "New wiki content"
    finally:
        conn.close()
