# EQ2Logs — Boss-Kill Rankings

**Date:** 2026-05-25
**Status:** Design approved, pending implementation plan

## Summary

A Warcraft-Logs–style rankings feature ("EQ2Logs") layered on top of the
existing parses pipeline. It surfaces leaderboards for successful boss kills:
pick a scope (Raid / Group) → zone → boss → metric (Damage / Healing / Speed)
→ class, and see a ranked, percentile-coloured table. Each row links back to
the parse it came from.

The feature is **computed-on-read** — no separate ranking store. Boards are a
cached query over the existing `encounters` / `combatants` tables. The only
schema change is a soft-delete marker that preserves leaderboard entries when
a user deletes the underlying parse.

EQ2's ACT data is far less granular than WoW's (no talents/specs/buffs), so the
boards are correspondingly simpler — but the core ranking experience carries
over.

## Goals

- Automatically detect successful boss fights and rank them, no manual tagging.
- Per-character boards for **Damage** (encDPS) and **Healing** (encHPS); a
  per-guild board for **Speed** (fastest clear).
- Smart, fully data-driven dropdowns (populated from uploaded data).
- Warcraft-Logs percentile colouring (100 gold → 0–24 grey).
- Rankings reflect each character's state **at parse time** (level, guild,
  class) — the per-combatant snapshot already shipped supplies this.
- Clicking a board row opens the originating parse, and that link survives the
  user deleting the parse from their list.

## Non-goals

- Difficulty/mode tiers (normal vs challenge) — ACT exposes no signal, so all
  same-size kills of a boss share one board.
- Specs / talents / buff uptime / any WoW-specific granularity.
- A separate materialised ranking database.
- Backfilling rankings from parses that predate the ACT success flag.

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Boss detection | Existing `isBoss` heuristic — first character uppercase = named boss; trash is "a/an …" (lowercase article) | Already used on the parses page; reliable for EQ2 naming. Ported server-side as the authoritative `is_boss(title)`. |
| Kill eligibility | Wins only (`success_level == 1`) + `isBoss` | Clean "successful kill" semantics. Parses with unknown flag (0) are excluded until re-uploaded. |
| Rank metric | **encDPS** for Damage, **encHPS** for Healing | Raid-contribution over full encounter duration; matches ACT's headline number and how EQ2 players compare. |
| Speed unit | Best (min-duration) clear **per guild** | One row per guild, mirrors the per-character PB idea at guild level. |
| Board identity | `(size bucket, zone, boss title, metric)` | Separates a boss's group vs raid version and same-named mobs across zones. Class is a filter within Damage/Healing, not part of the key. |
| Zone categories | Auto-derived from kill size: raid-size (>6) kills → "Raid Zones"; group-size (2–6) → "Dungeons" | Zero curation; the scope filter decides which zone list shows. |
| Percentile pool | Damage/Healing: **same boss + same class, all guilds**. Speed: **all guilds on that boss**. | Warcraft-Logs model; class-fair. Coarse with little data, meaningful as uploads grow. |
| Architecture | **Computed-on-read**, cached | No write path, no staleness; deletes and PB updates fall out for free. |
| Delete vs board | **Soft-delete** boss-kill parses (preserve standing + link); **hard-delete** trash; **admin hard-purge** for true erasure | Preserves leaderboard integrity and click-through while keeping the parses list clean. |
| Build scope | All three metrics in the first build | They share the computed-on-read query layer. |

## Architecture

### Computed-on-read

No new ranking tables. A new read-only router `web/routes/rankings.py` computes
boards on demand from `encounters` + `combatants`, dispatched to a thread via
`run_in_executor` (same pattern as `recipes.py` / `parses.py`) and cached
through the existing stale-while-revalidate `TTLCache`.

Consequences (all "for free"):
- **Deletes just work** — a removed parse leaves the pool and the board
  recomputes; the next-best parse becomes the new standing. (See soft-delete
  below for the preservation case.)
- **PB updates on new parse** — a better parse simply wins the `MAX` at the
  next read.
- **Primary-only** — the query reuses the existing mirror-grouping to take the
  primary (longest-duration) upload of each fight; no cherry-picking.
- **Always consistent**, zero materialisation drift.

Cost: each load does mirror-grouping + boss detection + percentile. At a
guild's data scale (hundreds–low thousands of encounters) this is trivial, and
the cache absorbs repeat loads.

### Schema change — soft delete

Add a nullable `hidden_at INTEGER` column to `encounters` via the existing
idempotent `_MIGRATIONS` list. Semantics:

- `/parses` list query gains `WHERE hidden_at IS NULL` — soft-deleted parses
  disappear from that list.
- The **rankings** query ignores `hidden_at` — a soft-deleted parse that holds
  a personal best still ranks and stays openable.
- `GET /parses/{id}` serves a parse regardless of `hidden_at`, so leaderboard
  links always open. The detail page shows a small "removed from listing" note
  when `hidden_at` is set.
- A **hard purge** (admin only) truly cascade-deletes the row, which also
  removes it from boards.

### Boss detection

Port the frontend `isBoss` to a shared Python helper (next to `parses`):

```python
def is_boss(title: str) -> bool:
    # EQ2 trash is "a krait warrior" / "an ancient guard" (lowercase article);
    # bosses have a proper capitalised name. First-char uppercase is the signal.
    return bool(title) and "A" <= title[0] <= "Z"
```

Used by both the rankings query and the delete path. The frontend keeps its
copy with a comment noting the server version is authoritative.

## Ranking computation

**Board key:** `(size bucket, zone, boss title, metric)`. The size bucket is
**Raid | Group**: Group = 2–6 players, Raid = 7–24 (so 12- and 24-player kills
of the same boss share one Raid board). Individual (1) is excluded. The table's
Size column still shows the actual player count, so a 12 vs 24 kill is visible
within the board.

**Eligibility:** `is_boss(title)` AND `success_level == 1`, taking the primary
upload of each mirror group.

**Damage / Healing (per character):**
1. From each qualifying primary kill, take every ally *player* combatant
   (single-word name, not the `Unknown` rollup) and their encDPS / encHPS.
2. Keep each character's **best** score across all their kills of that board →
   one row per character.
3. Rank descending.

**Speed (per guild):** per qualifying primary kill, take the encounter
duration; keep each guild's **minimum** → one row per guild, ranked ascending.

**Percentile + colour:** rank-based within the pool. For a row at rank `r` of
`N`:

```
percentile = round(100 * (N - r + 1) / N)
```

Best is always 100; N=4 yields 100/75/50/25, etc. Ties take the better rank.
The pool is same-boss + same-class (Damage/Healing) or all-guilds-on-boss
(Speed). The Class filter narrows visible rows only — it never changes a
percentile. Percentile maps to the Warcraft-Logs colour brackets:

| Percentile | Colour | Hex |
|---|---|---|
| 100 | gold | `#e5cc80` |
| 99 | pink | `#e268a8` |
| 95–98 | orange | `#ff8000` |
| 75–94 | purple | `#a335ee` |
| 50–74 | blue | `#0070ff` |
| 25–49 | green | `#1eff00` |
| 0–24 | grey | `#666666` |

**Smart dropdowns (data-driven):** Scope shows only sizes with qualifying
kills; Zone shows zones with kills at that size; Boss shows bosses with kills
in that zone+size; Class shows classes that actually have an entry on the
selected board.

### Edge cases

- **Unresolved class** (combatant `cls` snapshot is NULL — e.g. a pug whose
  Census lookup failed at ingest): excluded from the per-character Damage /
  Healing boards, since there's no class to rank them within. The snapshot
  resolves all guildmates, so this only drops genuinely unidentifiable
  players; re-uploading once they're cache-resolved includes them.
- **Unresolved guild** (encounter `guild_name` is NULL): excluded from the
  Speed board, which is keyed by guild. The Damage/Healing boards still show
  the character (guild column just renders blank).
- **Pets / `Unknown` rollup rows:** never eligible — the per-character boards
  only take single-word ally combatant names that aren't `Unknown`, matching
  the existing player-detection heuristic.

## API

New read-only router `web/routes/rankings.py`, auth = same authenticated-user
gate as the parses read endpoints.

- `GET /api/rankings/filters` → the dropdown tree in one call: scopes present,
  zones under each, bosses under each zone. Small payload, cached.
- `GET /api/rankings?size=&zone=&boss=&metric=&class=` → the ranked board:
  ordered rows + the available class list for that board + total `N`.

Unified row model with a `kind` field:
- `kind="character"`: `name`, `guild_name`, `level`, `cls`, `score`,
  `percentile`, `size`, `date`, `encounter_id`.
- `kind="guild"` (Speed): `guild_name`, `duration_s`, `percentile`, `size`,
  `date`, `encounter_id`.

## Frontend

New `RankingsPage.tsx` at route `/rankings`, plus a nav link.

- Filter bar (Scope, Zone, Boss, Metric, Class) driven by `/api/rankings/filters`
  and the selected board, with selections mirrored to URL params (shareable,
  as `RecipesPage` does).
- One table component rendering character-rows or guild-rows by metric. Row
  click → `/parse/{encounter_id}`.
- New `frontend/src/percentileColors.ts` mapping percentile → bracket colour,
  kept separate from `rarityColors.ts` (different scale). Colours are
  data-driven, so inline `style` is the sanctioned dynamic use under the
  Tailwind rules; everything static uses utilities.
- Reuses `Card`, `Breadcrumb`, `SectionLabel`, and `formatters`.

## Deletion touchpoints

`parses/db.py`:
- Migration: `ALTER TABLE encounters ADD COLUMN hidden_at INTEGER`.
- `soft_delete_encounter(conn, id)` setting `hidden_at`; keep `delete_encounter`
  for the hard path.

`web/routes/parses.py`:
- `delete_parse`, `delete_parses_batch`, and bulk `delete_encounters_by_filter`
  → soft-delete boss kills (`is_boss(title)`), hard-delete trash.
- New admin-only hard purge: a `?purge=1` flag on the delete endpoints, gated
  by `_is_admin`, that truly cascade-deletes.
- `/parses` list filters `WHERE hidden_at IS NULL`; detail endpoint serves
  hidden rows (with the "removed from listing" note surfaced to the frontend).

## Testing

Backend (pytest, mirroring `tests/web/test_parses*.py` and `tests/parses/`):
- `is_boss` detection (uppercase vs "a/an" articles, empty).
- Board computation: per-character PB (best kept, one row per character),
  primary-only selection, wins-only gate, the percentile formula, per-guild
  Speed (min duration).
- `/api/rankings/filters` tree shape; `/api/rankings` board responses for each
  metric, including the class filter and within-class percentile invariance.
- Soft-delete matrix: boss → soft (stays in rankings + detail, gone from list),
  trash → hard, admin purge → fully erased (gone from boards).

Frontend: `tsc --noEmit` + the existing ruff/pyright/tsc pre-push gates.

## Rollout

Single feature branch. The migration is additive; no data regeneration. Boards
populate as win-flagged parses accumulate; pre-flag parses (success = 0) are
absent until re-uploaded, consistent with the wins-only decision.
