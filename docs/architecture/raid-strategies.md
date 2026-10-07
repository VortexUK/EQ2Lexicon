# Raid strategies (`raids` schema)

Raid strategies are hybrid content: seeded from the EQ2 wiki (EQ2i), then
hand-edited by guild officers and contributors on the site. The catalogue is
`backend/eq2db/raids.py` (`RaidCatalogue`, shared instance usually imported as
`raids_db`); the DDL is `db/migrations/0005_raids.sql`; the HTTP surface is
`backend/server/api/raid_strategies.py` (strategy/overview read + write) and
`backend/server/api/act/` (ACT triggers and spell timers).

## Tables

| Table | What it holds |
| --- | --- |
| `raid_zones` | One row per raid zone: zone-level markdown (`access_md`, `background_md`, `overview_md`) plus wiki-owned metadata (`expansion_short`, `wiki_url`, level range, zdiff, lockouts, `last_synced_at`). Linked to the zones family by `zone_name` only — it lives in a different schema, so there is no enforced FK. |
| `raid_encounters` | One row per named boss within a raid zone. `strategy_md` is a single markdown blob. Unique on `(raid_zone_id, mob_name_lower)`. |
| `raid_encounter_revisions` | History of `strategy_md`: `before_md` / `after_md` / `edited_by` / `edited_at` / optional `edit_note`. The first row for an encounter has `before_md = NULL`. |
| `raid_zone_revisions` | The same history for the zone overview. |
| `act_triggers` | ACT trigger rows for an encounter (see below). |
| `act_spell_timers` | ACT spell-timer definitions for an encounter. |

Content scope is the TLE cycle (Vanilla through Rise of Kunark for the wiki
seed); the schema itself has no expansion restriction.

### Encounter keys

The site addresses an encounter by `(zone_name, position)` — the curator-managed
boss roster in the zones schema (`zone_encounters`). The route resolves that to
`zone_encounters.encounter_name` and uses the string as `raid_encounters.mob_name`,
so there is one strategy per curated encounter, keyed by display name; a group
encounter gets a single strategy under its joined name. A write for a zone the
raids schema has not seen yet creates the `raid_zones` row on the fly, taking
`expansion_short` from the zones catalogue.

Curator edits to the zones roster (rename, reorder, delete) are mirrored into
`raid_encounters` by `rename_raid_encounter_if_exists`,
`update_raid_encounter_if_exists` (position) and
`delete_raid_encounter_by_zone_mob`, so strategies follow their boss. These
take an open connection and do not commit.

## Provenance (`source`)

`raid_zones.source` and `raid_encounters.source` record where the content came
from. The tokens are module constants mirrored on the catalogue class
(`raids_db.SOURCE_MANUAL`), and `upsert_*` rejects anything outside
`VALID_SOURCES`:

| Token | Meaning |
| --- | --- |
| `eq2i_scrape` (`SOURCE_SCRAPE`) | Extracted from the wiki and not edited since. |
| `manual` (`SOURCE_MANUAL`) | Added or edited by a person in the site editor. |
| `parse_data` (`SOURCE_PARSE`) | Reserved for content derived from encounter parses. |

A row moves from `eq2i_scrape` to `manual` on its first hand edit. The revision
tables keep the original scraped text.

### Manual content is never overwritten by a re-scrape

`upsert_raid_zone` / `upsert_raid_encounter` are the write path for the scrape
ingest. Their behaviour depends on the existing row:

- **New row** — inserted as given (an encounter also gets its first revision row).
- **Existing scraped row, re-scraped** — wiki-owned fields and the markdown are
  refreshed, so wiki edits propagate. A changed `strategy_md` writes a revision.
- **Existing manual row, re-scraped** — only wiki-owned metadata is refreshed
  (zone: expansion, url, level range, zdiff, lockouts, sync time; encounter:
  url, position, sync time). Markdown and `source` are left alone.

Zone upserts `COALESCE` every nullable column, so passing `None` means "don't
touch", never "set to NULL". This matters because the strategy route
auto-creates the parent zone with `upsert_raid_zone(..., source=MANUAL)` and no
overview; a plain `excluded.col` upsert would wipe an existing overview. To
clear a field, use a targeted `UPDATE` (the route's `_update_overview_sync` /
`_write_overview_sync`), which is also the canonical path for human edits:
they touch only the edited field and stamp `last_edited_at`, which the upserts
never set.

## ACT triggers and spell timers

These tables back the `/act` editor and the EQ2Parser trigger pack.

- One `act_triggers` row maps 1:1 to a `<Trigger>` element in ACT's
  `spell_timers.xml`; columns mirror the XML attributes in snake_case. Web-only
  curation fields (`position`, `label`, `notes`) and EQ2Parser enrichment
  (`cooldown_seconds`, served by `/api/act/pack`) are never exported to ACT XML,
  so plain ACT exports stay byte-compatible.
- One `act_spell_timers` row maps 1:1 to a `<Spell>` element. A trigger with
  `timer = 1` references a timer by `timer_name` within the same encounter (a
  loose name FK). Several triggers may share one timer; export deduplicates by
  name and emits both rows so the file round-trips in ACT without fix-up.
- `absolute_` carries a trailing underscore to avoid the SQL keyword.
- Both tables cascade-delete with their encounter, and every write stamps
  `last_edited_at` / `last_edited_by`.

The write helpers (`upsert_act_trigger`, `upsert_act_spell_timer`, and the
deletes) take an open connection so a caller can batch several writes in one
transaction; read helpers open their own pooled checkout per call.

## Seeding pipeline: scrape, then ingest

The network-dependent scrape is decoupled from the fast local load:

1. **Scrape** — `scripts/dev/scrape_eq2i_raids.py` discovers raid zones from the
   zones catalogue (`list_by_expansion`, filtered to raid types), fetches the
   EQ2i zone and encounter pages through a polite on-disk cache
   (`scripts/dev/.eq2i_cache/`, gitignored), converts wikitext to markdown with
   `backend/census/wikitext_md.py`, and writes JSON only.
   `scripts/dev/eq2_raid_data.json` (the full scrape) is committed so a fresh
   clone can load without re-scraping.
2. **Ingest** — `scripts/dev/ingest_raids_json.py` reads that JSON and calls
   `upsert_raid_zone` / `upsert_raid_encounter` with `source=SOURCE_SCRAPE`
   against the schema at `DATABASE_URL`. Because of the rules above, re-running
   it never clobbers a human edit.

ACT triggers have a separate one-off loader, `scripts/dev/ingest_act_triggers.py`,
which maps an ACT `spell_timers.xml` export onto `(zone_name, position)`
encounters (dry-run by default, `--apply` to write; idempotent).
