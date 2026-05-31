"""Unit tests for the zone classifier in web/routes/parses/list.py.

The classifier mirrors the rankings page's leaderboard predicate exactly:
a zone is 'on the leaderboard' iff (a) its type token is 'raid_x4' or
'dungeon' AND (b) it has ≥1 row in zone_encounters (a curator has
populated at least one boss for it). Anything else is 'other'.

The implementation reuses rankings._cached_zones_data so the classifier
and the rankings dropdowns are guaranteed in lockstep. Cache invalidation
rides invalidate_zones_cache() so admin curator edits propagate without
a separate hook.
"""

from __future__ import annotations

from unittest.mock import patch

from backend.server.api.parses.list import _classifier_cache_clear, _classify_zone


def _fake_trees(raid_zones: list[str], dungeon_zones: list[str]):
    """Mirror the (boss_index, raid_tree, dungeon_tree) shape that
    rankings._cached_zones_data returns. Tests only need the names."""
    return (
        {},
        [{"zone": z, "expansion": "EoF", "bosses": ["X"]} for z in raid_zones],
        [{"zone": z, "expansion": "EoF", "bosses": ["X"]} for z in dungeon_zones],
    )


def test_classify_returns_raid_for_raid_x4_with_bosses():
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees(["Castle Mistmoore"], ["Halls of Fate"]),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Castle Mistmoore") == "raid"


def test_classify_returns_dungeon_for_dungeon_with_bosses():
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees(["Castle Mistmoore"], ["Halls of Fate"]),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Halls of Fate") == "dungeon"


def test_classify_returns_other_for_unlisted_zone():
    # 'Antonica' is an open-world overland and has neither a raid_x4 nor
    # dungeon type — must classify Other regardless of what's in zones.db.
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees(["Castle Mistmoore"], ["Halls of Fate"]),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Antonica") == "other"


def test_classify_returns_other_for_zone_without_curated_bosses():
    # A 'dungeon'-type zone with zero zone_encounters rows does NOT appear in
    # the dungeon_tree (rankings._cached_zones_data's subquery requires
    # 'z.id IN (SELECT DISTINCT zone_id FROM zone_encounters)'). So our
    # classifier — which derives its map from that same tree — naturally
    # returns Other. This test pins that behaviour.
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees([], []),  # no zones populated yet
    ):
        _classifier_cache_clear()
        assert _classify_zone("Halls of Fate") == "other"


def test_classify_returns_other_for_none_or_empty():
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees(["Castle Mistmoore"], []),
    ):
        _classifier_cache_clear()
        assert _classify_zone(None) == "other"
        assert _classify_zone("") == "other"
        assert _classify_zone("(unknown zone)") == "other"


def test_classify_is_case_insensitive():
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees(["Castle Mistmoore"], []),
    ):
        _classifier_cache_clear()
        assert _classify_zone("CASTLE MISTMOORE") == "raid"
        assert _classify_zone("castle mistmoore") == "raid"
        assert _classify_zone("Castle MistmoorE") == "raid"


def test_classify_resolves_aliases():
    # When the parse's `zone` doesn't match a canonical name directly but
    # zones_db.find_by_name resolves it to one that's on the leaderboard,
    # the classifier should still bucket it correctly.
    with (
        patch(
            "backend.server.api.parses.list._cached_zones_data",
            return_value=_fake_trees(["Castle Mistmoore"], []),
        ),
        patch(
            "backend.server.api.parses.list.zones_db.find_by_name",
            return_value={"name": "Castle Mistmoore"},
        ),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Mistmoore Castle") == "raid"


def test_classify_falls_through_to_other_when_alias_misses():
    with (
        patch(
            "backend.server.api.parses.list._cached_zones_data",
            return_value=_fake_trees(["Castle Mistmoore"], []),
        ),
        patch(
            "backend.server.api.parses.list.zones_db.find_by_name",
            return_value=None,
        ),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Some Random Zone") == "other"


def test_classifier_cache_clear_picks_up_new_trees():
    # Curator adds bosses to a previously-empty dungeon → second classify
    # after cache_clear should now return 'dungeon'.
    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees([], []),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Halls of Fate") == "other"

    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees([], ["Halls of Fate"]),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Halls of Fate") == "dungeon"


def test_invalidate_zones_cache_also_clears_classifier_map():
    """The Phase 2 spec wires _classifier_cache_clear into
    rankings.invalidate_zones_cache so the 8 admin call sites in
    web/routes/zones_admin.py don't each need their own hook. Verify by
    populating the map, calling invalidate_zones_cache, repopulating with
    a different fake, and checking the new result wins."""
    from backend.server.api.rankings import invalidate_zones_cache

    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees(["Castle Mistmoore"], []),
    ):
        _classifier_cache_clear()
        assert _classify_zone("Castle Mistmoore") == "raid"

    invalidate_zones_cache()

    with patch(
        "backend.server.api.parses.list._cached_zones_data",
        return_value=_fake_trees([], []),
    ):
        # Cache is empty after invalidation, so the next classify rebuilds
        # from the new (empty) trees → Other.
        assert _classify_zone("Castle Mistmoore") == "other"
