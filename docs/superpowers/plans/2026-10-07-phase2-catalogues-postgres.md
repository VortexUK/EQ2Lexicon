# Phase 2 — items / spells / recipes catalogues to Postgres (retire the PVC)

## Goal

Move the three remaining volume-resident SQLite mirrors — items.db (2.42 GB,
372,444 items + 1,452,331 item_stats), spells.db (118 MB, 167,228 rows),
recipes.db (62 MB, 66,431 + 60,122 recipe_classes) — into the same Supabase
database as three new schemas (`items`, `spells`, `recipes`). End state: the
Railway volume detaches, deploys become zero-downtime, and catalogue refreshes
are script runs against `DATABASE_URL` instead of hand-copies.

**Stays SQLite (confirmed nothing forces a move)**: aas.db + classes.db
(committed repo files), the `BaseCatalogue`/SQLite `_meta` machinery they sit
on. Mixed mode remains supported. `test_catalogue_base.py`'s 22 stand-in sites
re-point from Recipe/Spell to AA/Class catalogues.

## Schema DDL — `db/migrations/0007_items.sql` / `0008_spells.sql` / `0009_recipes.sql` / `0010_lockdown2.sql`

- Two-line family header convention (meta-test enforced) like 0001–0005.
- **All ids bigint** — items.id max is 4,294,964,656 (> int4); same for
  item_stats.item_id, spells.id/crc, recipes.id/crc, every `out_*_id`,
  recipe_classes.recipe_id, associated_quest.
- **SQLite REAL → double precision** (ilvl, item_stats.value, damage/delay,
  spell timings, aoe_radius).
- `raw_json` / `effects` / `classes_json` / `secondary_comps` /
  `classification_list` stay **text** (raw_json alone is ~1.38 GB; nothing
  queries into it at runtime — the one `json_extract` and the `LIKE '%pvp%'`
  scan are build-time backfills, rewritten as `(raw_json::jsonb)->…` in the
  loader). No `\u0000` in current data, so a later jsonb move stays open.
- `recipes.out_level` becomes a real column in CREATE TABLE (today it exists
  only as a SQLite ALTER migration and the upsert never writes it).
- Each schema gets its own `_meta` k/v table (download resume offsets +
  backfill version gates keep working; `get_meta` fixed for dict rows).
- Port all indexes (14 items + 3 item_stats + 10 spells + 10 recipes).
- 0010 enables RLS + anon/authenticated revokes for the three new schemas
  (0006's loop hard-codes the original five and its header says new schemas
  bring their own).
- `metrics.py`: drop the three file-size gauges; extend the hard-coded
  5-schema `pg_schema_size_bytes` list to 8.

## Catalogue ports (PgCatalogue + SchemaBound, like census/zones/raids)

Mechanical layer (same recipes as P2 of the main migration):
- `?`/`:name` → `%s`/`%(name)s`; `INSERT OR REPLACE/IGNORE` → `ON CONFLICT`;
  `IN (?…)` chunking → `= ANY(%s)` (also fixes guild_cache's unchunked
  find_by_ids over every member's spell ids — single query, no 999 chunks);
  aiosqlite `find_by_name`/`find_by_id` → `pg.aconnection()`.
- **dict rows break ~20 positional sites** (items.py ×8, spells
  `upgradeable_crcs`/`spell_count`, recipes `_backfill_spell_tiers`/count,
  api/item.py ×3, api/recipes.py ×4, upgrades.py, app.py ×2) — inventory has
  exact lines; `recipes._row_to_result` catches IndexError which becomes
  KeyError.
- **LIKE is case-sensitive in PG**: audit every LIKE; `beneficial_buff_tiers`
  (`name LIKE 'Base %'` on mixed-case column) → ILIKE or name_lower; escape
  user `%`/`_` reaching api/item.py:310 + api/recipes.py:303.
- **NULL sort order flips**: `NULLS LAST` on the DESC orderings over nullable
  columns (find_by_name variants, find_by_crc_highest_tier, items search).
- **Items search SQL must be rewritten** — `GROUP BY i.id` while selecting
  joined `s0.value` columns is a hard PG error. Rewrite with per-stat
  aggregates (or LATERAL), and fold the per-row follow-up query (N+1 × 50)
  into the main statement while in there.
- **Raw-file opens move to catalogue/schema reads**: api/item.py (search +
  spell-scroll), api/recipes.py (both DBs), upgrades `_lookup_items_by_name`
  (f-string IN list — parameterize while porting), app.py counts. After this,
  prod aiosqlite usage is gone → drop the dependency.
- `.exists()` 503 guards → `catalogue.ready()` (cached row-count probe) so an
  unloaded schema still degrades to 503, preserving test semantics.

## Runtime behaviour fixes folded in (they get worse over a network)

- `census/client._cache_item` — the one per-request write — runs sync **on the
  event loop**; make it `asyncio.to_thread`/async. Same for the hot sync
  lookups where they sit in async handlers (gear_for_ids call sites, spells
  tab, stats `_resolve_ability`): in-region latency (~1 ms) makes them
  tolerable, but the two real N+1s get fixed: `_resolve_item_meta`'s ~25
  per-slot `find_by_id`s per character fetch → one `find_by_ids` batch; items
  search's 50 follow-ups → main query.
- **Startup `_ensure_item_stats` / `_ensure_recipe_levels` threads and the
  per-boot `_post_init` backfills are deleted.** Correctness moves to the
  loaders (out_level computed at load via one cross-schema UPDATE…FROM; 8,685
  legitimately-NULL recipes stop re-triggering the 372k-item dict load every
  boot; `_backfill_spell_tiers`'s 36k-row rescan every boot dies too).
  PgCatalogue.init_db() returns a pooled proxy — no DDL, no backfills per call
  (today four items methods re-run the whole DDL + backfills on every call).

## Build/refresh pipeline

- `download_items/spells/recipes.py` → write to PG via the catalogue upserts
  (ON CONFLICT), resume via schema `_meta`. Chunked executemany is fine at
  refresh volumes.
- `backfill_item_stats` / `backfill_recipe_levels` / `build_recipe_classes`
  become cross-schema SQL against PG (recipe_classes = items⋈recipes join —
  the chunks-of-900 Python stitch dies). The other items backfills fold into
  item_to_row / the loader.
- **Initial bulk load**: extend `scripts/migrate_to_postgres.py` with the
  three families (its introspected COPY + transforms already fit), sourced
  from the LOCAL `data/*.db` — no volume download needed. Run with
  `SET statement_timeout = 0` (already in). Expect items ≈ 10–20 min.
- Disk: ~+3–3.5 GB with indexes → ~5 GB total of the 8 GB Supabase disk. OK;
  check headroom after load.

## Tests

- `tests/fixtures/pg.py` SchemaLeaser gains the three families from the
  migration headers (same retarget substitution).
- Re-point per inventory counts: items 10 constructors + ~30 patches; spells
  10 + ~50; recipes 30 + ~45; ~25 raw sqlite3 seeding sites → `pg_conn`.
  conftest's DB_ITEMS_PATH gap (items tests currently read the dev's real
  2.4 GB file) disappears with leases.
- Keep the "missing → 503" route tests via `ready()`.

## Cutover (easy mode — read-only data, no user writes to lose)

1. Branch `catalogues-pg`; land migrations + ports; CI green.
2. Bulk-load the three schemas into Supabase from local data/*.db (site
   unaffected — new schemas are dark until the deploy).
3. Push to main. The deploy itself is the flip; no maintenance window. The
   only write path (_cache_item upserts) switches atomically with it.
4. Verify: item page + tooltip + search, recipes page + shopping list,
   character page / spells / upgrades / rotation sim, guild spellcheck +
   adorn check, stats ability names, bot /item + /spellcheck.
5. After confidence: detach/delete the Railway volume, remove
   DB_ITEMS/SPELLS/RECIPES_PATH (+ confirm DB_AAS_PATH is NOT set on Railway —
   aas.db must resolve to the repo copy), drop aiosqlite, delete the R2
   litestream prefixes, simplify railway.toml startCommand (no mkdirs).
   This closes the SQLite-era rollback for good.

## Open questions (user)

- None blocking. aas/classes staying SQLite is the default unless told
  otherwise; volume deletion waits for explicit go-ahead after verification.
