"""Canonical cache-key shapes for every TTLCache instance.

Every cache key in the app is shaped ``{kind}:{name.lower()}:{world.lower()}``
(or a variant). The same format string was hand-rolled in 10+ sites; a typo
(dropping ``.lower()`` on one side) silently missed the cache. This module
owns every shape so the typo class is impossible.

Pair each cache instance (web/cache.py) with one key-builder here. If a new
cache flavour is added, add its key-builder here too — never hand-roll a
key in route code.
"""

from __future__ import annotations


def char_cache_key(name: str, world: str) -> str:
    """Key for ``character_cache``. Used by every character read path."""
    return f"{name.lower()}:{world.lower()}"


def aa_cache_key(name: str, world: str) -> str:
    """Key for ``aa_cache``. ``aas:`` prefix distinguishes from char_cache."""
    return f"aas:{name.lower()}:{world.lower()}"


def gear_sets_cache_key(name: str, world: str) -> str:
    """Cache key for a character's saved gear sets."""
    return f"gearsets:{name.lower()}:{world.lower()}"


def guild_roster_key(guild: str, world: str) -> str:
    """``guild_cache`` key for the full roster fetch."""
    return f"roster:{guild.lower()}:{world.lower()}"


def guild_info_key(guild: str, world: str) -> str:
    """``guild_cache`` key for the guild summary (name + world + rank list)."""
    return f"info:{guild.lower()}:{world.lower()}"


def guild_adorns_key(guild: str, world: str) -> str:
    """``guild_cache`` key for the adorn-check rollup."""
    return f"adorns:{guild.lower()}:{world.lower()}"


def guild_spells_key(guild: str, world: str) -> str:
    """``guild_cache`` key for the spell-check rollup."""
    return f"spells:{guild.lower()}:{world.lower()}"


def census_refresh_key(name: str, world: str) -> str:
    """Key into ``web/census_refresh.py`` ``_last_attempt`` / ``_in_flight``.
    Same shape as ``char_cache_key`` so the throttle + cache line up."""
    return f"{name.lower()}:{world.lower()}"


def census_refresh_guild_key(guild: str, world: str) -> str:
    """Key into ``_last_attempt`` / ``_in_flight`` for guild refreshes."""
    return f"guild:{guild.lower()}:{world.lower()}"
