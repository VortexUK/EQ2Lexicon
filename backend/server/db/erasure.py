"""Account erasure — the right-to-be-forgotten mechanics behind the privacy
policy (2026-09-28).

One sync function, ONE Postgres transaction spanning the ``users`` and
``parses`` schemas (search_path switched mid-transaction) — run from the
routes through ``run_sync``:

- rows that ARE the person go: the ``users`` row and everything keyed to
  their Discord id (tokens, roles, role requests, claims, favourites,
  download events, AA plans, availability), the voice-attendance
  observations that carry their Discord id, and every tamper/quarantine
  report about their uploads (those hold the full payload + their name);
- rows that merely NAME the person as an actor are tombstoned — the
  ``*_by`` author columns point at a placeholder ``users`` row
  (``DELETED_USER_ID``) so NOT NULL / FK references stay valid and the
  guild's records keep their history without the identity;
- uploaded fights stay, as the guild's raid records, with the Discord
  identity stripped: ``source_dsn`` ``plugin:<id>`` becomes
  ``plugin:deleted`` (so the list shows no uploader identity) and
  ``hidden_by`` is cleared.

What this deliberately does NOT touch: character names. Uploads and
attendance rows name in-game characters, which are game data shared across
every raider's log; the policy says so. In-memory state (claim cache,
supporters cache, the metrics last-seen map) is cleared by the route.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from psycopg.types.json import Json

from backend import pg
from backend.server import db as _users_db  # SCHEMA read at call time (tests re-point it)
from backend.server.parses.db import SCHEMA as _PARSES_SCHEMA

#: The placeholder users row every tombstoned reference points at.
DELETED_USER_ID = "deleted"
DELETED_SOURCE_DSN = "plugin:deleted"

# (table, column) pairs whose rows belong to the person and are deleted.
_OWNED_ROWS: tuple[tuple[str, str], ...] = (
    ("api_tokens", "user_id"),
    ("user_roles", "discord_id"),
    ("role_requests", "discord_id"),
    ("character_claims", "discord_id"),
    ("character_favorites", "discord_id"),
    ("download_events", "discord_id"),
    ("aa_plans", "discord_id"),
    ("user_availability", "discord_id"),
)

# (table, column) pairs that name the person as an actor; re-pointed at the
# tombstone. Nullable columns could be NULLed instead, but one rule for all
# keeps the audit meaning ("someone who has since left") consistent.
_AUTHOR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("character_claims", "reviewed_by"),
    ("role_requests", "reviewed_by"),
    ("user_roles", "granted_by"),
    ("item_watch", "added_by"),
    ("raid_teams", "updated_by"),
    ("raid_roster_roles", "updated_by"),
    ("raid_placements", "updated_by"),
    ("character_availability", "set_by"),
    ("attendance_overrides", "set_by"),
    ("attendance_segments", "set_by"),
    ("discord_guild_links", "linked_by"),
    ("guild_settings", "updated_by"),
    # Recruitment: both author ids are tombstoned; the logo blob and the
    # profile text stay (guild assets, not the person's data). Contacts are
    # in-game character names — public Census game data, out of scope.
    ("guild_recruitment", "updated_by"),
    ("guild_recruitment", "logo_uploaded_by"),
)


@dataclass
class ErasureResult:
    found: bool
    deleted: dict[str, int] = field(default_factory=dict)
    tombstoned: dict[str, int] = field(default_factory=dict)
    parses_anonymised: int = 0
    tamper_reports_deleted: int = 0
    voice_observations_deleted: int = 0
    sessions_scrubbed: int = 0

    def as_dict(self) -> dict:
        return {
            "found": self.found,
            "deleted": self.deleted,
            "tombstoned": self.tombstoned,
            "parses_anonymised": self.parses_anonymised,
            "tamper_reports_deleted": self.tamper_reports_deleted,
            "voice_observations_deleted": self.voice_observations_deleted,
            "sessions_scrubbed": self.sessions_scrubbed,
        }


def _ensure_tombstone(conn, now: int) -> None:
    conn.execute(
        "INSERT INTO users (discord_id, discord_name, discord_username, avatar, first_seen, last_seen, "
        "access_status) VALUES (%s, 'Deleted user', 'deleted', NULL, %s, %s, 'denied') "
        "ON CONFLICT (discord_id) DO NOTHING",
        (DELETED_USER_ID, now, now),
    )


def erase_user_sync(
    discord_id: str,
    *,
    users_schema: str | None = None,
    parses_schema: str | None = None,
    now: int | None = None,
) -> ErasureResult:
    """Erase one Discord account from both schemas. Idempotent: a second
    call finds nothing and reports ``found=False``. The tombstone id itself
    can never be erased. The WHOLE erasure is one Postgres transaction with
    users constraints deferred (replacing SQLite's PRAGMA foreign_keys=OFF),
    so a crash mid-way leaves nothing half-erased; migrations guarantee
    every table exists, so the per-table existence probes are gone."""
    if not discord_id or discord_id == DELETED_USER_ID:
        return ErasureResult(found=False)
    now = int(now if now is not None else time.time())
    result = ErasureResult(found=False)

    schema = users_schema if users_schema is not None else _users_db.SCHEMA
    with pg.connection() as conn:
        conn.execute(pg.search_path_sql(schema))
        # The users-referencing FKs are DEFERRABLE INITIALLY IMMEDIATE —
        # defer them all so statement order inside this transaction is free.
        conn.execute("SET CONSTRAINTS ALL DEFERRED")
        found_user = conn.execute("SELECT 1 FROM users WHERE discord_id = %s", (discord_id,)).fetchone()
        result.found = found_user is not None
        _ensure_tombstone(conn, now)
        for table, column in _AUTHOR_COLUMNS:
            cur = conn.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s", (DELETED_USER_ID, discord_id))
            if cur.rowcount:
                result.tombstoned[f"{table}.{column}"] = cur.rowcount
        for table, column in _OWNED_ROWS:
            cur = conn.execute(f"DELETE FROM {table} WHERE {column} = %s", (discord_id,))
            if cur.rowcount:
                result.deleted[table] = cur.rowcount
        cur = conn.execute(
            "DELETE FROM attendance_observations WHERE kind = 'voice' AND character_name = %s", (discord_id,)
        )
        result.voice_observations_deleted = cur.rowcount
        # uploaders is jsonb: `?` is the key-exists operator (psycopg only
        # treats %s as a placeholder, so the bare ? passes through).
        rows = conn.execute(
            "SELECT id, uploaders FROM attendance_sessions WHERE uploaders ? %s", (discord_id,)
        ).fetchall()
        for row in rows:
            uploaders = row["uploaders"] or {}
            uploaders.pop(discord_id, None)
            conn.execute("UPDATE attendance_sessions SET uploaders = %s WHERE id = %s", (Json(uploaders), row["id"]))
            result.sessions_scrubbed += 1
        cur = conn.execute("DELETE FROM users WHERE discord_id = %s", (discord_id,))
        if cur.rowcount:
            result.deleted["users"] = cur.rowcount

        # ---- parses half: SAME transaction, schema switched in place.
        # SET search_path is transactional, so the whole erasure commits or
        # rolls back as one unit — the old users.db/parses.db split could
        # crash between the halves and leave a half-erased account.
        conn.execute(pg.search_path_sql(parses_schema if parses_schema is not None else _PARSES_SCHEMA))
        dsn = f"plugin:{discord_id}"
        cur = conn.execute("UPDATE encounters SET source_dsn = %s WHERE source_dsn = %s", (DELETED_SOURCE_DSN, dsn))
        result.parses_anonymised = cur.rowcount
        conn.execute("UPDATE ingest_log SET source_dsn = %s WHERE source_dsn = %s", (DELETED_SOURCE_DSN, dsn))
        conn.execute("UPDATE encounters SET hidden_by = NULL WHERE hidden_by = %s", (discord_id,))
        cur = conn.execute("DELETE FROM tamper_reports WHERE uploader_discord_id = %s", (discord_id,))
        result.tamper_reports_deleted = cur.rowcount
        conn.execute("UPDATE tamper_reports SET acknowledged_by = NULL WHERE acknowledged_by = %s", (discord_id,))
        conn.commit()

    return result
