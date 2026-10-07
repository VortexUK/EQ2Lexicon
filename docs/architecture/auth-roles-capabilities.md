# Auth, roles and capabilities

Code: `backend/server/auth_deps.py` (dependencies and allowlists),
`backend/server/db/` (`user_roles`, `role_permissions` tables in the users
schema), `backend/server/api/admin.py` (grant/revoke, role-request queue),
`backend/server/api/role_requests.py` (self-service requests),
`backend/server/api/guild.py` (`_officer_chars`, `_leader_chars`).

## Authentication

| Dependency | Accepts |
|---|---|
| `require_user_session` | Session cookie only (Discord OAuth login). |
| `require_user_session_or_token` | Session cookie, or `Authorization: Bearer <token>`. Used by endpoints for the ACT plugin / desktop parser and other integrations. |
| `require_admin` | Session user whose Discord ID is in `ADMIN_DISCORD_IDS`. 401 without a session, 403 otherwise. |

Bearer tokens are minted per user and stored hashed. A token whose owner is not
`approved` (pending or denied) is rejected with 403, so a token minted before a
denial cannot be used. Failed token lookups log a short sha256 prefix of the
token, never the token itself.

`ADMIN_DISCORD_IDS` is read once at import; changing it needs a restart
(Railway redeploys on push). If it is unset every admin route returns 403 and a
single warning is logged on the first admin-gated request.

## Roles

There are three kinds of role:

- **admin** — from the `ADMIN_DISCORD_IDS` env var. It lives outside the
  database on purpose, so a database wipe or a bad migration cannot lock the
  operator out. Admin is treated as holding every capability and never appears
  in `role_permissions`.
- **DB roles** — rows in `user_roles`, granted and revoked by an admin
  (directly, or by approving a self-service role request). The name must be in
  `KNOWN_ROLES`; the grant, revoke and request routes reject anything else with
  400, so a typo cannot create a row that silently grants nothing.
- **officer** — dynamic, computed per request from the user's approved
  character claims and the guild's Census roster: a claimed character whose
  rank id is in `_OFFICER_RANKS` (0 and 1) is an officer of that guild. Never
  persisted; Census is the source of truth.

Current `KNOWN_ROLES`:

| Role | What it does | How it is checked |
|---|---|---|
| `contributor` | Edit site content (raid strategies, zone rosters, ACT packs) | `edit_content` capability via `role_permissions` |
| `supporter` | Cosmetic crown badge next to the holder's name; awarded by an admin for donations (Support page, `/api/supporters`) | `has_role` |
| `subscriber` | Raid-attendance features while they are in limited preview (guild Attendance tab, parser attendance endpoints, the bot's attendance summary). Admins always pass. | `has_role` |
| `api` | Read-only third-party export API (`/api/export/v1/*`). Opt-in per account and paired with a bearer token, so access is attributable and can be revoked by removing the role or the token. Admins pass. | `has_role` |

Role requests are immutable audit history; approving one also inserts the
`user_roles` row, and that grant can later be revoked without rewriting the
request.

## Capabilities

Content routes gate on **capabilities**, not role names, through
`require_capability("<name>")` (the pre-built `require_editor` is
`require_capability("edit_content")`). Routes state *what* they need; which
roles qualify is data in `role_permissions`.

Resolution, cheapest first:

1. admin — always passes;
2. DB roles — one indexed `EXISTS` over `user_roles` joined to
   `role_permissions`;
3. officer — only if `('officer', capability)` exists in `role_permissions`;
   then the user's primary guild is resolved from cache and `_officer_chars`
   checks the roster. Skipping this when officers lack the capability avoids
   the claims + roster lookup.

Today the only capability is `edit_content`, granted to `contributor`. Officers
do not hold it: content editing is admin/contributor only.

`KNOWN_CAPABILITIES` is the programmer-facing allowlist.
`require_capability` raises `ValueError` at route-definition (import) time for
an unregistered name, so a misspelt capability fails loudly instead of
authorising nobody.

### Adding a capability

1. Add the string to `KNOWN_CAPABILITIES`.
2. Seed its `role_permissions` rows in a new `db/migrations/NNNN_users.sql`
   migration (including `('officer', '<capability>')` if officers should have
   it).
3. Gate the route with `Depends(require_capability("<capability>"))`.

### Adding a role

Add it to `KNOWN_ROLES`. If it gates a capability, seed `role_permissions`
rows in a users-schema migration; a purely cosmetic or directly checked role
(like `supporter`) needs none.

## Guild-scoped officer and leader checks

Several guild features are gated per guild rather than through capabilities:
claim review, raid schedule, recruitment profile and item watch use
`_officer_chars(discord_id, guild_name)` (officer of *that* guild, or admin);
guild settings use `_leader_chars` (rank id 0 only). Both read the user's
approved claims on the current world and the guild roster rank map.
`_leader_chars_cached` (and `_roster_rank_map_cached`) are cache-only twins for
hot or polled read paths: they return `None` on a cold cache instead of calling
Census.
