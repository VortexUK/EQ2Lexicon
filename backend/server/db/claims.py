"""users.db character_claims table helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the character claims domain. ``path: Path = DB_PATH`` parameter on every
public function so tests can inject a temp DB.

Claim statuses:
  pending    – submitted, awaiting admin review
  approved   – admin approved; user's active character
  rejected   – admin rejected (note may contain reason)
  withdrawn  – user cancelled their own pending request
  superseded – was approved but user submitted a new claim that was later approved
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.db_catalogue import AsyncStoreBase
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class ClaimsStore(AsyncStoreBase):
    """users.db `claims` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db); methods open per-call
    connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def get_active_claims(
        self,
        discord_id: str,
        world: str = "Varsoon",
    ) -> dict:
        """
        Return all active claims for this user on the given world as:
          { 'approved': [list of approved claim dicts], 'pending': claim dict or None }
        Ignores withdrawn / rejected / superseded.
        Claims are scoped to (discord_id, world) — a user's Varsoon and Wuoshi
        primaries are completely independent.
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                _SQL["list_active_claims"],
                (discord_id, world),
            ) as cur:
                rows = await cur.fetchall()
        claims = [dict(r) for r in rows]
        return {
            "approved": [c for c in claims if c["status"] == "approved"],
            "pending": next((c for c in claims if c["status"] == "pending"), None),
        }

    async def submit_claim(
        self,
        discord_id: str,
        character_name: str,
        world: str = "Varsoon",
    ) -> dict:
        """
        Cancel any current *pending* claim for this user on this world (set to
        'withdrawn'), then insert a new pending claim.  Returns the new claim dict.
        Already-approved claims for other characters are not affected.
        Raises ValueError if this character is already claimed on this world.
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            # Reject if this character name is already claimed (approved or pending)
            # by anyone *on this world* (EQ2 names are unique only within a server)
            async with db.execute(
                _SQL["check_character_name_taken"],
                (character_name, world),
            ) as cur:
                existing = await cur.fetchone()
            if existing:
                if existing["discord_id"] == discord_id:
                    raise ValueError(f"'{character_name}' is already claimed on your account.")
                else:
                    raise ValueError(f"'{character_name}' has already been claimed by another player.")
            # Cancel any existing pending claim on this world (one pending per world at a time)
            await db.execute(
                _SQL["withdraw_pending_claims_on_world"],
                (discord_id, world),
            )
            cur = await db.execute(
                _SQL["submit_claim"],
                (discord_id, character_name, world),
            )
            new_id = cur.lastrowid
            await db.commit()
            async with db.execute(_SQL["find_by_id"], (new_id,)) as cur2:
                row = await cur2.fetchone()
        assert row is not None, "INSERT succeeded but SELECT returned nothing"
        return dict(row)

    async def withdraw_claim(
        self,
        claim_id: int,
        discord_id: str,
        world: str = "Varsoon",
    ) -> bool:
        """
        Withdraw or remove a specific claim belonging to this user.
        Works on both pending and approved claims.
        If the withdrawn claim was the primary, the oldest remaining approved
        character on the same world is automatically promoted to primary.
        Returns True if something changed.
        """
        async with aiosqlite.connect(self.path) as db:
            # Check if this claim is primary before withdrawing (and capture world from row)
            async with db.execute(
                _SQL["select_primary_and_world"],
                (claim_id, discord_id),
            ) as cur:
                row = await cur.fetchone()
            was_primary = row is not None and row[0] == 1
            claim_world = row[1] if row is not None else world

            cur = await db.execute(
                _SQL["withdraw_claim"],
                (claim_id, discord_id),
            )
            changed = cur.rowcount > 0

            # Promote the oldest remaining approved character on the same world to primary
            if changed and was_primary:
                await db.execute(
                    _SQL["promote_oldest_to_primary"],
                    (discord_id, claim_world, claim_id),
                )

            await db.commit()
        return changed

    async def set_primary(
        self,
        discord_id: str,
        claim_id: int,
        world: str = "Varsoon",
    ) -> bool:
        """
        Set a specific approved claim as the user's primary character on the given world.
        Clears is_primary on all other approved claims for this user on the same world.
        Claims on other worlds are not affected.
        Returns True if the target claim exists and was updated.
        """
        async with aiosqlite.connect(self.path) as db:
            # Verify the claim belongs to this user, is approved, and is on the right world
            async with db.execute(
                _SQL["find_approved_claim_for_user_on_world"],
                (claim_id, discord_id, world),
            ) as cur:
                if not await cur.fetchone():
                    return False

            # Clear primary on all approved claims for this user on this world, then set on target
            await db.execute(
                _SQL["clear_primary_on_world"],
                (discord_id, world),
            )
            await db.execute(
                _SQL["set_primary_by_id"],
                (claim_id,),
            )
            await db.commit()
        return True

    async def get_claim_by_id(
        self,
        claim_id: int,
    ) -> dict | None:
        """Return a single claim joined with its submitting user's info."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                _SQL["find_claim_with_user"],
                (claim_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def list_claims(
        self,
        status: str | None = None,
        world: str | None = None,
    ) -> list[dict]:
        """
        List claims joined with user info, optionally filtered by status and/or world.
        world=None returns claims from all worlds (admin overview).
        world='Varsoon' (or any other) scopes to that server only.
        Pending claims are sorted oldest-first (queue order).
        All other statuses are sorted newest-first.
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            where_parts: list[str] = []
            params: list = []
            if status:
                where_parts.append("c.status = ?")
                params.append(status)
            if world is not None:
                where_parts.append("c.world = ?")
                params.append(world)
            where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
            order = "ASC" if status == "pending" else "DESC"
            async with db.execute(
                _SQL["list_claims"].format(where_sql=where_sql, order=order),
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def review_claim(
        self,
        claim_id: int,
        status: str,
        admin_id: str,
        note: str | None = None,
    ) -> dict | None:
        """
        Approve or reject a claim.
        On approval, auto-assigns is_primary if this is the user's first approved
        character on this world (scoped to (discord_id, world) so each server has
        an independent primary), and removes the new owner's favourite of the
        character if one exists — you can't favourite your own character, and this
        is the single funnel every approval path (admin + officer) flows through.
        Returns the updated claim (with user info) or None if not found.
        """
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(_SQL["select_claim_user_and_world"], (claim_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            discord_id = row[0]
            claim_world = row[1]
            character_name = row[2]

            await db.execute(
                _SQL["review_claim"],
                (status, admin_id, note, claim_id),
            )
            # Auto-assign primary if this is the user's first approved character on this world
            if status == "approved":
                async with db.execute(
                    _SQL["check_user_has_primary_on_world"],
                    (discord_id, claim_world, claim_id),
                ) as cur:
                    has_primary = await cur.fetchone() is not None
                if not has_primary:
                    await db.execute(
                        _SQL["set_primary_by_id"],
                        (claim_id,),
                    )
                # The character is now theirs — drop their own-favourite of it.
                # (The favourite count cache self-heals via TTL; see api/favorites.)
                await db.execute(
                    _SQL["remove_new_owners_favorite"],
                    (discord_id, claim_world, character_name),
                )
            await db.commit()

        return await self.get_claim_by_id(claim_id)

    async def delete_claim(
        self,
        claim_id: int,
    ) -> bool:
        """Hard-delete a claim row. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(_SQL["delete_claim"], (claim_id,))
            deleted = cur.rowcount > 0
            await db.commit()
        return deleted

    async def delete_claims_for_user(
        self,
        discord_id: str,
    ) -> int:
        """Hard-delete all claim rows for a user. Returns the number of rows deleted."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(_SQL["delete_claims_for_user"], (discord_id,))
            count = cur.rowcount
            await db.commit()
        return count


# The shared default instance — every runtime consumer goes through this.
store = ClaimsStore()
