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


def init_db(path: Path = DB_PATH) -> None:
    """Create tables if they don't exist + apply migrations.

    Called once at startup. Idempotent. Order:
      1. executescript SCHEMA — creates tables + the indices known at v1.
      2. apply_migrations(conn) — ALTER TABLE + post-ALTER index creates + seeds.

    Memory [[test-migrations-against-old-db-shape]]: any new column added
    here MUST be added to BOTH SCHEMA (for fresh DBs) and migrations.py
    (for existing DBs). Column-dependent indexes live in migrations.py
    AFTER the ADD COLUMN — never in SCHEMA.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        apply_migrations(conn)
        assert_schema_complete(conn)


# ---------------------------------------------------------------------------
# Re-export the per-domain helpers so the existing API shape is preserved.
# Order matters: each domain only imports from web.db (this module) at the
# helper level — no inter-domain imports.
# ---------------------------------------------------------------------------

from backend.server.db.claims import (  # noqa: E402,F401
    delete_claim,
    delete_claims_for_user,
    get_active_claims,
    get_claim_by_id,
    list_claims,
    review_claim,
    set_primary,
    submit_claim,
    withdraw_claim,
)
from backend.server.db.item_watch import (  # noqa: E402,F401
    add_item_watch,
    list_item_watches,
    remove_item_watch,
    update_item_watch_check,
)
from backend.server.db.servers import (  # noqa: E402,F401
    get_server_by_subdomain_sync,
    get_server_by_world_sync,
    list_servers_sync,
    set_default_server_sync,
    upsert_server_settings_sync,
)
from backend.server.db.tokens import (  # noqa: E402,F401
    generate_token,
    hash_token,
    list_api_tokens,
    lookup_api_token,
    mint_api_token,
    revoke_api_token,
)
from backend.server.db.users import (  # noqa: E402,F401
    create_role_request,
    get_display_names_for_discord_ids,
    get_role_request,
    get_user_access_status,
    grant_role,
    has_role,
    list_all_users,
    list_pending_users,
    list_role_assignments,
    list_role_requests,
    list_roles_for_user,
    review_and_grant_role,
    review_role_request,
    revoke_role,
    role_has_capability,
    set_user_access,
    upsert_user,
    user_has_capability_via_db,
    withdraw_role_request,
)
