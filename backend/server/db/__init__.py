"""Backend user/claims/tokens/servers DB layer — the users Postgres schema.

Each domain gets its own module (users, claims, item_watch, tokens,
servers, …); the schema DDL lives in db/migrations/0001_users.sql.

Every per-domain helper is re-exported from this module, so consumers call
`from backend.server import db as users_db; users_db.get_active_claims(...)`.
"""

from __future__ import annotations

#: Postgres schema the users family lives in (see db/migrations/
#: 0001_users.sql). Tests re-point each store's ``schema`` at leased
#: scratch schemas via tests/fixtures/users_db.point_users_db_at.
SCHEMA = "users"


# ---------------------------------------------------------------------------
# Facade: re-export each domain store's bound methods as
# `users_db.get_active_claims(...)`. The domains are
# XStore(PgStoreBase) classes (backend/db_catalogue.py) — the bound methods
# read the shared instance's `schema` dynamically, so conftest re-points one
# attribute per store and every alias follows.
# ---------------------------------------------------------------------------

from backend.server.db.aa_plans import store as aa_plans_store  # noqa: E402
from backend.server.db.attendance import store as attendance_store  # noqa: E402
from backend.server.db.availability import store as availability_store  # noqa: E402
from backend.server.db.claims import store as claims_store  # noqa: E402
from backend.server.db.discord_links import store as discord_links_store  # noqa: E402
from backend.server.db.downloads import store as downloads_store  # noqa: E402
from backend.server.db.favorites import store as favorites_store  # noqa: E402
from backend.server.db.guild_recruitment import store as guild_recruitment_store  # noqa: E402
from backend.server.db.guild_settings import store as guild_settings_store  # noqa: E402
from backend.server.db.item_watch import store as item_watch_store  # noqa: E402
from backend.server.db.raid_planning import store as raid_planning_store  # noqa: E402
from backend.server.db.raid_schedule import store as raid_schedule_store  # noqa: E402
from backend.server.db.servers import store as servers_store  # noqa: E402
from backend.server.db.site_settings import store as site_settings_store  # noqa: E402
from backend.server.db.tokens import store as tokens_store  # noqa: E402
from backend.server.db.users import store as users_store  # noqa: E402

# claims
delete_claim = claims_store.delete_claim
delete_claims_for_user = claims_store.delete_claims_for_user
get_active_claims = claims_store.get_active_claims
get_claim_by_id = claims_store.get_claim_by_id
list_claims = claims_store.list_claims
review_claim = claims_store.review_claim
set_primary = claims_store.set_primary
submit_claim = claims_store.submit_claim
withdraw_claim = claims_store.withdraw_claim

# item watch
add_item_watch = item_watch_store.add_item_watch
list_item_watches = item_watch_store.list_item_watches
remove_item_watch = item_watch_store.remove_item_watch
update_item_watch_check = item_watch_store.update_item_watch_check

# servers registry
get_server_by_world_sync = servers_store.get_server_by_world_sync
list_servers_sync = servers_store.list_servers_sync
set_default_server_sync = servers_store.set_default_server_sync
upsert_server_settings_sync = servers_store.upsert_server_settings_sync

# site-wide settings
get_site_setting = site_settings_store.get_setting
set_site_setting = site_settings_store.set_setting
all_site_settings = site_settings_store.all_settings

# api tokens
list_api_tokens = tokens_store.list_api_tokens
lookup_api_token = tokens_store.lookup_api_token
mint_api_token = tokens_store.mint_api_token
revoke_api_token = tokens_store.revoke_api_token

# users + roles
approve_all_pending = users_store.approve_all_pending
create_role_request = users_store.create_role_request
get_display_names_for_discord_ids = users_store.get_display_names_for_discord_ids
get_role_request = users_store.get_role_request
get_user_access_status = users_store.get_user_access_status
get_session_access = users_store.get_session_access
bump_session_epoch = users_store.bump_session_epoch
grant_role = users_store.grant_role
has_role = users_store.has_role
list_all_users = users_store.list_all_users
list_pending_users = users_store.list_pending_users
list_role_assignments = users_store.list_role_assignments
list_role_requests = users_store.list_role_requests
list_roles_for_user = users_store.list_roles_for_user
review_and_grant_role = users_store.review_and_grant_role
review_role_request = users_store.review_role_request
revoke_role = users_store.revoke_role
role_has_capability = users_store.role_has_capability
set_user_access = users_store.set_user_access
upsert_user = users_store.upsert_user
user_has_capability_via_db = users_store.user_has_capability_via_db
withdraw_role_request = users_store.withdraw_role_request

#: Every domain store over the users schema — tests re-point
#: `store.schema` on each (tests/fixtures/users_db.point_users_db_at).
ALL_STORES = (
    aa_plans_store,
    attendance_store,
    availability_store,
    claims_store,
    discord_links_store,
    downloads_store,
    favorites_store,
    guild_recruitment_store,
    guild_settings_store,
    item_watch_store,
    raid_planning_store,
    raid_schedule_store,
    servers_store,
    site_settings_store,
    tokens_store,
    users_store,
)
