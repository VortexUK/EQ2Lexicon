-- SQL for backend/server/db/users.py (async aiosqlite).
-- Domain: users + user_roles + role_requests + role_permissions.

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------

-- :name upsert_user
INSERT INTO users (discord_id, discord_name, discord_username, avatar, access_status)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(discord_id) DO UPDATE SET
    discord_name     = excluded.discord_name,
    discord_username = excluded.discord_username,
    avatar           = excluded.avatar,
    last_seen        = strftime('%s','now'),
    access_status    = CASE
        WHEN ? = 1 THEN 'approved'
        ELSE access_status
    END;

-- :name select_access_status
SELECT access_status FROM users WHERE discord_id = ?;

-- :name select_display_names_by_ids
-- {placeholders} = "?,?,..." sized at call time
SELECT discord_id, discord_name FROM users WHERE discord_id IN ({placeholders});

-- :name list_pending_users
SELECT discord_id, discord_name, discord_username, avatar, first_seen
FROM users WHERE access_status = 'pending' ORDER BY first_seen DESC;

-- :name list_all_users_with_claim_count
SELECT u.discord_id, u.discord_name, u.discord_username, u.avatar,
       u.first_seen, u.last_seen, u.access_status,
       COUNT(c.id) AS claim_count
FROM users u
LEFT JOIN character_claims c ON c.discord_id = u.discord_id
GROUP BY u.discord_id
ORDER BY u.first_seen DESC;

-- :name update_user_access_status
UPDATE users SET access_status = ? WHERE discord_id = ?;

-- ---------------------------------------------------------------------------
-- user_roles
-- ---------------------------------------------------------------------------

-- :name grant_role
INSERT OR IGNORE INTO user_roles (discord_id, role, granted_by) VALUES (?, ?, ?);

-- :name revoke_role
DELETE FROM user_roles WHERE discord_id = ? AND role = ?;

-- :name list_roles_for_user
SELECT role FROM user_roles WHERE discord_id = ? ORDER BY role;

-- :name check_has_role
SELECT 1 FROM user_roles WHERE discord_id = ? AND role = ? LIMIT 1;

-- :name list_all_role_assignments
SELECT discord_id, role FROM user_roles ORDER BY discord_id, role;

-- ---------------------------------------------------------------------------
-- role_requests
-- ---------------------------------------------------------------------------

-- :name create_role_request
INSERT INTO role_requests (discord_id, role, user_note) VALUES (?, ?, ?);

-- {where_sql} = "WHERE …" or "" composed by Python build_where helper.
-- {order_sql} = "ORDER BY …" composed by Python (varies by status filter).
-- :name list_role_requests
SELECT rr.id, rr.discord_id, rr.role, rr.status,
       rr.requested_at, rr.reviewed_at, rr.reviewed_by,
       rr.user_note, rr.admin_note,
       u.discord_name, u.discord_username, u.avatar
FROM role_requests rr
LEFT JOIN users u ON u.discord_id = rr.discord_id
{where_sql}
{order_sql};

-- :name get_role_request
SELECT rr.id, rr.discord_id, rr.role, rr.status,
       rr.requested_at, rr.reviewed_at, rr.reviewed_by,
       rr.user_note, rr.admin_note,
       u.discord_name, u.discord_username, u.avatar
FROM role_requests rr
LEFT JOIN users u ON u.discord_id = rr.discord_id
WHERE rr.id = ?;

-- :name review_role_request
UPDATE role_requests SET
    status      = ?,
    reviewed_at = strftime('%s','now'),
    reviewed_by = ?,
    admin_note  = ?
WHERE id = ? AND status = 'pending';

-- :name select_role_request_grant_info
SELECT discord_id, role FROM role_requests WHERE id = ?;

-- :name withdraw_role_request
UPDATE role_requests SET status = 'withdrawn'
WHERE id = ? AND discord_id = ? AND status = 'pending';

-- ---------------------------------------------------------------------------
-- role_permissions (capability checks)
-- ---------------------------------------------------------------------------

-- :name check_user_has_capability
SELECT 1
FROM user_roles ur
JOIN role_permissions rp ON rp.role = ur.role
WHERE ur.discord_id = ? AND rp.capability = ?
LIMIT 1;

-- :name check_role_has_capability
SELECT 1 FROM role_permissions WHERE role = ? AND capability = ? LIMIT 1;
