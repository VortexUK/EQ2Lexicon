# Scripts

Preview, download and catalogue-build utilities. Run them from the repo root
with `uv run python scripts/<name>.py`. Every script that writes data writes
to the Postgres schema at `DATABASE_URL` (the same resolution chain as the
app: `DATABASE_URL` → `SUPABASE_DB_URL` → `POSTGRES_CONNECTION_STRING`), so
point that at a local database or at Supabase deliberately — there is no
SQLite file anywhere any more. The full refresh order and what each loader
touches is in [docs/runbooks/catalogue-refresh.md](../docs/runbooks/catalogue-refresh.md).

## Preview / inspection (read-only)

```powershell
uv run python scripts/preview_item.py "Faded Black Hood"        # render tooltip image
uv run python scripts/inspect_item.py "Faded Black Hood"        # dump raw Census JSON
uv run python scripts/preview_guild.py "Exordium"
uv run python scripts/preview_spellcheck.py Sihtric
uv run python scripts/preview_spellcheck.py Sihtric --details
uv run python scripts/preview_spellcheck.py Sihtric --debug     # show each counted spell
uv run python scripts/preview_aa_tree.py 25                     # render AA tree by ID
uv run python scripts/preview_aacheck.py Menludiir              # list character AA trees
uv run python scripts/preview_aacheck.py Menludiir Templar      # render a specific tree
```

## Catalogue downloads (Census → Postgres)

All three bulk downloaders are resumable: they keep an offset and continue
where they stopped; `--restart` ignores it, `--limit N` is for test runs.

```powershell
uv run python scripts/download_items.py                          # items schema (~370k rows; hours)
uv run python scripts/download_spells.py                         # spells schema
uv run python scripts/download_recipes.py                        # recipes schema (~66k rows)
uv run python scripts/download_recipes.py --limit 500            # test run
uv run python scripts/download_recipes.py --restart              # ignore the saved offset
uv run python scripts/backfill_item_stats.py                     # item_stats rows from raw_json (--rebuild to redo all)
uv run python scripts/backfill_recipe_levels.py                  # recipes.out_level from the items schema
uv run python scripts/build_recipe_classes.py                    # recipe_classes from the recipe books
uv run python scripts/download_spell_icons.py [--start N]        # spell icon PNGs (static files)
uv run python scripts/download_item_icons.py                     # item icon PNGs
```

## AA trees (aas schema)

The `aas` schema is seeded in full by `db/migrations/0012_aas.sql`; the
scripts are for refreshing it when Census changes.

```powershell
uv run python scripts/download_aa_trees.py                      # tree JSONs → local intermediates (gitignored)
uv run python scripts/build_aas_db.py                           # JSONs + data/AAs/aa_limits.json → aas schema
uv run python scripts/download_aa_icons.py                      # AA node icon PNGs (data/AAs/icons, served at /aa-assets)
```

## Zones (zones schema)

Boss rosters are curator data edited on the site; a rebuild upserts zones
by stable id and never touches `zone_encounters` / `zone_encounter_mobs`
(removed zones ARE pruned, cascading their rosters — read the script's
count output).

```powershell
uv run python scripts/dev/clean_eq2_zones.py                    # source JSON → eq2_zones.cleaned.json
uv run python scripts/build_zones_db.py                         # cleaned JSON → zones schema
uv run python scripts/dev/_smoke_test_zones.py                  # validate the cleaned JSON
uv run python scripts/dev/_smoke_test_zones_db.py               # validate the loaded schema
```

## Raid-strategies seed pipeline (raids schema)

Two stages — the network-dependent scrape is decoupled from the fast ingest.

```powershell
# Stage 1: scrape EQ2i (produces scripts/dev/eq2_raid_data.json, committed)
uv run python scripts/dev/scrape_eq2i_raids.py                  # 3 sample zones
uv run python scripts/dev/scrape_eq2i_raids.py --all-raids      # every raid zone the zones schema lists

# Stage 2: ingest into the raids schema
uv run python scripts/dev/ingest_raids_json.py --in scripts/dev/eq2_raid_data.json
uv run python scripts/dev/ingest_raids_json.py --dry-run        # parse + summarise without writing
```

The ingest is re-run safe — `SOURCE_MANUAL` rows (human edits) are never
overwritten. The HTTP cache (`scripts/dev/.eq2i_cache/`) is gitignored. The
export → agent → apply polish passes for strategy text are described in
[docs/runbooks/raid-strategy-agent-polish.md](../docs/runbooks/raid-strategy-agent-polish.md).

## Dev helpers

```powershell
scripts/dev_backend.ps1                                          # uvicorn --reload with the right flags
scripts/dev_frontend.ps1                                         # vite dev server
uv run python scripts/dev/seed_fake_parses.py --character X      # fake rankings data (--wipe removes it)
uv run python scripts/dev/grafana_pull.py panels|data|render     # pull Grafana Cloud metrics (GRAFANA_URL/TOKEN in .env)
```

`scripts/migrate_to_postgres.py` is the finished one-shot SQLite → Postgres
cutover copy; it stays until the rollback window closes and is not part of
any refresh.
