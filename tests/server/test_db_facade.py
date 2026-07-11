"""Guards for the backend.server.db facade (bound-method re-exports).

The facade re-exports each domain store's bound methods under the original
free-function names. Two failure modes this file pins down:

  1. Completeness drift — a new public store method that nobody remembers
     to alias on the facade fails only at runtime, on the facade path.
     ``test_facade_covers_every_public_store_method`` turns that into a
     test failure with the missing name spelled out (intentionally
     store-only names go in _FACADE_EXEMPT).

  2. Alias integrity — every facade alias must be the bound method of the
     store instance conftest re-points, or patches/repoints silently stop
     covering production traffic.
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
    "list_all_teams_with_twitch",
    # raid_planning + availability domains bypass the facade too
    "get_roles",
    "set_role",
    "get_placements",
    "replace_placements",
    "prune_placements_beyond",
    "claims_map",
    "roles_for_world",
    "get_range",
    "set_days",
    "statuses_for_day",
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
