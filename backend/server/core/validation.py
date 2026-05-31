"""Conservative input validators for the public API surface.

EQ2 has well-defined shapes for character names, guild names, and server
names. Validating against the real shape on the way in is defence in depth:
- Keeps obvious injection shapes (paths, ``:``, control chars) out of
  Census API URLs.
- Stops a hostile name with ``:`` from colliding with cache keys (the keys
  are shaped ``name.lower():world.lower()`` throughout the app — a name
  containing ``:`` could read or poison another player's cache entry).
- Makes invalid input fail loudly at the route layer rather than producing
  a 502 from a downstream Census error.

Originally lived inline in web/routes/parses.py:84-107 — promoted here so
every route applies the same rules, not just the ingest endpoint.
"""

from __future__ import annotations

import re

# EQ2 character names are letters only, max 15 chars. Daybreak's naming rules.
CHARACTER_NAME_RE = re.compile(r"^[A-Za-z]{1,15}$")

# EQ2 server names: letters, digits, spaces, apostrophes, hyphens, underscores.
# Max 30 chars to match the Pydantic max_length=30 on logger_server.
WORLD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 '_-]{0,30}$")

# Guild names allow spaces and apostrophes. Looser than character names by
# necessity ("The Spitting Cobras" is a real guild). Max 64 chars matches
# the existing _validate_guild_name in web/routes/guild.py.
GUILD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '_-]{0,63}$")


def validate_character_name(name: str | None) -> str | None:
    """Return ``name`` if it matches the EQ2 character-name shape, else None.

    Strips surrounding whitespace before matching. Use as a `if not ...: raise
    HTTPException(400, ...)` gate at the top of every route that takes a
    character name from the URL or query string.
    """
    if not name:
        return None
    candidate = name.strip()
    if not candidate:
        return None
    return candidate if CHARACTER_NAME_RE.match(candidate) else None


def sanitize_world(world: str | None) -> str | None:
    """Return ``world`` if it matches the EQ2 server-name shape, else None.

    Callers typically use the result as ``sanitize_world(w) or DEFAULT_WORLD``
    so a missing or malformed value falls back to the deployment default
    rather than feeding garbage into a Census API URL. Replaces the
    private ``_sanitize_world`` in parses.py."""
    if not world:
        return None
    candidate = world.strip()
    if not candidate:
        return None
    return candidate if WORLD_NAME_RE.match(candidate) else None


def validate_guild_name(name: str | None) -> str | None:
    """Return ``name`` if it matches a plausible EQ2 guild-name shape.

    Looser than character names (spaces, apostrophes allowed). Replaces the
    private ``_validate_guild_name`` in web/routes/guild.py."""
    if not name:
        return None
    candidate = name.strip()
    if not candidate:
        return None
    return candidate if GUILD_NAME_RE.match(candidate) else None
