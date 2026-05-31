-- SQL for backend/server/db/claims.py (async aiosqlite).
-- Claims are scoped to (discord_id, world).

-- :name list_active_claims
SELECT * FROM character_claims
WHERE discord_id = ? AND world = ? AND status IN ('approved', 'pending')
ORDER BY requested_at ASC, id ASC;

-- :name check_character_name_taken
SELECT discord_id FROM character_claims
WHERE character_name = ? AND world = ? AND status IN ('approved', 'pending');

-- :name withdraw_pending_claims_on_world
UPDATE character_claims SET status = 'withdrawn'
WHERE discord_id = ? AND world = ? AND status = 'pending';

-- :name submit_claim
INSERT INTO character_claims (discord_id, character_name, status, world)
VALUES (?, ?, 'pending', ?);

-- :name find_by_id
SELECT * FROM character_claims WHERE id = ?;

-- :name select_primary_and_world
SELECT is_primary, world FROM character_claims WHERE id = ? AND discord_id = ?;

-- :name withdraw_claim
UPDATE character_claims SET status = 'withdrawn', is_primary = 0
WHERE id = ? AND discord_id = ? AND status IN ('pending', 'approved');

-- Promote the oldest remaining approved character on the same world to primary.
-- :name promote_oldest_to_primary
UPDATE character_claims SET is_primary = 1
WHERE id = (
    SELECT id FROM character_claims
    WHERE discord_id = ? AND world = ? AND status = 'approved' AND id != ?
    ORDER BY requested_at ASC
    LIMIT 1
);

-- :name find_approved_claim_for_user_on_world
SELECT id FROM character_claims WHERE id = ? AND discord_id = ? AND world = ? AND status = 'approved';

-- :name clear_primary_on_world
UPDATE character_claims SET is_primary = 0 WHERE discord_id = ? AND world = ? AND status = 'approved';

-- :name set_primary_by_id
UPDATE character_claims SET is_primary = 1 WHERE id = ?;

-- :name find_claim_with_user
SELECT c.*, u.discord_name, u.discord_username, u.avatar
FROM character_claims c
LEFT JOIN users u ON u.discord_id = c.discord_id
WHERE c.id = ?;

-- :name list_claims
-- {where_sql} and {order} are composed by Python.
SELECT c.*, u.discord_name, u.discord_username, u.avatar
FROM character_claims c
LEFT JOIN users u ON u.discord_id = c.discord_id
{where_sql}
ORDER BY c.requested_at {order};

-- :name select_claim_user_and_world
SELECT discord_id, world FROM character_claims WHERE id = ?;

-- :name review_claim
UPDATE character_claims
SET status = ?,
    reviewed_at = strftime('%s','now'),
    reviewed_by = ?,
    note = ?
WHERE id = ?;

-- :name check_user_has_primary_on_world
SELECT id FROM character_claims
WHERE discord_id = ? AND world = ? AND status = 'approved' AND is_primary = 1 AND id != ?;

-- :name delete_claim
DELETE FROM character_claims WHERE id = ?;

-- :name delete_claims_for_user
DELETE FROM character_claims WHERE discord_id = ?;
