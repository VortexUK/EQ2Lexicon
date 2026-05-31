-- SQL for backend/server/db/tokens.py (async aiosqlite).
-- API tokens are hashed (sha256); raw token is shown once at mint time.

-- :name mint_token
INSERT INTO api_tokens (user_id, name, token_hash, token_prefix)
VALUES (?, ?, ?, ?);

-- :name find_by_id
SELECT * FROM api_tokens WHERE id = ?;

-- :name list_for_user
-- Hash + user_id omitted from the SELECT — UI doesn't need them.
SELECT id, name, token_prefix, created_at, last_used_at, revoked_at
FROM api_tokens
WHERE user_id = ?
ORDER BY created_at DESC;

-- :name revoke_token
UPDATE api_tokens
SET revoked_at = strftime('%s','now')
WHERE id = ? AND user_id = ? AND revoked_at IS NULL;

-- :name lookup_by_hash
SELECT t.id AS token_id, t.user_id, t.name AS token_name, t.revoked_at,
       t.last_used_at,
       u.discord_id, u.discord_name, u.discord_username, u.avatar,
       u.access_status
FROM api_tokens t
JOIN users u ON u.discord_id = t.user_id
WHERE t.token_hash = ?;

-- :name update_last_used_at
UPDATE api_tokens SET last_used_at = ? WHERE id = ?;
