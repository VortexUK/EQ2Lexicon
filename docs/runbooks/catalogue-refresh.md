# Runbook: refreshing the reference catalogues

The read-only reference data (items, spells, recipes, AAs, zones) lives in Postgres, one schema
per family, in the database at `DATABASE_URL` (resolution chain `DATABASE_URL` →
`SUPABASE_DB_URL` → `POSTGRES_CONNECTION_STRING`, see `backend/pg.py`). Schema DDL is owned by
`db/migrations/`; the scripts below only write data. A refresh is a script run from the repo
root against whichever database the DSN points at — there are no files to upload. The `aas` and
`classes` families are also fully seeded by migrations 0011/0012, so a fresh database is usable
before any refresh.

All commands: `uv run python scripts/<name>.py` from the repo root. Point `DATABASE_URL` at a
local database first if you want to rehearse.

## Order

Items before anything that reads items:

1. `download_items.py` → items schema
2. `download_spells.py` → spells schema (independent of items)
3. `download_recipes.py` → recipes schema
4. `build_recipe_classes.py` → `recipes.recipe_classes` (reads items + recipes)
5. `backfill_recipe_levels.py --rebuild` → `recipes.out_level` (reads items)
6. `backfill_item_stats.py` → `items.item_stats`, only when the stat-extraction patterns change

AAs and zones are independent of the above and of each other.

## Items, spells, recipes

`download_items.py`, `download_spells.py` and `download_recipes.py` page through the Census
collection and upsert by id.

- They resume automatically from an offset stored in the schema's `_meta` table, so an
  interrupted run picks up where it stopped. `--restart` ignores the saved offset; `--limit N`
  stops early for testing.
- Census caps every page at 100 rows regardless of `c:limit`, and requests run sequentially
  because that is the most reliable against Census timeouts. A full run takes minutes (roughly
  70k recipes, 167k spells; items are the largest).
- Census occasionally returns an empty page mid-dataset under load. The item downloader only
  treats an empty page as the end when it is near the known total, so a flaky page does not
  truncate the download.
- Derived columns are computed at write time by the catalogue's `*_to_row` helpers (item level,
  tier display, spell base names, recipe spell-scroll `base_name_lower` / `crafted_tier`, and so
  on). There is no separate backfill for them; re-downloading recomputes them.

### `build_recipe_classes.py`

A recipe's tradeskill class is not in the recipe JSON (recipes only carry a shared crafting
bench). The script rebuilds `recipe_classes(recipe_id, class)` from two sources, in priority
order:

1. **Recipe books (authoritative).** A recipe-book item tagged with exactly one primary
   tradeskill class in `typeinfo.classes` (for example "Advanced Armorer Volume 20") maps every
   recipe in its `recipe_list` to that class. Secondary tradeskills (Tinkering, Adorning) have
   empty `typeinfo.classes` and are identified by the item's `requiredskill.text` instead.
   Multi-class "Lore and Legend" books, tagged with all nine classes, are ignored — they would
   stamp every class onto every recipe. A recipe in two single-class books maps to both.
2. **Item-type fallback.** Recipes that appear only in multi-class books are classified by what
   they craft. The signature → class map is learned from the step-1 ground truth rather than
   hard-coded: armour weight (Plate/Chain → Armorer, Leather/Cloth → Tailor), jewellery slot →
   Jeweler, weapon → Weaponsmith/Woodworker, food → Provisioner, house item → Carpenter, spell
   scroll → the scholar class for the spell's archetype. The script prints the learned map's
   resubstitution accuracy.

It runs on one recipes-schema connection and reads `items.items` schema-qualified.

### `backfill_recipe_levels.py`

The crafting tier (T1–T14) on the recipe page comes from the level of the item a recipe makes,
not from the fuel name: the same fuel adjective appears across very different tiers. The script
resolves each recipe's output level with one cross-schema `UPDATE ... FROM` and stores it in
`recipes.out_level`; the API maps level → tier.

- The level comes from the first leveled output in priority order `out_elaborate_id` (for spell
  recipes this is the named-quality scroll; the other ids often point at the fuel) →
  `out_worked_id` → `out_formed_id` → `out_simple_id`. Levels below 1 (intermediate components)
  count as "no level" and leave `out_level` NULL.
- By default it only fills rows where `out_level IS NULL`. Pass `--rebuild` after an items
  refresh to recompute every row.
- It does nothing if the items schema is empty, so it never wipes levels against an unloaded
  items catalogue.
- `out_level` is owned by this script: the recipe upsert never writes it.

### `backfill_item_stats.py`

Rebuilds the `item_stats` side table from `raw_json` already in the items schema, with no
re-download. Run it when the stat-extraction rules change (`STAT_MAP`, or
`_EFFECT_STAT_PATTERNS` in `backend/eq2db/items.py`). Modifier-derived stats always win;
effect-derived stats never overwrite them.

The default pass is incremental and never overwrites or deletes an existing effect-derived row.
Use `--rebuild` (deletes all `item_stats` rows first) when a pattern changed its value or was
removed.

## AAs

1. `download_aa_trees.py` — reads `data/AAs/adventurer.json` for tree ids, fetches each tree
   from Census into `data/AAs/trees/{id}.json` (gitignored local intermediates), and writes
   `data/AAs/index.json`. Existing files with real data are skipped, so it is safe to re-run.
2. `build_aas_db.py` — upserts every numeric `data/AAs/trees/{id}.json` plus the committed
   `data/AAs/aa_limits.json` into `aa_trees` / `aa_nodes` / `aa_limits`, precomputing
   `tree_type` and `max_points`, and stamps `_meta` provenance. `--schema` targets a scratch
   schema; `--trees-dir` / `--limits` override the inputs.
3. `download_aa_icons.py` — collects icon ids from the aas schema and fetches missing PNGs into
   `data/AAs/icons/` (served at `/aa-assets`).

If the refreshed data should also be what a fresh database starts with, the seed in
`db/migrations/0012_aas.sql` needs a new migration — the build script does not touch migrations.

## Zones

```powershell
uv run python scripts/dev/clean_eq2_zones.py      # eq2_zones.json -> eq2_zones.cleaned.json + report
uv run python scripts/build_zones_db.py           # cleaned JSON -> the zones schema
uv run python scripts/dev/_smoke_test_zones_db.py
```

`build_zones_db.py` upserts `zones` and fully replaces each zone's `zone_types` and
`zone_aliases`.

- Zone ids are stable across rebuilds (existing name → existing id, new name → max + 1). That
  is what keeps the curator-managed boss rosters attached.
- Zones missing from the source are pruned, and the prune cascades to their boss rosters. The
  script prints the prune count; a renamed source zone reads as one new zone plus one pruned
  zone, so check that number before trusting a rebuild.
- Boss rosters (`zone_encounters` / `zone_encounter_mobs`) and the `featured_*` tables are
  curator data edited in the web UI; the build never writes them.
- `scripts/dev/eq2_zones.dungeons.json` (optional, `--dungeons`) adds a `dungeon` overlay row in
  `zone_types` for each curated max-level group instance; it drives the Dungeons category on the
  rankings page.
- `_meta` gets `built_at`, `built_from` and `source_count`.

## Icons

`download_item_icons.py`, `download_spell_icons.py` and `download_class_icons.py` fetch PNGs
from EQ2wire into `data/items/icons/`, `data/spells/icons/` and `data/classes/icons/`. They skip
files that already exist and touch no database (the class-icon script reads ids from the
classes schema).
