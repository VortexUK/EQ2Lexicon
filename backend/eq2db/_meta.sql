-- :name select_value
SELECT value FROM _meta WHERE key = ?

-- :name upsert
INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)
