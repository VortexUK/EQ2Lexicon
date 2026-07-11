"""Backend user/claims/tokens/servers DB layer.

Carved out of the original 1309-line web/db.py. Five unrelated domains
each get their own module:

  - users.py      — users table + role/role_request/role_permission helpers
  - claims.py     — character_claims table
  - item_watch.py — item_watch table
  - tokens.py     — api_tokens table
  - servers.py    — servers (per-server registry) table

The init_db() orchestrator + the DB_PATH constant live here. Every
per-domain helper is re-exported from this module so the existing
`from web import db as users_db; users_db.get_active_claims(...)` API
shape is preserved — no consumer rewrites needed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.db_helpers import resolve_db_path
from backend.server.db._assertions import assert_schema_complete
from backend.server.db.migrations import apply_migrations
from backend.server.db.schema import SCHEMA

DB_PATH = resolve_db_path("DB_USERS_PATH", "users.db")


def init_db(path: Path | None = None) -> None:
    """Create tables if they don't exist + apply migrations.

    Called once at startup. Idempotent. Order:
      1. executescript SCHEMA — creates tables + the indices known at v1.
      2. apply_migrations(conn) — ALTER TABLE + post-ALTER index creates + seeds.

    Memory [[test-migrations-against-old-db-shape]]: any new column added
    here MUST be added to BOTH SCHEMA (for fresh DBs) and migrations.py
    (for existing DBs). Column-dependent indexes live in migrations.py
    AFTER the ADD COLUMN — never in SCHEMA.
    """
    # Read DB_PATH at call time, not def time — the default-arg capture
    # pattern is exactly what the store conversion eliminated everywhere
    # else, and conftest re-points DB_PATH after import (BE-096 race).
    path = Path(path) if path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        apply_migrations(conn)
        assert_schema_complete(conn)


# ---------------------------------------------------------------------------
# Facade: re-export each domain store's bound methods so the existing
# `users_db.get_active_claims(...)` API shape is preserved. The domains are
# XStore(AsyncStoreBase) classes now (backend/db_catalogue.py) — the bound
# methods read the shared instance's `path` dynamically, so conftest
# re-points one attribute per store and every alias follows.
# ---------------------------------------------------------------------------

from backend.server.db.claims import store as claims_store  # noqa: E402
from backend.server.db.favorites import store as favorites_store  # noqa: E402
from backend.server.db.item_watch import store as item_watch_store  # noqa: E402
from backend.server.db.raid_schedule import store as raid_schedule_store  # noqa: E402
from backend.server.db.servers import store as servers_store  # noqa: E402
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

#: Every domain store over users.db — conftest re-points `store.path` on
#: each after re-resolving DB_PATH from the env.
ALL_STORES = (
    claims_store,
    favorites_store,
    item_watch_store,
    raid_schedule_store,
    servers_store,
    tokens_store,
    users_store,
)
