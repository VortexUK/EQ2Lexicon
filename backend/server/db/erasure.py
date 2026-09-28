"""Account erasure — the right-to-be-forgotten mechanics behind the privacy
policy (2026-09-28).

One sync function over BOTH SQLite files (users.db and parses.db), run from
the routes through ``run_sync``:

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

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.server import db as _users_db  # DB_PATH read at call time (tests re-point it)
from backend.server.parses.db import store as parses_store

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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _ensure_tombstone(conn: sqlite3.Connection, now: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO users (discord_id, discord_name, discord_username, avatar, first_seen, last_seen, "
        "access_status) VALUES (?, 'Deleted user', 'deleted', NULL, ?, ?, 'denied')",
        (DELETED_USER_ID, now, now),
    )


def erase_user_sync(
    discord_id: str,
    *,
    users_path: Path | None = None,
    parses_path: Path | None = None,
    now: int | None = None,
) -> ErasureResult:
    """Erase one Discord account from both databases. Idempotent: a second
    call finds nothing and reports ``found=False``. The tombstone id itself
    can never be erased."""
    if not discord_id or discord_id == DELETED_USER_ID:
        return ErasureResult(found=False)
    now = int(now if now is not None else time.time())
    result = ErasureResult(found=False)

    users_db = Path(users_path) if users_path is not None else _users_db.DB_PATH
    with sqlite3.connect(users_db) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")  # we order the deletes ourselves
        found_user = conn.execute("SELECT 1 FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        result.found = found_user is not None
        _ensure_tombstone(conn, now)
        for table, column in _AUTHOR_COLUMNS:
            if not _table_exists(conn, table):
                continue
            cur = conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (DELETED_USER_ID, discord_id))
            if cur.rowcount:
                result.tombstoned[f"{table}.{column}"] = cur.rowcount
        for table, column in _OWNED_ROWS:
            if not _table_exists(conn, table):
                continue
            cur = conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (discord_id,))
            if cur.rowcount:
                result.deleted[table] = cur.rowcount
        if _table_exists(conn, "attendance_observations"):
            cur = conn.execute(
                "DELETE FROM attendance_observations WHERE kind = 'voice' AND character_name = ?", (discord_id,)
            )
            result.voice_observations_deleted = cur.rowcount
        if _table_exists(conn, "attendance_sessions"):
            rows = conn.execute(
                "SELECT id, uploaders FROM attendance_sessions WHERE uploaders LIKE ?", (f"%{discord_id}%",)
            ).fetchall()
            for session_id, raw in rows:
                try:
                    uploaders = json.loads(raw or "{}")
                except ValueError:
                    continue
                if isinstance(uploaders, dict) and discord_id in uploaders:
                    uploaders.pop(discord_id)
                    conn.execute(
                        "UPDATE attendance_sessions SET uploaders = ? WHERE id = ?", (json.dumps(uploaders), session_id)
                    )
                    result.sessions_scrubbed += 1
        cur = conn.execute("DELETE FROM users WHERE discord_id = ?", (discord_id,))
        if cur.rowcount:
            result.deleted["users"] = cur.rowcount
        conn.commit()

    parses_db = Path(parses_path) if parses_path is not None else parses_store.path
    if parses_db.exists():
        conn = parses_store.init_db() if parses_path is None else sqlite3.connect(parses_db)
        try:
            dsn = f"plugin:{discord_id}"
            cur = conn.execute("UPDATE encounters SET source_dsn = ? WHERE source_dsn = ?", (DELETED_SOURCE_DSN, dsn))
            result.parses_anonymised = cur.rowcount
            if _table_exists(conn, "ingest_log"):
                conn.execute("UPDATE ingest_log SET source_dsn = ? WHERE source_dsn = ?", (DELETED_SOURCE_DSN, dsn))
            conn.execute("UPDATE encounters SET hidden_by = NULL WHERE hidden_by = ?", (discord_id,))
            if _table_exists(conn, "tamper_reports"):
                cur = conn.execute("DELETE FROM tamper_reports WHERE uploader_discord_id = ?", (discord_id,))
                result.tamper_reports_deleted = cur.rowcount
                conn.execute(
                    "UPDATE tamper_reports SET acknowledged_by = NULL WHERE acknowledged_by = ?", (discord_id,)
                )
            conn.commit()
        finally:
            conn.close()

    return result
