"""RETIRED — SQLite-era dev one-off (Sample values for unmapped fields to decide if they're worth storing.).

Read the local items.db directly; that mirror moved to the Postgres `items`
schema in Phase 2 (db/migrations/0007). Query the schema instead, or write a
fresh one-off against backend.eq2db.items.catalogue.
"""

if __name__ == "__main__":
    raise SystemExit("retired: items.db no longer exists — see module docstring")
