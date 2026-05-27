from __future__ import annotations

from web import db


def test_servers_seeded_and_lookups(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    rows = db.list_servers_sync(p)
    worlds = {r["world"] for r in rows}
    assert {"Varsoon", "Wuoshi"} <= worlds
    v = db.get_server_by_subdomain_sync("varsoon", p)
    assert v is not None and v["world"] == "Varsoon"
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w is not None and w["subdomain"] == "wuoshi"
    assert db.get_server_by_subdomain_sync("nope", p) is None


def test_upsert_server_updates_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    db.upsert_server_settings_sync(
        "Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt="2026-07-01T18:00:00Z", path=p
    )
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w["max_level"] == 70
    assert w["current_xpac"] == "Sentinel's Fate"
    assert w["launch_dt"] == "2026-07-01T18:00:00Z"


def test_second_init_db_preserves_upserted_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    db.upsert_server_settings_sync("Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt=None, path=p)
    # A second init_db (e.g. container restart) must not reset the admin edit.
    db.init_db(p)
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w["max_level"] == 70
    assert w["current_xpac"] == "Sentinel's Fate"
