# Editable Raid Boss Roster — Design

**Date:** 2026-05-28
**Status:** Design approved, pending implementation plan

## Problem

The per-zone boss roster (which bosses, in what order, with which group-mobs) is
**read-only reference data** in `zones.db` today — built once from
`scripts/dev/eq2_raid_bosses.review.txt` via `scripts/build_zones_db.py`. Any
correction (rename, reorder, add/remove a boss, add/remove a sibling mob in a
multi-mob encounter) requires a maintainer to edit the source text file, rebuild
`zones.db` locally, and re-upload it to the Railway volume. That round-trip is
incompatible with how curators (admins + the `contributor` role) want to work.

Two consequences of the current model the user explicitly wants fixed:
1. **No web editing.** Curators can't add, remove, reorder, rename, or manage
   sibling mobs of any boss.
2. **Boss display name is the comma-joined list of every mob in the encounter**
   (`encounter_name = "Ire, Malevolence"`) because the curated text file lists
   group encounters as a single comma-separated line. The boss "name" should be
   one of the encounter's mobs — the primary — with the other mobs as named
   siblings.

## Key finding (from the codebase)

The model is closer to ready than it looks:

- `census/zones_db.py` `zone_encounters` (with `position` per zone) +
  `zone_encounter_mobs` (with `position` per encounter) already cleanly support
  the "primary mob + siblings" idea. **No schema change needed** — just a
  convention shift (position-0 mob = primary; `encounter_name` = primary's name).
- `raid_encounters` in `raids.db` is a denormalised mirror that exists only when
  triggers / spell timers / strategies have been added to an encounter; it
  references the boss by stable numeric `encounter_id`. ACT triggers
  (`act_triggers`), spell timers (`act_spell_timers`), and strategy revisions
  all FK to `raid_encounters(id) ON DELETE CASCADE`. So renames + reorders are
  safe (the FK is by id); deletes correctly cascade.
- The encounter URL key `(zone_name, position)` is resolved on every call by
  `_resolve_encounter_sync` (`web/routes/act_triggers.py`) — so triggers/timers
  endpoints stay correct across reorders.
- No `world` column anywhere in the roster / triggers / timers chain — raids
  data is **global reference** across servers (confirmed for per-server work).

## Approved decisions

| Decision | Choice |
|---|---|
| Storage location | **Keep the roster in `zones.db`** (`zone_encounters` + `zone_encounter_mobs`). Add web write endpoints on top. No new table, no data move. |
| "Primary mob" model | **`encounter_name` = the primary mob's name**, kept in sync with `zone_encounter_mobs` row at `position = 0`. Siblings are `position >= 1` in the same table. Promoting a sibling = swap positions 0↔N and update `encounter_name` in one transaction. |
| Curated source file | **Decommission** — delete `scripts/dev/eq2_raid_bosses.review.txt`, drop the `--curated-bosses` flag from `scripts/build_zones_db.py`, remove the curated-boss parser. The roster lives only in the DB. |
| Live-data backup | **Add `zones.db` to `litestream.yml`** alongside `users.db` / `parses.db`. zones.db now holds curator edits; a volume wipe must not lose them. Tiny file (~1 MB), reuses the existing R2 env vars. |
| Auth gate | `@Depends(require_editor)` (admin **or** `contributor`) — same gate as triggers/timers/strategies. |
| URL stability on reorder | **Accept the URL break.** `/raids/{zone}/{position}` shifts when reorder changes positions; the page already refetches on roster change so internal nav follows. External bookmarks shift — acceptable; the user explicitly wants drag-reorder. |
| Per-server scoping | **None.** Raids data stays global reference, as today. |
| Drag-and-drop library | **`@dnd-kit/sortable`** — modern, accessible, React-19 friendly, small. No DnD lib in `package.json` today; this is a small add. |
| Future "any mob in encounter" leaderboard | **Out of scope here, but supported by this design with zero additional schema work** — once `zone_encounter_mobs` is the authoritative mob list, the future rankings query joins parses → `zone_encounter_mobs.mob_name_lower` → encounter, giving "kill any mob in the encounter" matching for free. |

## Architecture

### Data model — `zones.db` (no schema change)

Existing tables stay as-is:

```sql
-- already exists
CREATE TABLE zone_encounters (
    id              INTEGER PRIMARY KEY,
    zone_id         INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    encounter_name  TEXT    NOT NULL,   -- now: PRIMARY mob's name (no comma lists)
    position        INTEGER NOT NULL,   -- order within the zone
    stage           TEXT,
    wiki_url        TEXT,
    UNIQUE (zone_id, position)
);

-- already exists
CREATE TABLE zone_encounter_mobs (
    id              INTEGER PRIMARY KEY,
    encounter_id    INTEGER NOT NULL REFERENCES zone_encounters(id) ON DELETE CASCADE,
    mob_name        TEXT    NOT NULL,
    mob_name_lower  TEXT    NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0  -- 0 = primary; 1..N = siblings
);
```

Two index additions, declared in `_SCHEMA` (additive, idempotent — `CREATE INDEX
IF NOT EXISTS`):

```sql
-- enables fast reverse lookup ("did this parse mob match any encounter mob?"),
-- consumed both by find_zones_by_boss today and the future "any mob" leaderboard
CREATE INDEX IF NOT EXISTS idx_zem_mob_lower
    ON zone_encounter_mobs(mob_name_lower);

-- enables ordered enumeration per encounter without a sort
CREATE INDEX IF NOT EXISTS idx_zem_encounter_pos
    ON zone_encounter_mobs(encounter_id, position);
```

### One-time data normalization (auto, in `init_db`)

At app boot, `zones_db.init_db` normalizes any legacy `zone_encounters.encounter_name`
that holds a comma-joined list:

```python
# Idempotent. Touches only rows whose encounter_name contains a comma.
# Picks the position-0 mob from zone_encounter_mobs as the new primary name.
UPDATE zone_encounters
   SET encounter_name = (
         SELECT mob_name FROM zone_encounter_mobs m
          WHERE m.encounter_id = zone_encounters.id
          ORDER BY position ASC LIMIT 1
       )
 WHERE encounter_name LIKE '%,%';
```

After this runs once, `encounter_name` is always the primary mob's name, kept in
sync by every write helper (below). The migration is safe to re-run.

### Backend — new helpers in `census/zones_db.py`

**No column additions.** `zone_encounters` keeps exactly the schema it has today
(no `last_edited_at` / `last_edited_by`); curator-edit audit on the roster is a
deliberate non-goal here (can be added later if needed, parallel to how
triggers/timers track it, but it isn't blocking this work and avoids any
schema migration). Helpers (all sync; routes call via `run_in_executor`):

- `add_encounter(zone_id, primary_mob, position=None, stage=None, wiki_url=None) -> dict` — inserts both the `zone_encounters` row and a single `zone_encounter_mobs` row at position 0; if `position` is None, appends after the current max. Returns the new encounter row.
- `update_encounter(encounter_id, *, primary_mob=None, stage=None, wiki_url=None) -> dict` — when `primary_mob` is set, updates the position-0 mob and the cached `encounter_name`.
- `delete_encounter(encounter_id) -> bool` — single DELETE on `zone_encounters` (cascades siblings); also deletes the matching `raid_encounters` row when present (its CASCADE FK cleans up triggers/timers/strategies).
- `reorder_encounters(zone_id, ordered_encounter_ids: list[int]) -> None` — atomic bulk reorder; updates `zone_encounters.position` for every id in the list to 1..N in the given order. Validates that the list is a complete permutation of that zone's current encounter ids (no missing, no extras). Mirrors positions onto matching `raid_encounters` rows where present.
- `add_mob(encounter_id, mob_name, make_primary=False) -> dict` — appends to siblings at next position; when `make_primary=True`, instead inserts at position 0 (shifting existing primary to a sibling slot) and updates `encounter_name`.
- `update_mob(mob_id, mob_name) -> dict` — rename; if this mob is at position 0, also update the parent `encounter_name`.
- `promote_mob(mob_id) -> dict` — swap with the current position-0 mob (siblings ↔ primary), update `encounter_name`.
- `delete_mob(mob_id) -> bool` — refuse with a clear error if it's the encounter's only mob (encounter must have ≥1 mob); if it's the primary, the user must promote a sibling first.

Every write also keeps the **`raid_encounters` mirror** in sync when a row exists for the encounter: rename → update `mob_name`; reorder → update `position`. The lazy-create on first trigger/strategy save (in `_resolve_encounter_sync`) continues unchanged.

### Backend — new routes in `web/routes/zones.py`

All `@Depends(require_editor)`:

| Method | Path | Body | Notes |
|---|---|---|---|
| `POST`   | `/api/zones/{zone}/encounters` | `{ primary_mob, position?, stage?, wiki_url? }` | Append by default |
| `PUT`    | `/api/zones/{zone}/encounters/{id}` | `{ primary_mob?, stage?, wiki_url? }` | Edit metadata / rename primary |
| `DELETE` | `/api/zones/{zone}/encounters/{id}` | — | Cascades siblings + raid_encounters mirror + triggers/timers/strategies |
| `PUT`    | `/api/zones/{zone}/encounters/reorder` | `{ ordered_encounter_ids: [int, …] }` | Bulk reorder (drag commit) |
| `POST`   | `/api/zones/{zone}/encounters/{id}/mobs` | `{ mob_name, make_primary?: bool }` | Add sibling (or new primary) |
| `PUT`    | `/api/zones/{zone}/encounters/{id}/mobs/{mob_id}` | `{ mob_name? }` | Rename; primary rename also updates `encounter_name` |
| `POST`   | `/api/zones/{zone}/encounters/{id}/mobs/{mob_id}/promote` | — | Swap with current primary |
| `DELETE` | `/api/zones/{zone}/encounters/{id}/mobs/{mob_id}` | — | Refuses delete-last-mob; refuses delete-primary-when-siblings-exist (force a promote first) |

All routes resolve `zone_name → zone_id` via the existing
`zones_db.find_by_name`. All write paths re-fetch the zone after the mutation
and return the updated zone-with-bosses shape so the front-end can render
without a second round trip.

### Frontend — `RaidZonePage` + new `BossRosterEditor`

The boss sidebar gains an **Edit roster** toggle visible only to editors
(reusing the existing `canEdit` check from the triggers UI).

When editing is on:
- Each boss row becomes a `@dnd-kit/sortable` item with a drag handle + an edit
  button + a delete button.
- Drag reorder commits via `PUT /encounters/reorder` on drop (optimistic
  reorder; revert + toast on failure).
- "Edit" opens a small inline panel for: rename primary, edit stage, edit wiki
  url, and the **mobs sub-section** (list of mob rows; add sibling; rename mob;
  promote sibling → primary; remove mob with the safety rules above).
- An "Add boss" action at the bottom of the list opens the same panel pre-set
  to a new encounter (primary mob required).
- Delete shows a confirm dialog warning that linked triggers, spell timers, and
  the strategy markdown for that boss will be removed.

Non-editors see the existing read-only sidebar — unchanged. The boss detail
pane (triggers / timers / strategy) is unaffected.

### Source-file decommission

- Delete `scripts/dev/eq2_raid_bosses.review.txt`.
- Remove the `--curated-bosses` argument + `parse_curated_bosses` +
  `_load_curated_bosses_into_db` from `scripts/build_zones_db.py`. The script
  still rebuilds the `zones` / `zone_aliases` / `zone_types` tables from the
  cleaned-zones JSON; only the boss-loading branch goes.
- Update `CLAUDE.md`: the "Manual upload: zones.db" section no longer mentions
  the curated file; note that bosses are web-editable, and the rebuild path is
  for zone metadata only.

### Live-data backup — `litestream.yml`

Add `zones.db` to the existing replication config alongside `users.db` and
`parses.db`. Reuses the same `R2_*` env vars. Restore-on-start gets a third
`litestream restore` line in the Railway start command. zones.db is small;
overhead is negligible. Curator edits become R2-backed in near-real-time, and a
wiped volume restores correctly.

## Edge cases

| Situation | Behaviour |
|---|---|
| User reorders a boss that has triggers/timers attached | Safe — those FK to `encounter_id` (numeric), unaffected by `position` change. URL `/raids/{zone}/{new_position}` works; old position 404s — accepted. |
| User renames the primary mob | `zone_encounters.encounter_name` + the position-0 row in `zone_encounter_mobs` + `raid_encounters.mob_name` all updated in one transaction. |
| User promotes a sibling to primary | Positions 0 ↔ N swapped in `zone_encounter_mobs`; `encounter_name` updated to the new primary; `raid_encounters.mob_name` mirrored. |
| User tries to delete the only mob in an encounter | Rejected (422 with a clear message). At least one mob is required; to remove the encounter entirely, delete the encounter. |
| User tries to delete the primary while siblings exist | Rejected — they must promote a sibling first (the new primary inherits `encounter_name`). |
| User deletes an encounter that has triggers / spell timers / strategy | Allowed; the cascade on `raid_encounters` (via the FK) removes all children. The front-end shows a confirm dialog naming what's about to be removed. |
| User adds a new boss | New `zone_encounters` row at the next position + a single `zone_encounter_mobs` row at position 0. `raid_encounters` is lazy-created later when the first trigger/strategy is added (existing behaviour). |
| Reorder request with a malformed permutation (missing or extra id) | 400 with a clear validation message; nothing written. |
| Two editors reorder concurrently | The last write wins on `position` values; this is acceptable curator workflow. (Locks / OCC would be over-engineering at the current contributor count.) |

## Out of scope

- Any change to **triggers / spell timers / strategy** behaviour or storage —
  unchanged.
- **Per-server scoping** of raids data — stays global, as today.
- **Permalink-by-id** URLs (e.g. `/raids/{zone}/by-id/{encounter_id}`) to
  survive reorders for external bookmarks — defer until someone asks for it.
- **The future "any mob in encounter" leaderboard** — explicitly out of scope
  here, but supported with zero schema work: this design already puts every
  mob into `zone_encounter_mobs` with an index on `mob_name_lower`, so the
  future rankings query is purely a `JOIN`/`WHERE` rewrite.
- **Wiki re-scrape** to refresh rosters from the EQ2i wiki — would be a new
  importer, not affected by this work.

## Operational

- **No schema migration.** Same tables, same columns. Two `CREATE INDEX
  IF NOT EXISTS` declarations in `_SCHEMA` (idempotent, run by `init_db`) and
  the one-time data normalization below — that's it.
- The encounter_name normalization runs idempotently on every boot; first boot
  after deploy converts existing comma-joined names; subsequent boots are a
  no-op.
- The `litestream.yml` addition needs the existing R2 vars set (already are).
  First boot after deploy snapshots `zones.db` to R2.
- Web-editable means: **stop running `scripts/build_zones_db.py --curated-bosses`
  on the live volume.** With the source file gone and the flag removed, this is
  enforced — but call it out in the rollout step in case any local muscle memory
  exists.

## Testing

### `census/zones_db.py`
- Normalization migration: comma-joined `encounter_name` becomes the position-0
  mob's name; non-comma names are untouched; running twice is a no-op.
- `add_encounter` appends with the right position; with explicit `position`
  inserts at that slot and shifts later encounters when needed.
- `update_encounter` rename also touches `zone_encounter_mobs` position-0 row +
  cached `encounter_name`.
- `reorder_encounters` is atomic; rejects malformed permutations.
- `add_mob` with `make_primary` shifts the old primary to a sibling slot and
  updates `encounter_name`.
- `update_mob` rename of position-0 updates `encounter_name`; rename of a
  sibling doesn't.
- `promote_mob` swaps 0↔N and updates `encounter_name`.
- `delete_mob` refuses last-mob; refuses primary-while-siblings.
- `delete_encounter` cascades siblings (zones.db) and the `raid_encounters`
  mirror (raids.db) plus triggers/timers/strategies.

### Routes (`web/routes/zones.py`)
- Auth gate on every write (401/403 for non-editors).
- Each helper exposed end-to-end with realistic payloads (zone resolution, 404
  on unknown zone / encounter id ownership mismatch).
- Reorder validation (400 on malformed permutation).
- `raid_encounters` mirror stays in sync after each kind of write.

### Frontend
- `tsc` + build clean with `@dnd-kit/sortable` added.
- Sidebar drag-reorder commits with the expected payload.
- Edit panel: rename primary, add/rename/promote/remove sibling mobs, refuses
  delete-last-mob / delete-primary as 422 with the inline error.
- Add-boss flow.
- Non-editor view unchanged.

### Backup integration
- `litestream.yml` adds a third DB block; manual smoke: on a clean Railway
  redeploy, restore-on-start brings zones.db back from R2 (validated against
  the on-volume snapshot).

## Rollout

Additive, no manual migration. Deploy order:

1. **Code lands** with: the two `CREATE INDEX IF NOT EXISTS` declarations, the
   encounter_name normalization, all backend helpers + routes, the front-end
   editor, the curated-source decommission, and the `litestream.yml` zones.db
   block.
2. **First boot** creates the indexes (idempotent), normalizes any
   comma-joined `encounter_name`, and starts replicating zones.db to R2.
3. **Curators verify**: a sample boss rename, an add-then-delete, and a
   reorder. Triggers/timers/strategy on existing bosses survive renames and
   reorders (they FK by `encounter_id`).
4. No `--curated-bosses` flow remains in the repo — nothing to remember to
   avoid running.
