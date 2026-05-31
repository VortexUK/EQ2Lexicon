"""Tests for the admin-curated featured-raid endpoints.

Endpoints covered:
  - GET    /api/raids/expansions                 (public)
  - GET    /api/raids/expansions/available       (admin)
  - POST   /api/raids/expansions/{expansion}     (admin)
  - DELETE /api/raids/expansions/{expansion}     (admin)
  - GET    /api/raids/zones?expansion=X          (public)
  - GET    /api/raids/zones/available?expansion= (admin)
  - POST   /api/raids/zones/{zone_name}          (admin)
  - DELETE /api/raids/zones/{zone_name}          (admin)

Most tests mock the zones_db helpers and assert the route layer's auth +
HTTP-status behaviour. The bottom section runs real end-to-end roundtrips
against a throwaway zones.db to verify the SQL contracts.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.auth_deps import require_admin

# A representative hydrated-zone shape — matches what the real helpers return.
_ZONE = {
    "id": 7,
    "name": "Veeshan's Peak",
    "name_lower": "veeshan's peak",
    "expansion_short": "RoK",
    "expansion_name": "Rise of Kunark",
    "types": ["raid_x4"],
    "aliases": [],
    "bosses": [],
}


@pytest.fixture
def admin_override(app):
    """Bypass require_admin by injecting a fake admin session."""
    app.dependency_overrides[require_admin] = lambda: {
        "id": "admin-1",
        "username": "admin",
        "is_admin": True,
    }
    yield
    app.dependency_overrides.pop(require_admin, None)


# ── Auth gating ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_expansions_is_public(app):
    """GET /raids/expansions is public — no auth required."""
    with patch(
        "backend.server.api.zones_admin.zones_db.list_featured_raid_expansions",
        return_value=[{"short": "RoK", "name": "Rise of Kunark", "year": 2007}],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/raids/expansions")
    assert r.status_code == 200
    assert r.json() == [{"short": "RoK", "name": "Rise of Kunark", "year": 2007}]


@pytest.mark.asyncio
async def test_list_zones_is_public(app):
    with patch(
        "backend.server.api.zones_admin.zones_db.list_featured_raid_zones",
        return_value=[],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/raids/zones?expansion=RoK")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_available_expansions_requires_admin(app):
    """No session → 401/403. No DB mocks needed — the gate fires before
    any helper runs."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/raids/expansions/available")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_available_zones_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/raids/zones/available?expansion=RoK")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_post_expansion_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/raids/expansions/RoK")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_delete_expansion_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/raids/expansions/RoK")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_post_zone_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/raids/zones/Veeshans%20Peak")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_delete_zone_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/raids/zones/Veeshans%20Peak")
    assert r.status_code in (401, 403)


# ── Happy paths + cache invalidation ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_expansion_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_featured_raid_expansion",
            return_value=True,
        ) as add_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/expansions/TSO")
    assert r.status_code == 200
    assert r.json()["expansion_short"] == "TSO"
    add_mock.assert_called_once_with("TSO")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_add_expansion_unknown_404(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_featured_raid_expansion",
            return_value=False,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/expansions/NOPE")
    assert r.status_code == 404
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_delete_expansion_returns_removed_flag(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_featured_raid_expansion",
            return_value=True,
        ) as rm_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/raids/expansions/RoK")
    assert r.status_code == 200
    assert r.json() == {"expansion_short": "RoK", "removed": True}
    rm_mock.assert_called_once_with("RoK")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_nonexistent_expansion_returns_removed_false(app, admin_override):
    """Removing nothing → 200, removed=false. Idempotent by design."""
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_featured_raid_expansion",
            return_value=False,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/raids/expansions/NOPE")
    assert r.status_code == 200
    assert r.json()["removed"] is False


@pytest.mark.asyncio
async def test_add_zone_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_featured_raid_zone",
            return_value=_ZONE,
        ) as add_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/zones/Veeshan's%20Peak")
    assert r.status_code == 200
    assert r.json()["name"] == "Veeshan's Peak"
    add_mock.assert_called_once_with("Veeshan's Peak")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_add_zone_unknown_or_not_raid_returns_400(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.add_featured_raid_zone",
            return_value=None,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/zones/Imaginary")
    assert r.status_code == 400
    assert "raid_x4" in r.json()["detail"]
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_delete_zone_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.remove_featured_raid_zone",
            return_value=True,
        ) as rm_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/raids/zones/Veeshan's%20Peak")
    assert r.status_code == 200
    assert r.json() == {"zone_name": "Veeshan's Peak", "removed": True}
    rm_mock.assert_called_once_with("Veeshan's Peak")
    invalidate_mock.assert_called_once()


# ── Real-helper roundtrip tests (no SQL mocks) ────────────────────────────────


def _seed_zones(db_path):
    """Insert a representative set of zones into a fresh zones.db."""
    from backend.eq2db import zones as zdb

    conn = zdb.init_db(db_path)
    try:
        # RoK raid + RoK non-raid + EoF raid (raid_x2) + TSO raid.
        conn.executemany(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_year, expansion_confidence) VALUES (?, ?, ?, ?, ?, ?, 'category')",
            [
                (1, "Veeshan's Peak", "veeshan's peak", "RoK", "Rise of Kunark", 2007),
                (2, "Karnor's Castle", "karnor's castle", "RoK", "Rise of Kunark", 2007),
                (3, "Mistmoore Catacombs", "mistmoore catacombs", "EoF", "Echoes of Faydwer", 2006),
                (4, "Munzok's Material Bastion", "munzok's material bastion", "TSO", "The Shadow Odyssey", 2008),
            ],
        )
        conn.executemany(
            "INSERT INTO zone_types (zone_id, type) VALUES (?, ?)",
            [
                (1, "raid_x4"),
                (3, "raid_x2"),
                (4, "raid_x4"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_add_then_list_then_remove_expansion_roundtrip(tmp_path):
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)

    # Initially nothing featured.
    assert zdb.list_featured_raid_expansions(db) == []
    assert {e["short"] for e in zdb.list_available_raid_expansions(db)} == {"RoK", "EoF", "TSO"}

    # Add TSO as a featured (empty) expansion.
    assert zdb.add_featured_raid_expansion("TSO", db) is True
    featured = zdb.list_featured_raid_expansions(db)
    assert [e["short"] for e in featured] == ["TSO"]
    # No longer in available.
    assert "TSO" not in {e["short"] for e in zdb.list_available_raid_expansions(db)}

    # Idempotent re-add returns True (already present is OK).
    assert zdb.add_featured_raid_expansion("TSO", db) is True

    # Unknown expansion returns False.
    assert zdb.add_featured_raid_expansion("NOPE", db) is False

    # Remove brings it back to available.
    assert zdb.remove_featured_raid_expansion("TSO", db) is True
    assert "TSO" in {e["short"] for e in zdb.list_available_raid_expansions(db)}

    # Idempotent re-remove returns False (nothing to delete).
    assert zdb.remove_featured_raid_expansion("TSO", db) is False


def test_add_zone_validates_raid_type(tmp_path):
    """Only raid_x4/raid_x2-tagged zones can be featured."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)

    # raid_x4 → OK
    z = zdb.add_featured_raid_zone("Veeshan's Peak", db)
    assert z is not None
    assert z["name"] == "Veeshan's Peak"

    # raid_x2 → OK
    z2 = zdb.add_featured_raid_zone("Mistmoore Catacombs", db)
    assert z2 is not None

    # Non-raid zone (Karnor's Castle has no type tags) → None
    assert zdb.add_featured_raid_zone("Karnor's Castle", db) is None

    # Unknown zone → None
    assert zdb.add_featured_raid_zone("Imaginary", db) is None


def test_featured_zones_imply_expansion_in_list(tmp_path):
    """Adding a featured zone whose expansion isn't explicitly featured
    must still surface that expansion in list_featured_raid_expansions
    (the implicit case)."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)

    # No featured_raid_expansions rows, but feature a RoK raid zone.
    assert zdb.add_featured_raid_zone("Veeshan's Peak", db) is not None

    expansions = zdb.list_featured_raid_expansions(db)
    shorts = [e["short"] for e in expansions]
    assert "RoK" in shorts

    # And RoK no longer appears in the "available" list (it's implicitly featured).
    available = {e["short"] for e in zdb.list_available_raid_expansions(db)}
    assert "RoK" not in available


def test_remove_expansion_cascades_to_featured_zones(tmp_path):
    """Removing an expansion wipes its featured_raid_zones rows, but the
    underlying zone_encounters data is left intact."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)

    # Seed a curator encounter for Veeshan's Peak so we can verify it survives.
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO zone_encounters (id, zone_id, encounter_name, position) VALUES (10, 1, 'Druushk', 1)")
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (10, 'Druushk', 'druushk', 0)"
        )
        conn.commit()

    # Feature the expansion + zone.
    assert zdb.add_featured_raid_expansion("RoK", db) is True
    assert zdb.add_featured_raid_zone("Veeshan's Peak", db) is not None
    assert len(zdb.list_featured_raid_zones("RoK", db)) == 1

    # Remove the expansion → zone disappears from the featured list.
    assert zdb.remove_featured_raid_expansion("RoK", db) is True
    assert zdb.list_featured_raid_zones("RoK", db) == []

    # But the underlying zone_encounters / mobs row is preserved.
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM zone_encounters WHERE id = 10").fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM zone_encounter_mobs WHERE encounter_id = 10").fetchone()[0]
    assert n == 1
    assert m == 1


def test_available_zones_excludes_featured(tmp_path):
    """Once a zone is featured, it's no longer offered in the 'add zone' picker."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)

    before = {z["name"] for z in zdb.list_available_raid_zones("RoK", db)}
    assert "Veeshan's Peak" in before
    assert "Karnor's Castle" not in before  # not raid-tagged

    assert zdb.add_featured_raid_zone("Veeshan's Peak", db) is not None
    after = {z["name"] for z in zdb.list_available_raid_zones("RoK", db)}
    assert "Veeshan's Peak" not in after


def test_remove_zone_preserves_encounters(tmp_path):
    """remove_featured_raid_zone wipes the featured row only — boss data
    in zone_encounters stays untouched."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)

    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO zone_encounters (id, zone_id, encounter_name, position) VALUES (11, 1, 'Phara Dar', 1)"
        )
        conn.execute(
            "INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) "
            "VALUES (11, 'Phara Dar', 'phara dar', 0)"
        )
        conn.commit()

    assert zdb.add_featured_raid_zone("Veeshan's Peak", db) is not None
    assert zdb.remove_featured_raid_zone("Veeshan's Peak", db) is True

    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM zone_encounters WHERE id = 11").fetchone()[0]
    assert n == 1


# ── Categories + drag-reorder ────────────────────────────────────────────────


def _seed_multi_raid_expansion(db_path):
    """Seed a zones.db with four raid_x4 zones in the RoK expansion. All four
    are featured under the (NULL = "Uncategorised") lane so the reorder
    tests have a baseline to permute."""
    from backend.eq2db import zones as zdb

    _seed_zones(db_path)
    # Add three more RoK raid_x4 zones so we have 4 to permute.
    conn = zdb.init_db(db_path)
    try:
        conn.executemany(
            "INSERT INTO zones (id, name, name_lower, expansion_short, expansion_name, "
            "expansion_year, expansion_confidence) VALUES (?, ?, ?, ?, ?, ?, 'category')",
            [
                (5, "Trakanon's Lair", "trakanon's lair", "RoK", "Rise of Kunark", 2007),
                (6, "Chardok: The Bloodied Halls", "chardok: the bloodied halls", "RoK", "Rise of Kunark", 2007),
                (7, "Sebilis", "sebilis", "RoK", "Rise of Kunark", 2007),
            ],
        )
        conn.executemany(
            "INSERT INTO zone_types (zone_id, type) VALUES (?, ?)",
            [(5, "raid_x4"), (6, "raid_x4"), (7, "raid_x4")],
        )
        conn.commit()
    finally:
        conn.close()
    # Feature all four (each lands at next position in the NULL lane).
    for n in ("Veeshan's Peak", "Trakanon's Lair", "Chardok: The Bloodied Halls", "Sebilis"):
        assert zdb.add_featured_raid_zone(n, db_path) is not None


def test_add_featured_raid_zone_assigns_increasing_positions_in_null_lane(tmp_path):
    """First add → position 0, second → 1, third → 2. All in the NULL lane."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)
    rows = zdb.list_featured_raid_zones("RoK", db)
    positions = [(r["name"], r["position"], r["category"]) for r in rows]
    # Sorted by (category, position) so NULL-category goes first in insertion order.
    assert positions == [
        ("Veeshan's Peak", 0, None),
        ("Trakanon's Lair", 1, None),
        ("Chardok: The Bloodied Halls", 2, None),
        ("Sebilis", 3, None),
    ]


def test_list_featured_raid_zones_sorted_by_category_then_position(tmp_path):
    """After categorising a subset, list returns NULL-lane first then
    categorised lanes ordered by (category, position)."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)

    # Put VP + Chardok in "Wing A" (positions 0, 1), leave the rest NULL.
    ok = zdb.reorder_featured_raid_zones(
        "RoK",
        [
            {"name": "Trakanon's Lair", "category": None, "position": 0},
            {"name": "Sebilis", "category": None, "position": 1},
            {"name": "Veeshan's Peak", "category": "Wing A", "position": 0},
            {"name": "Chardok: The Bloodied Halls", "category": "Wing A", "position": 1},
        ],
        db,
    )
    assert ok is True
    rows = zdb.list_featured_raid_zones("RoK", db)
    seq = [(r["name"], r["category"], r["position"]) for r in rows]
    # NULL first (positions 0,1) then "Wing A" (positions 0,1).
    assert seq == [
        ("Trakanon's Lair", None, 0),
        ("Sebilis", None, 1),
        ("Veeshan's Peak", "Wing A", 0),
        ("Chardok: The Bloodied Halls", "Wing A", 1),
    ]


def test_reorder_zones_autocreates_missing_categories(tmp_path):
    """A category name that appears in `ordering` but isn't yet tracked in
    featured_raid_categories is inserted at MAX(position)+1."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)
    assert zdb.list_featured_raid_categories("RoK", db) == []

    ok = zdb.reorder_featured_raid_zones(
        "RoK",
        [
            {"name": "Veeshan's Peak", "category": "Wing A", "position": 0},
            {"name": "Trakanon's Lair", "category": "Wing B", "position": 0},
            {"name": "Chardok: The Bloodied Halls", "category": None, "position": 0},
            {"name": "Sebilis", "category": None, "position": 1},
        ],
        db,
    )
    assert ok is True
    cats = zdb.list_featured_raid_categories("RoK", db)
    names = sorted(c["name"] for c in cats)
    assert names == ["Wing A", "Wing B"]
    # Each has a distinct position (auto-create assigns MAX+1).
    positions = sorted(c["position"] for c in cats)
    assert positions[0] != positions[1]


def test_reorder_zones_returns_false_for_unfeatured_zone(tmp_path):
    """A zone in `ordering` that isn't in featured_raid_zones for the
    expansion → reorder returns False (route layer maps to 400)."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)

    ok = zdb.reorder_featured_raid_zones(
        "RoK",
        [{"name": "Imaginary Zone", "category": None, "position": 0}],
        db,
    )
    assert ok is False


def test_reorder_categories_atomic_two_phase(tmp_path):
    """Reordering categories rewrites position via the two-phase pattern —
    no UNIQUE/ordering collision possible even when swapping positions."""
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)

    # Create three categories at positions 0, 1, 2.
    zdb.reorder_featured_raid_zones(
        "RoK",
        [
            {"name": "Veeshan's Peak", "category": "A", "position": 0},
            {"name": "Trakanon's Lair", "category": "B", "position": 0},
            {"name": "Chardok: The Bloodied Halls", "category": "C", "position": 0},
            {"name": "Sebilis", "category": None, "position": 0},
        ],
        db,
    )
    cats_before = {c["name"]: c["position"] for c in zdb.list_featured_raid_categories("RoK", db)}
    assert len(cats_before) == 3

    # Reverse the order: C → 0, B → 1, A → 2.
    ok = zdb.reorder_featured_raid_categories(
        "RoK",
        [
            {"name": "C", "position": 0},
            {"name": "B", "position": 1},
            {"name": "A", "position": 2},
        ],
        db,
    )
    assert ok is True
    cats_after = sorted(zdb.list_featured_raid_categories("RoK", db), key=lambda r: r["position"])
    assert [c["name"] for c in cats_after] == ["C", "B", "A"]


def test_reorder_categories_returns_false_for_missing_category(tmp_path):
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)
    ok = zdb.reorder_featured_raid_categories(
        "RoK",
        [{"name": "DoesNotExist", "position": 0}],
        db,
    )
    assert ok is False


# ── Endpoint tests for reorder + categories ──────────────────────────────────


@pytest.mark.asyncio
async def test_get_categories_is_public(app):
    """GET /raids/categories is a public read."""
    with patch(
        "backend.server.api.zones_admin.zones_db.list_featured_raid_categories",
        return_value=[{"name": "Wing A", "position": 0}],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/raids/categories?expansion=RoK")
    assert r.status_code == 200
    assert r.json() == [{"name": "Wing A", "position": 0}]


@pytest.mark.asyncio
async def test_reorder_zones_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/raids/zones/reorder",
            json={"expansion": "RoK", "zones": []},
        )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_reorder_categories_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put(
            "/api/raids/categories/reorder",
            json={"expansion": "RoK", "categories": []},
        )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_reorder_zones_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.reorder_featured_raid_zones",
            return_value=True,
        ) as ro_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/raids/zones/reorder",
                json={
                    "expansion": "RoK",
                    "zones": [
                        {"name": "Veeshan's Peak", "category": None, "position": 0},
                        {"name": "Trakanon's Lair", "category": "Wing A", "position": 0},
                    ],
                },
            )
    assert r.status_code == 200
    body = r.json()
    assert body == {"expansion": "RoK", "reordered": 2}
    ro_mock.assert_called_once()
    args, _ = ro_mock.call_args
    assert args[0] == "RoK"
    assert args[1] == [
        {"name": "Veeshan's Peak", "category": None, "position": 0},
        {"name": "Trakanon's Lair", "category": "Wing A", "position": 0},
    ]
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_reorder_zones_returns_400_on_missing(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.reorder_featured_raid_zones",
            return_value=False,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/raids/zones/reorder",
                json={
                    "expansion": "RoK",
                    "zones": [{"name": "Imaginary", "category": None, "position": 0}],
                },
            )
    assert r.status_code == 400
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_reorder_categories_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.reorder_featured_raid_categories",
            return_value=True,
        ) as ro_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/raids/categories/reorder",
                json={
                    "expansion": "RoK",
                    "categories": [
                        {"name": "Wing B", "position": 0},
                        {"name": "Wing A", "position": 1},
                    ],
                },
            )
    assert r.status_code == 200
    assert r.json() == {"expansion": "RoK", "reordered": 2}
    ro_mock.assert_called_once()
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_reorder_categories_returns_400_on_missing(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.reorder_featured_raid_categories",
            return_value=False,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/raids/categories/reorder",
                json={
                    "expansion": "RoK",
                    "categories": [{"name": "Missing", "position": 0}],
                },
            )
    assert r.status_code == 400
    invalidate_mock.assert_not_called()


# ── Create + delete category endpoints ───────────────────────────────────────


@pytest.mark.asyncio
async def test_create_category_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/raids/categories?expansion=RoK", json={"name": "Wing A"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_delete_category_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/raids/categories?expansion=RoK&name=Wing+A")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_category_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.create_featured_raid_category",
            return_value=True,
        ) as create_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/categories?expansion=RoK", json={"name": "Wing A"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"expansion": "RoK", "name": "Wing A"}
    create_mock.assert_called_once_with("RoK", "Wing A")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_create_category_conflict_returns_409(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.create_featured_raid_category",
            return_value=False,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/categories?expansion=RoK", json={"name": "Wing A"})
    assert r.status_code == 409
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_category_rejects_empty_name(app, admin_override):
    with patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/categories?expansion=RoK", json={"name": "   "})
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_category_rejects_name_too_long(app, admin_override):
    with patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/raids/categories?expansion=RoK", json={"name": "x" * 65})
    assert r.status_code == 400
    assert "64" in r.json()["detail"]
    invalidate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_delete_category_happy_path(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.delete_featured_raid_category",
            return_value=True,
        ) as delete_mock,
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/raids/categories?expansion=RoK&name=Wing+A")
    assert r.status_code == 200
    body = r.json()
    assert body == {"expansion": "RoK", "name": "Wing A", "removed": True}
    delete_mock.assert_called_once_with("RoK", "Wing A")
    invalidate_mock.assert_called_once()


@pytest.mark.asyncio
async def test_delete_category_nonexistent_returns_removed_false(app, admin_override):
    with (
        patch(
            "backend.server.api.zones_admin.zones_db.delete_featured_raid_category",
            return_value=False,
        ),
        patch("backend.server.api.zones_admin.invalidate_zones_cache") as invalidate_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/raids/categories?expansion=RoK&name=NoSuch")
    assert r.status_code == 200
    assert r.json()["removed"] is False
    invalidate_mock.assert_called_once()


# ── Real-helper roundtrip tests for create + delete category ─────────────────


def test_create_featured_raid_category_roundtrip(tmp_path):
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)
    assert zdb.add_featured_raid_expansion("RoK", db) is True

    # Create a new category.
    assert zdb.create_featured_raid_category("RoK", "Tier 1", db) is True
    cats = zdb.list_featured_raid_categories("RoK", db)
    assert any(c["name"] == "Tier 1" for c in cats)

    # Duplicate returns False.
    assert zdb.create_featured_raid_category("RoK", "Tier 1", db) is False

    # Second category gets MAX+1 position.
    assert zdb.create_featured_raid_category("RoK", "Tier 2", db) is True
    cats2 = zdb.list_featured_raid_categories("RoK", db)
    tier1_pos = next(c["position"] for c in cats2 if c["name"] == "Tier 1")
    tier2_pos = next(c["position"] for c in cats2 if c["name"] == "Tier 2")
    assert tier2_pos > tier1_pos


def test_delete_featured_raid_category_moves_zones_to_null(tmp_path):
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_multi_raid_expansion(db)

    # Put VP in "Wing A".
    zdb.reorder_featured_raid_zones(
        "RoK",
        [
            {"name": "Veeshan's Peak", "category": "Wing A", "position": 0},
            {"name": "Trakanon's Lair", "category": None, "position": 0},
            {"name": "Chardok: The Bloodied Halls", "category": None, "position": 1},
            {"name": "Sebilis", "category": None, "position": 2},
        ],
        db,
    )
    assert any(c["name"] == "Wing A" for c in zdb.list_featured_raid_categories("RoK", db))

    # Delete the category.
    assert zdb.delete_featured_raid_category("RoK", "Wing A", db) is True
    # Category row is gone.
    assert not any(c["name"] == "Wing A" for c in zdb.list_featured_raid_categories("RoK", db))
    # VP is now in the NULL lane.
    rows = zdb.list_featured_raid_zones("RoK", db)
    vp = next(r for r in rows if r["name"] == "Veeshan's Peak")
    assert vp["category"] is None

    # Idempotent re-delete returns False.
    assert zdb.delete_featured_raid_category("RoK", "Wing A", db) is False


def test_delete_featured_raid_category_nonexistent_returns_false(tmp_path):
    from backend.eq2db import zones as zdb

    db = tmp_path / "zones.db"
    _seed_zones(db)
    zdb.init_db(db)
    assert zdb.delete_featured_raid_category("RoK", "DoesNotExist", db) is False
