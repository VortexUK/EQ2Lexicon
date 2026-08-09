-- SQL for backend/server/db/downloads.py (async aiosqlite).

-- Record a download click. UNIQUE(discord_id, slug) makes this idempotent per
-- user, so the public count is distinct-downloaders and can't be inflated by
-- one person re-clicking. rowcount 0 ⇒ this user already recorded this slug.
-- :name insert_download
INSERT OR IGNORE INTO download_events (discord_id, slug) VALUES (?, ?);

-- :name count_for_slug
SELECT COUNT(*) FROM download_events WHERE slug = ?;

-- :name count_all
SELECT slug, COUNT(*) AS n FROM download_events GROUP BY slug;
