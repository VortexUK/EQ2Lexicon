"""servers registry store (sync) + schema invariants: the 0001 migration seeds
Varsoon (default) + Wuoshi, and re-running the seeds never clobbers an admin edit.
"""

from __future__ import annotations

from backend.server.db.servers import ServersStore
from tests.fixtures.pg import leaser, pg_conn


def _rerun_seeds(schema: str) -> None:
    """Re-apply the migration's idempotent ``-- seeds`` section, as a redeploy does."""
    with pg_conn(schema) as conn:
        conn.execute(leaser._seeds_sql())


def test_servers_seeded_and_lookups(users_schema):
    rows = ServersStore(users_schema).list_servers_sync()
    worlds = {r["world"] for r in rows}
    assert {"Varsoon", "Wuoshi"} <= worlds
    v = ServersStore(users_schema).get_server_by_subdomain_sync("varsoon")
    assert v is not None and v["world"] == "Varsoon"
    w = ServersStore(users_schema).get_server_by_world_sync("Wuoshi")
    assert w is not None and w["subdomain"] == "wuoshi"
    assert ServersStore(users_schema).get_server_by_subdomain_sync("nope") is None


def test_upsert_server_updates_settings(users_schema):
    ServersStore(users_schema).upsert_server_settings_sync(
        "Wuoshi", max_level=90, current_xpac="Sentinel's Fate", launch_dt="2026-07-01T18:00:00Z"
    )
    w = ServersStore(users_schema).get_server_by_world_sync("Wuoshi")
    assert w["max_level"] == 90
    assert w["current_xpac"] == "Sentinel's Fate"
    assert w["launch_dt"] == "2026-07-01T18:00:00Z"


def test_seed_rerun_preserves_upserted_settings(users_schema):
    ServersStore(users_schema).upsert_server_settings_sync(
        "Wuoshi", max_level=90, current_xpac="Sentinel's Fate", launch_dt=None
    )
    # A seeds re-run (e.g. redeploy re-applying the migration's idempotent
    # seeds) must not reset the admin edit.
    _rerun_seeds(users_schema)
    w = ServersStore(users_schema).get_server_by_world_sync("Wuoshi")
    assert w["max_level"] == 90
    assert w["current_xpac"] == "Sentinel's Fate"


def test_schema_carries_per_server_columns_and_indexes(users_schema):
    """The per-server columns and the
    world index are part of the schema outright — assert they exist.
    """
    with pg_conn(users_schema) as conn:
        claims_cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'character_claims'",
                (users_schema,),
            ).fetchall()
        }
        watch_cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'item_watch'",
                (users_schema,),
            ).fetchall()
        }
        idx = {
            r["indexname"]
            for r in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = 'character_claims'",
                (users_schema,),
            ).fetchall()
        }
    assert "world" in claims_cols and "is_primary" in claims_cols
    assert "world" in watch_cols
    assert "idx_claims_world" in idx


# ---------------------------------------------------------------------------
# is_default invariants
# ---------------------------------------------------------------------------


def test_exactly_one_default_server_seeded(users_schema):
    """The seeds
    must leave exactly one default row (Varsoon), and a seeds re-run must be
    idempotent — still exactly one default.
    """
    with pg_conn(users_schema) as conn:
        rows = conn.execute("SELECT world FROM servers WHERE is_default = 1").fetchall()
    assert [r["world"] for r in rows] == ["Varsoon"]

    _rerun_seeds(users_schema)
    with pg_conn(users_schema) as conn:
        rows = conn.execute("SELECT world FROM servers WHERE is_default = 1").fetchall()
    assert len(rows) == 1, f"After seed re-run, expected 1 default, got {rows}"


def test_set_default_server_clears_others(users_schema):
    """set_default_server_sync atomically swaps the default between servers."""
    # After seeding the default should be Varsoon.
    rows = ServersStore(users_schema).list_servers_sync()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1

    # Set Wuoshi as default — Varsoon must flip to 0.
    result = ServersStore(users_schema).set_default_server_sync("Wuoshi")
    assert result is True

    rows = ServersStore(users_schema).list_servers_sync()
    wuoshi = next(r for r in rows if r["world"] == "Wuoshi")
    varsoon = next(r for r in rows if r["world"] == "Varsoon")
    assert wuoshi["is_default"] is True
    assert varsoon["is_default"] is False

    # Flip back to Varsoon.
    result = ServersStore(users_schema).set_default_server_sync("Varsoon")
    assert result is True

    rows = ServersStore(users_schema).list_servers_sync()
    wuoshi = next(r for r in rows if r["world"] == "Wuoshi")
    varsoon = next(r for r in rows if r["world"] == "Varsoon")
    assert varsoon["is_default"] is True
    assert wuoshi["is_default"] is False

    # Unknown world: returns False and does NOT leave zero defaults.
    result = ServersStore(users_schema).set_default_server_sync("Nope")
    assert result is False
    rows = ServersStore(users_schema).list_servers_sync()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1, "Must always have exactly one default even after failed set"
