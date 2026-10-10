# Zone metadata refresh and raid-strategy seeding

## Zone metadata refresh (zones schema)

Zone metadata is built from the cleaned wiki dump straight into Postgres — no file upload. Boss rosters are **web-editable** curator data; a rebuild upserts `zones` by stable id and fully replaces `zone_types`/`zone_aliases`, never touching `zone_encounters` / `zone_encounter_mobs` (removed zone names ARE pruned, cascading their rosters — the script prints the count; a renamed source zone reads as new+pruned, so check that output).

```powershell
python scripts/dev/clean_eq2_zones.py     # source → cleaned JSON
uv run python scripts/build_zones_db.py   # cleaned JSON → the zones schema (DATABASE_URL)
uv run python scripts/dev/_smoke_test_zones_db.py
```

## Raid strategies seed (raids schema)

Raid strategies are hybrid — wiki-seeded plus user edits + revision history.

1. **First-time seed** (once, before any production user edits):

   ```powershell
   python scripts/dev/scrape_eq2i_raids.py --all-raids
   uv run python scripts/dev/ingest_raids_json.py --in scripts/dev/eq2_raid_data.json
   ```

   Writes into the raids schema at `DATABASE_URL`.

2. **Refreshing scraped content**: re-run the scrape, then the ingest. `upsert_raid_encounter` skips `SOURCE_MANUAL` rows — human edits survive every re-scrape.

## Raid-strategies seed pipeline

Two-stage by design — the network-dependent scrape is decoupled from the fast local DB ingest:

  1. **Scrape** (`scrape_eq2i_raids.py`) — fetches EQ2i zone/encounter pages via the polite-API cache (`scripts/dev/.eq2i_cache/`, gitignored). Produces JSON only. Discovers zones via `zones_db.list_by_expansion` filtered to raid types — no hardcoded URL list.
  2. **Ingest** (`ingest_raids_json.py`) — reads the JSON, calls `raids_db.upsert_raid_zone` + `upsert_raid_encounter` with `source=SOURCE_SCRAPE`. The helper **skips rows with `source=SOURCE_MANUAL`** on re-scrape, so a re-run never clobbers a human edit.

`eq2_raid_data.json` (the full-scrape output) is **committed** so a fresh clone can run `ingest_raids_json.py` without re-scraping. The intermediate HTTP cache and 3-zone sample JSON are gitignored.

## Local testing scripts

See [scripts/README.md](scripts/README.md) for the full list of preview, download, and DB-build scripts.
