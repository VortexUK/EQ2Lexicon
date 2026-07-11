# Scripts

Preview, download, and DB-build utilities for local development. Run all scripts from the repo root with `python scripts/<name>.py`.

## Preview / inspection

```bash
python scripts/preview_item.py "Faded Black Hood"        # render tooltip image
python scripts/inspect_item.py "Faded Black Hood"        # dump raw Census JSON
python scripts/preview_guild.py "Exordium"
python scripts/preview_spellcheck.py Sihtric
python scripts/preview_spellcheck.py Sihtric --details
python scripts/preview_spellcheck.py Sihtric --debug     # show each counted spell
python scripts/preview_aa_tree.py 25                     # render AA tree by ID
python scripts/preview_aacheck.py Menludiir              # list character AA trees
python scripts/preview_aacheck.py Menludiir Templar      # render a specific tree
```

## Data downloads

```bash
python scripts/download_aa_trees.py                      # fetch all AA tree JSONs from Census
python scripts/download_aa_icons.py                      # fetch all AA node icon PNGs
python scripts/build_aas_db.py                           # tree JSONs -> data/AAs/aas.db (committed)
python scripts/download_spell_icons.py                   # download all spell icon PNGs
python scripts/download_spell_icons.py --start N         # resume from icon N
python scripts/download_spells.py --guild "Guild Name"   # seed spell cache DB for a guild
python scripts/download_spells.py --guild "Guild Name" --refresh   # force re-fetch all
python scripts/download_recipes.py                       # download all ~70k recipes into data/recipes/recipes.db
python scripts/download_recipes.py --limit 500           # test run (500 recipes)
python scripts/download_recipes.py --restart             # ignore saved offset, re-download from scratch
```

## Zone DB build

```bash
python scripts/dev/clean_eq2_zones.py                   # source JSON → cleaned JSON
python scripts/build_zones_db.py                        # cleaned JSON → SQLite
python scripts/dev/_smoke_test_zones.py                 # validate cleaned JSON
python scripts/dev/_smoke_test_zones_db.py              # validate built SQLite DB
```

## Raid-strategies seed pipeline

Two-stage — the network-dependent scrape is decoupled from the fast local ingest.

```bash
# Stage 1: scrape EQ2i (produces eq2_raid_data.json, committed to repo)
python scripts/dev/scrape_eq2i_raids.py                 # ~3 sample zones (PoC)
python scripts/dev/scrape_eq2i_raids.py --all-raids     # every Vanilla–RoK raid (~60 zones)

# Stage 2: ingest into data/raids/raids.db
python scripts/dev/ingest_raids_json.py                 # ingest sample JSON
python scripts/dev/ingest_raids_json.py --in scripts/dev/eq2_raid_data.json   # ingest full scrape
python scripts/dev/ingest_raids_json.py --dry-run       # parse + summarise without writing
```

The ingest is re-run safe — `SOURCE_MANUAL` rows (human edits) are never overwritten. The intermediate HTTP cache (`scripts/dev/.eq2i_cache/`) and the 3-zone sample JSON are gitignored; the full `eq2_raid_data.json` is committed so a fresh clone can ingest without re-scraping.
