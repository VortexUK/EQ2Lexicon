"""Guards for the backend.server.db facade: every public store method is aliased
(store-only names go in _FACADE_EXEMPT), and every alias is the bound method of
the store instance tests re-point — otherwise patches silently miss production traffic.
"""

from __future__ import annotations

import inspect

from backend.server import db as users_db

#: Public store methods deliberately NOT re-exported on the facade —
#: consumers use the store instance (or the class) directly.
_FACADE_EXEMPT = {
    # tokens: pure staticmethods used internally / via TokensStore
    "generate_token",
    "hash_token",
    # servers: only the registry loader path uses it, via the store
    "get_server_by_subdomain_sync",
    # servers: the xpac-rollover loop calls the store directly
    "apply_xpac_rollover_sync",
    # favorites + raid_schedule domains bypass the facade entirely
    # (routes import their store instances directly)
    "add_favorite",
    "remove_favorite",
    "count_favorites_for_character",
    "is_favorited",
    "count_user_favorites",
    "list_favorites",
    "get_schedule",
    "replace_schedule",
    "get_schedules",  # recruiting list: bulk read via the store instance
    "officer_rank_ids_for",  # guild_settings: bulk read via the store instance
    "list_all_teams_with_twitch",
    # downloads domain bypasses the facade too (route uses the store directly)
    "record_download",
    "count_for_slug",
    "counts",
    # discord_links domain — the bot imports the store directly
    "upsert_link",
    "set_voice_channel",
    "get_link",
    "delete_link",
    "list_voice_links",
    "set_parses_channel",
    "set_parses_posted_at",
    "list_parse_links",
    # attendance domain bypasses the facade too
    "apply_snapshot",
    "find_live_session",
    "record_voice",
    "set_override",
    "clear_override",
    "overrides_for_session",
    "overrides_for_sessions",
    "set_segments",
    "segments_for_session",
    "segments_for_sessions",
    "set_session_window",
    "remove_character",
    "list_sessions",
    "get_session",
    "observations_for_session",
    "observations_for_sessions",
    "delete_session",
    "prune_voice_observations",
    # guild_settings domain bypasses the facade too (routes, delete.py and
    # list.py import the store directly)
    "get_settings",
    "upsert_settings",
    "officers_can_delete_parses",
    # guild_recruitment domain bypasses the facade too (the routes and the
    # recruitment sweep import the store directly)
    "get_profile",
    "upsert_profile",
    "get_logo",
    "set_logo",
    "clear_logo",
    "list_recruiting",
    "list_listed_worlds",
    "list_listed_ids",
    "set_guild_name",
    "delist",
    # site_settings: aliased on the facade under site-specific names
    # (get_site_setting / set_site_setting / all_site_settings)
    "get_setting",
    "set_setting",
    "all_settings",
    # aa_plans domain bypasses the facade too (routes use the store directly)
    "list_plans",
    "count_plans",
    "get_plan",
    "get_plan_by_slug",
    "create_plan",
    "update_plan",
    "delete_plan",
    # raid_planning + availability domains bypass the facade too
    "get_roles",
    "set_role",
    "get_placements",
    "replace_placements",
    "prune_placements_beyond",
    "claims_map",
    "primary_claims",
    "roles_for_world",
    "get_range",
    "set_days",
    "statuses_for_day",
    "statuses_for_day_with_times",
    "set_character_days",
    "char_statuses_for_day",
    "char_statuses_for_day_with_times",
    # base-class surface
    "clear_caches",
}


def _public_methods(store) -> set[str]:
    return {name for name, member in inspect.getmembers(type(store)) if not name.startswith("_") and callable(member)}


def test_facade_covers_every_public_store_method():
    missing = []
    for store in users_db.ALL_STORES:
        for name in _public_methods(store) - _FACADE_EXEMPT:
            if not hasattr(users_db, name):
                missing.append(f"{type(store).__name__}.{name}")
    assert not missing, (
        "Public store methods with no facade alias (add the alias in "
        f"backend/server/db/__init__.py or add to _FACADE_EXEMPT): {sorted(missing)}"
    )


def test_facade_aliases_are_bound_to_the_shared_stores():
    stores = set(users_db.ALL_STORES)
    bad = []
    for name in dir(users_db):
        if name.startswith("_"):
            continue
        member = getattr(users_db, name)
        bound_self = getattr(member, "__self__", None)
        if bound_self is None:
            continue  # not a bound method (module, constant, plain function)
        if isinstance(bound_self, type):
            continue  # classmethod-style binding — not a store alias
        if bound_self not in stores:
            bad.append(name)
    assert not bad, f"Facade aliases bound to something other than an ALL_STORES instance: {bad}"


def test_exempt_names_are_actually_store_methods():
    """Keep _FACADE_EXEMPT honest — a renamed/deleted method must not
    linger in the exemption list."""
    all_methods = set()
    for store in users_db.ALL_STORES:
        all_methods |= _public_methods(store)
    stale = _FACADE_EXEMPT - all_methods - {"clear_caches"}
    assert not stale, f"_FACADE_EXEMPT entries that match no store method: {sorted(stale)}"
