-- SQL for backend/server/db/users.py (psycopg, users schema).
-- Domain: users + user_roles + role_requests + role_permissions.

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------

-- :name upsert_user
INSERT INTO users (discord_id, discord_name, discord_username, avatar, access_status)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT(discord_id) DO UPDATE SET
    discord_name     = excluded.discord_name,
    discord_username = excluded.discord_username,
    avatar           = excluded.avatar,
    last_seen        = floor(extract(epoch from now())),
    access_status    = CASE
        WHEN %s = 1 THEN 'approved'
        ELSE users.access_status
    END;

-- :name select_access_status
SELECT access_status FROM users WHERE discord_id = %s;

-- :name select_display_names_by_ids
-- The id list binds as ONE array parameter (= ANY) — no composed
-- placeholder strings and no variable-count limits.
SELECT discord_id, discord_name FROM users WHERE discord_id = ANY(%s);

-- :name list_pending_users
SELECT discord_id, discord_name, discord_username, avatar, first_seen
FROM users WHERE access_status = 'pending' ORDER BY first_seen DESC;

-- :name approve_all_pending
-- One-shot backlog clear used when OPEN_SIGNUP is enabled.
UPDATE users SET access_status = 'approved' WHERE access_status = 'pending';

-- :name list_all_users_with_claim_count
-- Bare u.* columns with GROUP BY u.discord_id is legal in Postgres —
-- functional dependency on the users primary key.
SELECT u.discord_id, u.discord_name, u.discord_username, u.avatar,
       u.first_seen, u.last_seen, u.access_status,
       COUNT(c.id) AS claim_count
FROM users u
LEFT JOIN character_claims c ON c.discord_id = u.discord_id
GROUP BY u.discord_id
ORDER BY u.first_seen DESC;

-- :name update_user_access_status
UPDATE users SET access_status = %s WHERE discord_id = %s;

-- ---------------------------------------------------------------------------
-- user_roles
-- ---------------------------------------------------------------------------

-- :name grant_role
INSERT INTO user_roles (discord_id, role, granted_by) VALUES (%s, %s, %s)
ON CONFLICT DO NOTHING;

-- :name revoke_role
DELETE FROM user_roles WHERE discord_id = %s AND role = %s;

-- :name list_roles_for_user
SELECT role FROM user_roles WHERE discord_id = %s ORDER BY role;

-- :name check_has_role
SELECT 1 FROM user_roles WHERE discord_id = %s AND role = %s LIMIT 1;

-- :name list_all_role_assignments
SELECT discord_id, role FROM user_roles ORDER BY discord_id, role;

-- ---------------------------------------------------------------------------
-- role_requests
-- ---------------------------------------------------------------------------

-- :name create_role_request
INSERT INTO role_requests (discord_id, role, user_note) VALUES (%s, %s, %s)
RETURNING id;

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
WHERE rr.id = %s;

-- :name review_role_request
UPDATE role_requests SET
    status      = %s,
    reviewed_at = floor(extract(epoch from now())),
    reviewed_by = %s,
    admin_note  = %s
WHERE id = %s AND status = 'pending';

-- :name select_role_request_grant_info
SELECT discord_id, role FROM role_requests WHERE id = %s;

-- :name withdraw_role_request
UPDATE role_requests SET status = 'withdrawn'
WHERE id = %s AND discord_id = %s AND status = 'pending';

-- ---------------------------------------------------------------------------
-- role_permissions (capability checks)
-- ---------------------------------------------------------------------------

-- :name check_user_has_capability
SELECT 1
FROM user_roles ur
JOIN role_permissions rp ON rp.role = ur.role
WHERE ur.discord_id = %s AND rp.capability = %s
LIMIT 1;

-- :name check_role_has_capability
SELECT 1 FROM role_permissions WHERE role = %s AND capability = %s LIMIT 1;
