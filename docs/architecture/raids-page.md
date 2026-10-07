# Raids pages: where the data comes from

The raid-strategy area of the site has two pages:

- `/raids` — `frontend/src/pages/RaidZonesPage.tsx`, the landing page: one
  collapsible section per expansion, each holding raid-zone cards and (for
  contributors) a dungeon-curation card.
- `/raids/:name[/:bossName]` — `frontend/src/pages/RaidZonePage.tsx`, one zone:
  an encounter sidebar plus the selected encounter's strategy, ACT triggers and
  roster.

Shared response types (`EncounterMob`, `Encounter`, `Zone`, ...) live in
`frontend/src/pages/raids/types.ts`.

## The landing page is fully admin-curated

Nothing on `/raids` is derived from zone types or a hardcoded expansion list.
Three tables in the Postgres `zones` schema drive it:

| Table | Holds |
|---|---|
| `featured_raid_expansions` | which expansion sections appear, and their order |
| `featured_raid_categories` | named lanes inside an expansion section, and their order |
| `featured_raid_zones` | which raid zones appear in an expansion, their lane and position |

The read endpoints (`backend/server/api/zones_admin.py`):

- `GET /api/raids/expansions` — the expansion sections to render.
- `GET /api/raids/zones?expansion=X` — the featured zones in one section.
- `GET /api/raids/categories?expansion=X` — the named lanes in one section.

Admin writes: `POST`/`DELETE /api/raids/expansions/{short}`,
`POST`/`DELETE /api/raids/zones/{zone}`, `PUT /api/raids/zones/reorder`,
`POST`/`DELETE /api/raids/categories`, `PUT /api/raids/categories/reorder`.
The two pickers in `raids/ZonePickerModal.tsx` read
`GET /api/raids/expansions/available` (expansions in the zones catalogue not
yet featured) and `GET /api/raids/zones/available` (`raid_x4`/`raid_x2` zones
in an expansion not yet featured).

### Component split

`RaidZonesPage` owns only the expansion list, the open/closed state of each
section and the page-level "Add expansion" button. Each expansion is an
`<ExpansionSection>` (`pages/raids/ExpansionSection.tsx`) that runs its own
`useFetch` calls for its zones and categories. The split exists because the
number of expansions is dynamic: the parent cannot call a hook per expansion
without breaking the Rules of Hooks, but a child component per expansion can.

Inside a section:

- The uncategorised lane (category `NULL`) always comes first and has no
  header. Named lanes follow in their stored order.
- Each lane is a grid of zone cards. Admins see a drag handle on every card
  and on every named lane header.
- One `DndContext` wraps the whole section so a zone can be dragged between
  lanes. `handleDragEnd` branches on the id prefix: `cat:` is a lane reorder
  (`PUT /api/raids/categories/reorder`), `zone:` is a zone reorder or
  cross-lane move (`PUT /api/raids/zones/reorder` with the full lane order,
  positions renumbered `0..N-1`). Each lane is also a `useDroppable` target so
  an empty lane can accept a drop.
- Mutations refetch through the hook's `refetch`. Deleting an expansion calls
  `onExpansionRemoved` so the parent drops the whole section in one render.

Per-zone kill progress for the signed-in user's guild comes from
`GET /api/zones/progress` via `hooks/useRaidProgress.ts`, joined on zone name.
Any failure (including 401) yields an empty payload and the cards render
without progress.

## Dungeon curation

`pages/raids/DungeonsCard.tsx` is rendered per expansion but returns `null` for
anyone who is not a contributor or admin, so the public page is unaffected. It
lists zones in the expansion tagged with the `dungeon` zone type
(`GET /api/zones?expansion=X&type=dungeon`) and lets a contributor add or remove
that tag (`POST /api/zones/{zone}/types`, `DELETE /api/zones/{zone}/types/dungeon`).
Each tagged dungeon can expand to the same `<BossRosterEditor>` the raid page
uses.

There is no frontend coupling between this card and the rest of the site: the
backend's `_classify_zone` (in `backend/server/api/parses/list.py`) reads the
zone types, so a newly tagged dungeon shows up in the rankings filters and as
the "Dungeon" parses bucket on the next request. The tag mutation calls
`invalidate_zones_cache`, so neither cache serves a stale view.

## The zone page

`RaidZonePage` fetches `GET /api/zones/{name}` (zone metadata plus the boss
roster from `zone_encounters` / `zone_encounter_mobs`).

The `:bossName` URL segment is the URL-encoded `encounter_name`. The page
resolves it client-side against `zone.bosses`; with no segment it opens the
first encounter. The strategy API is keyed by encounter position
(`GET /api/zones/{zone}/encounters/{position}/strategy`), so the page keeps
`selected.position` for API calls while the URL carries the readable name.

Clicking an encounter in the sidebar calls `navigate()`, which only changes
`useParams`; the component stays mounted and the zone is not refetched.
Strategy text, revisions and the zone overview come from
`backend/server/api/raid_strategies.py` (the `raids` schema); roster edits go
through the `/api/zones/{zone}/encounters...` routes in `zones_admin.py`.
