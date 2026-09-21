"""Parse poster — fight selection + dedup watermark + pure embed rendering.

The cog's Discord half is a thin adapter; what's tested here is the sync
selection helper (seeded parses DB, mirror-grouping, watermark semantics)
and the pure ``build_parse_post`` embed builder in render.py.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.bot.cogs.parse_posts import collect_new_fights, site_url_for
from backend.bot.render import build_parse_post, fmt_compact
from backend.server.parses import db as parses_db

NOW = 2_000_000_000


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Temp parses DB wired into the module store (what collect reads)."""
    db_path = tmp_path / "parses.db"
    monkeypatch.setattr(parses_db.store, "path", db_path)
    conn = parses_db.ParsesStore(db_path).init_db()
    yield conn
    conn.close()


def _insert_fight(
    conn: sqlite3.Connection,
    *,
    title: str = "Trakanon",
    started_at: int = NOW,
    uploaded_by: str = "RaiderA",
    duration_s: int = 180,
    success_level: int = 1,
    ingested_at: int | None = None,
    players: int = 8,
    world: str = "Varsoon",
    guild: str = "Exordium",
) -> int:
    cur = conn.execute(
        "INSERT INTO encounters (world, act_encid, title, zone, started_at, ended_at, duration_s, "
        "success_level, source_dsn, uploaded_by, guild_name, ingested_at) "
        "VALUES (?, ?, ?, 'Trakanon''s Lair', ?, ?, ?, ?, 'eq2act', ?, ?, ?)",
        (
            world,
            f"enc-{started_at}-{uploaded_by}",
            title,
            started_at,
            started_at + duration_s,
            duration_s,
            success_level,
            uploaded_by,
            guild,
            ingested_at if ingested_at is not None else started_at + duration_s,
        ),
    )
    enc_id = int(cur.lastrowid or 0)
    # is_player set explicitly so classification never reruns in tests; the
    # same roster on every upload satisfies the mirror top-N gate.
    for i in range(players):
        healer = i % 2 == 1
        conn.execute(
            "INSERT INTO combatants (encounter_id, name, ally, is_player, cls, encdps, enchps) "
            "VALUES (?, ?, 1, 1, ?, ?, ?)",
            (enc_id, f"Player{i}", "Templar" if healer else "Wizard", 1_000_000 - i * 50_000, 300_000 if healer else 0),
        )
    conn.commit()
    return enc_id


def test_collect_groups_mirrors_into_one_post(seeded_db):
    _insert_fight(seeded_db, uploaded_by="RaiderA", duration_s=170)
    _insert_fight(seeded_db, started_at=NOW + 10, uploaded_by="RaiderB", duration_s=180)
    fights = collect_new_fights("Varsoon", "Exordium", 0, NOW + 3600)
    assert len(fights) == 1
    fight, players = fights[0]
    assert len(fight["uploads"]) == 2
    assert fight["duration_s"] == 180  # canonical = longest capture
    assert len(players) == 8


def test_collect_watermark_excludes_already_posted(seeded_db):
    _insert_fight(seeded_db)
    assert collect_new_fights("Varsoon", "Exordium", NOW + 3600, NOW + 7200) == []


def test_collect_straggler_mirror_of_posted_fight_not_reposted(seeded_db):
    """A late mirror regroups with its already-posted fight (LOOKBACK window)
    and the group's EARLIEST upload sits before the watermark — skipped."""
    _insert_fight(seeded_db, uploaded_by="RaiderA", ingested_at=NOW)
    _insert_fight(seeded_db, started_at=NOW + 10, uploaded_by="RaiderB", ingested_at=NOW + 600)
    assert collect_new_fights("Varsoon", "Exordium", NOW + 300, NOW + 3600) == []


def test_collect_filters_trash_and_small_groups(seeded_db):
    _insert_fight(seeded_db, title="a krait warrior")  # trash — lowercase article
    _insert_fight(seeded_db, started_at=NOW + 500, uploaded_by="RaiderB", players=5)  # group content
    assert collect_new_fights("Varsoon", "Exordium", 0, NOW + 3600) == []


def test_collect_other_guild_and_hidden_excluded(seeded_db):
    _insert_fight(seeded_db, guild="Other Guild")
    hidden = _insert_fight(seeded_db, started_at=NOW + 500, uploaded_by="RaiderB")
    seeded_db.execute("UPDATE encounters SET hidden_at = ? WHERE id = ?", (NOW, hidden))
    seeded_db.commit()
    assert collect_new_fights("Varsoon", "Exordium", 0, NOW + 3600) == []


def test_collect_wipes_never_post(seeded_db):
    """Kills only — a progression night of wipes stays out of the channel."""
    _insert_fight(seeded_db, success_level=2)  # wipe
    _insert_fight(seeded_db, started_at=NOW + 500, uploaded_by="RaiderB", success_level=0)  # unknown
    assert collect_new_fights("Varsoon", "Exordium", 0, NOW + 3600) == []
    _insert_fight(seeded_db, started_at=NOW + 900, uploaded_by="RaiderC", success_level=1)
    assert len(collect_new_fights("Varsoon", "Exordium", 0, NOW + 3600)) == 1


def test_site_url_for(monkeypatch):
    monkeypatch.delenv("SESSION_COOKIE_DOMAIN", raising=False)
    assert site_url_for("Wuoshi", 5) is None
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".eq2lexicon.com")
    assert site_url_for("Wuoshi", 5) == "https://wuoshi.eq2lexicon.com/parse/5"


# ---------------------------------------------------------------------------
# Pure embed rendering
# ---------------------------------------------------------------------------


def test_fmt_compact():
    assert fmt_compact(0) == "0"
    assert fmt_compact(999) == "999"
    assert fmt_compact(1234) == "1.23k"
    assert fmt_compact(123_456) == "123k"
    assert fmt_compact(1_234_567) == "1.23M"
    assert fmt_compact(12_345_678) == "12.3M"
    assert fmt_compact(1_234_567_890) == "1.23B"


def _players() -> list[dict]:
    return [
        {"name": "Wizzy", "cls": "Wizard", "encdps": 1_500_000, "enchps": 0},
        {"name": "Healy", "cls": "Templar", "encdps": 100_000, "enchps": 800_000},
        {"name": "Beater", "cls": None, "encdps": 900_000, "enchps": 0},
    ]


def test_build_parse_post_kill():
    fight = {"title": "Trakanon", "zone": "Trakanon's Lair", "duration_s": 222, "success_level": 1, "uploads": [1, 2]}
    post = build_parse_post(fight, _players(), "https://wuoshi.example.com/parse/5")
    assert post.title.startswith("✅ Trakanon — 3m 42s")
    assert "(wipe)" not in post.title
    assert post.url is not None and post.url.endswith("/parse/5")
    assert "Trakanon's Lair · 3 players" in post.description
    assert "2.50M" in post.description  # raid DPS = 1.5M + 0.1M + 0.9M
    name, value, inline = post.fields[0]
    assert name == "Top DPS" and inline is True
    assert value.index("Wizzy") < value.index("Beater")  # ranked by encdps
    assert "Wizard" in value
    hps_value = post.fields[1][1]
    assert "Healy" in hps_value and "Wizzy" not in hps_value  # zero-HPS rows drop
    assert "2 uploads" in post.footer


def test_build_parse_post_wipe_and_empty_roster():
    fight = {"title": "Trakanon", "zone": None, "duration_s": 61, "success_level": 2, "uploads": [1]}
    post = build_parse_post(fight, [], None)
    assert post.title.startswith("💀") and post.title.endswith("(wipe)")
    assert post.fields[0][1] == "—" and post.fields[1][1] == "—"
    assert post.url is None
    assert "1 upload" in post.footer
