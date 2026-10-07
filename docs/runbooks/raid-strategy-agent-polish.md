# Runbook: agent polish passes over raid strategies

Raid strategies in the `raids` schema start life as EQ2i wiki scrapes (see "Raid-strategies seed
pipeline" in CLAUDE.md). Scraped markdown is noisy: anecdotes, out-of-era notes, zone-level
content stuck inside one boss's strategy, per-boss content stuck in a zone overview. Three
offline passes clean this up by exporting content into JSON chunks, having parallel AI agents
rewrite each chunk, and applying the results back.

All six scripts live in `scripts/dev/` and read/write the raids schema at `DATABASE_URL` via
`backend.eq2db.raids.catalogue`. Run them with `uv run python scripts/dev/<script>.py`.

## How it works

Each pass is an export / agent / apply triple:

| Pass | Export | Inbox / outbox under `data/raids/` | Apply | `last_edited_by` |
|---|---|---|---|---|
| Strategy polish | `export_for_polish.py` (`--chunks`, default 6) | `polish_inbox/` / `polish_outbox/` | `apply_polish.py` | `ai-polish` |
| Zone overview audit | `export_zone_overviews.py` (`--chunks`, default 2) | `zone_overview_inbox/` / `zone_overview_outbox/` | `apply_zone_overviews.py` | `ai-audit` |
| Zone/encounter rebalance | `export_for_rebalance.py` (`--chunks`, default 4) | `rebalance_inbox/` / `rebalance_outbox/` | `apply_rebalance.py` | `ai-rebalance` |

- **Export** greedily bin-packs the content into N chunks by character count, so the agents
  finish in similar wall-clock time, and writes `chunk_<n>.json` to the inbox. Each export clears
  the inbox's old `chunk_*.json` first so a previous run cannot leak into the next.
- **Agents** each take one inbox chunk and write a matching `chunk_<n>.json` to the outbox.
- **Apply** reads every `chunk_*.json` in the outbox (or one file via `--in <path>`), supports
  `--dry-run`, and matches rows by zone name / mob name case-insensitively. It reports entries
  whose names do not match an existing row rather than inventing them.

Every applied row is stamped `source = manual`. That is the point: the scrape ingest
(`ingest_raids_json.py` → `upsert_raid_encounter`) skips manual rows, so a re-scrape never
undoes a polish pass. The distinct `last_edited_by` identity lets the revision history tell AI
passes apart from human edits.

An empty (or whitespace-only) output field means "this content was entirely noise / belonged
elsewhere": the apply step sets the column to NULL with a targeted `UPDATE`. It does not go
through `upsert_raid_encounter` for that case, because the helper treats a `None` strategy as
"no change".

## Order

1. Strategy polish, then the zone overview audit (per-boss content out of overviews).
2. Rebalance last: it goes the other way, pulling zone-level content (a "Zone Layout" section
   inside a single boss's strategy is the classic case) out of strategies and merging it into
   the overview. Chunking is by zone, so an agent sees all of a zone's encounters at once and
   can merge without duplicating content across the overview and the strategies.

`export_for_polish.py` skips rows that are already `manual` by default (`--include-manual`
overrides), so re-running the polish pass does not re-polish hand edits or earlier passes.

## Chunk shapes

### Strategy polish

Inbox:

```json
{
  "chunk_id": 1,
  "total_chars": 18234,
  "entries": [
    {
      "zone_name": "Veeshan's Peak",
      "mob_name": "Druushk",
      "position": 1,
      "wiki_url": "https://eq2.fandom.com/wiki/Druushk",
      "current_md": "...scraped strategy markdown..."
    }
  ]
}
```

Outbox (wrapped in `{"entries": [...]}` or a bare list):

```json
{ "zone_name": "Veeshan's Peak", "mob_name": "Druushk", "polished_md": "..." }
```

### Zone overview audit

Inbox entries: `{"zone_name": "...", "current_md": "...current overview..."}`.

Outbox: `{"entries": [{"zone_name": "...", "cleaned_md": "..."}]}`. An empty `cleaned_md` means
the overview was entirely per-boss content; the field is nulled and the UI shows no overview.

### Rebalance

Inbox:

```json
{
  "chunk_id": 1,
  "total_chars": 18234,
  "zones": [
    {
      "zone_name": "Trakanon's Lair",
      "expansion_short": "RoK",
      "current_overview_md": "...",
      "encounters": [
        { "mob_name": "Trakanon", "position": 1, "wiki_url": "...", "current_strategy_md": "..." }
      ]
    }
  ]
}
```

Outbox (wrapped in `{"zones": [...]}` or a bare list; consumed verbatim):

```json
{
  "zone_name": "Trakanon's Lair",
  "updated_overview_md": "...",
  "encounters": [ { "mob_name": "Trakanon", "updated_strategy_md": "..." } ]
}
```

Empty `updated_overview_md` / `updated_strategy_md` nulls that field.
