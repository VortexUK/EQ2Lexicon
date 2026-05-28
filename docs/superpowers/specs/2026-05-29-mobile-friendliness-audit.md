# Mobile Friendliness — Audit & Remediation Spec

**Date:** 2026-05-29
**Status:** Audit complete; awaiting approval before plan stage.

## Problem

The web companion currently assumes desktop. On a phone (~390px) the page chrome and most data views are unusable: the nav overflows or crushes, several layouts collapse the main content area to near-zero behind a fixed sidebar, hover-only tooltips hide core data, and a couple of CSS grids clip content rather than scrolling.

## Goal

Make every page usable on phones (~390px) and small tablets (~640–768px) **without changing the desktop layout at all**. Pixel-perfect game-client recreations (item tooltips, AA tree, spell scrolls) keep their fixed dimensions and gain a horizontally-scrollable wrapper — they are intentionally not reflowed.

## Decisions (locked)

| Question | Choice |
|---|---|
| Target viewports | Phone (~390px) + small tablet (~640–768px). Three tiers including desktop. |
| Reflow vs. preserve | Reflow chrome, lists, forms, tables. **Game-client recreations stay fixed-size in an `overflow-x-auto` wrapper.** |
| Drive style | Audit doc first (this), then burn down P0/P1/P2 incrementally. |
| Desktop layout | **MUST NOT change.** Every responsive utility added uses mobile-first defaults so existing widths kick in at `md:`/`lg:` and the desktop render is byte-identical. |

## Foundations (already in place)

- Viewport meta tag present in `frontend/index.html`.
- Tailwind v4 mobile-first defaults: unprefixed utilities = phone, `sm:` ≥640px, `md:` ≥768px, `lg:` ≥1024px.
- CSS-first theme in `frontend/src/index.css` via `@theme`; no breakpoint overrides.
- No Tailwind Preflight — raw `<input>`/`<button>` already use explicit resets where needed.

No changes needed in any of the above.

## Breakpoint strategy

One uniform pattern for the whole codebase:

- **`sm:` (≥640px)** — Two-column grids where items need ≥240px. Pill filter rows that should ride one line.
- **`md:` (≥768px)** — **Primary layout breakpoint.** All sidebar-vs-main splits. Column-count increases from 1→2 for data-dense views. The dividing line where the page stops being phone-shaped and starts being tablet-shaped.
- **`lg:` (≥1024px)** — Three-column grids. The hamburger nav swap-back to inline nav (gives tablets a hamburger too, since 8 nav items don't fit at 768px alongside the right-side widgets).

Copy-paste patterns the implementer follows verbatim:

```tsx
// 1. Sidebar layout (CharacterPage, SpellsTab, AAsTab, HomePage, etc.):
<div className="flex flex-col md:flex-row gap-6 items-start">
  <div className="w-full md:w-[240px] md:shrink-0">{/* sidebar */}</div>
  <div className="flex-1 min-w-0">{/* main */}</div>
</div>

// 2. Pixel-exact content (ParsePage combatant grid, AA tree on narrow):
<div className="overflow-x-auto">
  <div className="min-w-[640px]">{/* fixed-size content */}</div>
</div>

// 3. Mixed inline + responsive grid columns (RecipesPage):
className="grid gap-5 grid-cols-1 md:[grid-template-columns:1fr_340px]"

// 4. Tables — wrappers must be overflow-x-auto, never overflow-hidden:
<Card className="p-0 overflow-x-auto">

// 5. Fixed-width inputs that should be full-width on mobile:
className="w-full md:w-[260px]"

// 6. dnd-kit touch sensor (BossRosterEditor):
import { TouchSensor } from '@dnd-kit/core'
const sensors = useSensors(
  useSensor(PointerSensor),
  useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 5 } }),
  useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
)

// 7. Hover tooltip → also touch (add alongside, don't replace):
onMouseEnter={e => showTip(id, e)}
onClick={e => showTip(id, e)}
```

## Architecture — what changes shape

Two cross-cutting changes that touch multiple pages, applied once:

- **Hamburger nav.** Below `lg:` (1024px), the header's inline `<nav>` is hidden and replaced by a `<HamburgerMenu>` button that opens a full-width overlay containing the same nav links stacked vertically. The ACT-plugin download icon drops out of the header below `lg:` (it migrates onto the home page). `ServerBadge`, `NotificationBell`, and `UserWidget` stay in the header at all sizes (compact, tap-friendly).
- **Tap-trigger tooltips.** Every place that opens a tooltip on `onMouseEnter` gets a parallel `onClick` so touch users can open the same tooltip. The tooltip component itself doesn't change — its viewport-clamping math already handles narrow viewports.

Everything else is per-page utility-class adjustments.

## Per-page findings

### Chrome — `App.tsx`

| Line | Issue | Treatment |
|---|---|---|
| 254 | Header's three flex groups (logo+ServerBadge, 8-item nav, widget cluster) compete for one non-wrapping row. Nav has `whiteSpace: 'nowrap'`. Unusable at 390px. | Below `lg:` hide nav + ACT image; add hamburger that toggles a full-width dropdown overlay with the same links stacked. |
| 156 | `<MyCharacters>` flex row: `w-[210px] shrink-0` sidebar + card grid, no wrap. | (HomePage — see below.) |

### `pages/AdminPage.tsx`

| Line | Issue | Treatment |
|---|---|---|
| 596 | `min-w-[18rem]` on approve/reject note `<td>` — forces 288px column. | Change to `w-full`; textarea fills the row naturally. |
| Various | Tables already `overflow-x-auto`. | Acceptable — admin on mobile is uncommon. |

### `pages/CharacterAAsTab.tsx` — **P0**

| Line | Issue | Treatment |
|---|---|---|
| 256–259 | `w-[240px] shrink-0` sidebar + `flex gap-6 items-start`. Right column collapses to ~0px on 390px. | `flex flex-col md:flex-row` + `w-full md:w-[240px]`. |
| 380 | `<div className="w-[60%]">` around `<AATree>`. | Wrap tree in `overflow-x-auto` with `min-w-[420px]` floor; drop the `w-[60%]` on mobile. |

### `pages/CharacterPage.tsx` — **P0**

| Line | Issue | Treatment |
|---|---|---|
| 651 | `w-[260px] shrink-0` stats sidebar + `flex gap-6`. Paperdoll collapses. | `flex flex-col md:flex-row` + `w-full md:w-[260px]`. |
| 661, 677 | Paperdoll `grid-cols-2`. | `grid-cols-1 md:grid-cols-2` — one column of slots on mobile. |
| 726 | `GeneralBanner` 6-column flex row, all `whitespace-nowrap`. | Add `flex-wrap`; each stat column `min-w-[100px]`. |
| 609, 693 | `<ItemTooltip>` hover-only. | Add `onClick` tap trigger alongside `onMouseEnter`. |
| 623 | Tab bar tight with "Alternate Advancements" label. | Add `flex-wrap`; acceptable for v1. |

### `pages/CharacterSpellsTab.tsx` — **P0**

| Line | Issue | Treatment |
|---|---|---|
| 422, 425 | Same sidebar collapse. | Same `flex flex-col md:flex-row` fix. |
| 509 | Two `<table>` side by side with `whitespace-nowrap` cells, no wrap on container. | Stack to one column at mobile: render the two halves vertically below `md:`. |
| 496 | `w-[260px]` search input. | `w-full md:w-[260px]`. |
| 139 | Inline tooltip `absolute left-full top-0 ml-2 w-[220px]` — not viewport-clamped. | Convert to `position: fixed` with the same clamp helper used elsewhere. |
| `SpellTierPip` | Hover-only `SpellScrollTooltip` trigger. | Add `onClick` tap trigger. |

### `pages/ClaimPage.tsx`

No issues. `max-w-[560px] mx-auto my-12 px-4` is naturally responsive.

### `pages/GuildPage.tsx` — **P1**

| Line | Issue | Treatment |
|---|---|---|
| 1260 | Tables already in `overflow-x-auto` Card. | Accept horizontal scroll on mobile. |
| 409, 597 | Spell + adorn cell tooltips hover-only. Core "what's missing at this tier" data unreachable on touch. | Add `onClick` tap trigger on `<td>`. |
| 908, 919 | Watch form inputs `w-[160px]` / `w-[240px]` in `flex-wrap` row. | Already wraps; no change needed. |

### `pages/HomePage.tsx` — **P1**

| Line | Issue | Treatment |
|---|---|---|
| 253 | `flex gap-8 items-start` + `w-[210px] shrink-0` sidebar. Cards overflow. | `flex flex-col-reverse md:flex-row` (cards above sidebar at mobile); sidebar `w-full md:w-[210px]`. |

### `pages/ItemPage.tsx`

No issues.

### `pages/ItemSearchPage.tsx` — **P1**

| Line | Issue | Treatment |
|---|---|---|
| 726–727 | Results table already `overflow-x-auto`. | Accept scroll; optionally hide `Classes` + `iLvl` columns below `md:` via `hidden md:table-cell`. |
| 440 | Filter row already `flex flex-wrap`. | No change. |

### `pages/NotFoundPage.tsx`

No issues.

### `pages/ParsePage.tsx` — **P0** (highest priority single fix)

| Line | Issue | Treatment |
|---|---|---|
| 333–338 | `CombatantSection` CSS grid with 9 columns, hard pixel widths summing to ~640px+, **no `overflow-x-auto`**. Content clips. | Wrap card in `overflow-x-auto`; set `min-w-[640px]` on the grid. |
| 379, 791 | `grid-cols-subgrid` rows inherit the parent. | Fixed by the wrapper above. |

### `pages/ParsesPage.tsx`

| Line | Issue | Treatment |
|---|---|---|
| Various | Grouped card layout. Need spot-check of `ParseCard` width but likely fine. | Confirm during implementation. |

### `pages/RaidZonePage.tsx`

Already responsive: `grid-cols-1 md:grid-cols-[16rem_1fr]`. No changes needed. `ZoneOverview` + `EncounterStrategy` already wrap `<pre>` / `<table>` in `overflow-x-auto`.

### `pages/RaidZonesPage.tsx`

Already responsive: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`. No changes.

### `pages/RankingsPage.tsx` — **P1**

| Line | Issue | Treatment |
|---|---|---|
| 188 | `<Card className="p-0 overflow-hidden">` clips the 9-column table instead of scrolling. | Change `overflow-hidden` → `overflow-x-auto`. |
| 190–230 | 9-column table. | Optionally hide `Size` + `Date` below `md:` via `hidden md:table-cell`. |

### `pages/RecipesPage.tsx` — **P0**

| Line | Issue | Treatment |
|---|---|---|
| 387 | Inline `gridTemplateColumns: '1fr 340px'`. Main column collapses to ~30px on 390px. | `grid-cols-1 md:[grid-template-columns:1fr_340px]`. Cart stacks below results on mobile. |
| 392 | 5-column filter grid `grid-cols-[1fr_1fr_1fr_1fr_auto]`. | Replace with `flex flex-wrap gap-[0.6rem]`. |
| 695 | Recipe-detail `grid-cols-2` ingredient layout. | Keep — once outer grid stacks, this has enough room. |

### `pages/RolesSettingsPage.tsx` — **P1**

| Line | Issue | Treatment |
|---|---|---|
| 304 | `<Card overflow-hidden>` clips role-request history table. | Change to `overflow-x-auto`. |

### `pages/SearchPage.tsx` — **P2**

| Line | Issue | Treatment |
|---|---|---|
| 99 | `my-16` large vertical margin wastes mobile above-fold space. | `my-8 md:my-16`. |

### `pages/TokensPage.tsx`

Spot-check token action buttons for touch-target size during implementation. No structural issues spotted.

## Cross-cutting components

### Hover-only tooltips (all need a tap trigger added alongside `onMouseEnter`)

1. `ItemTooltip` in `CharacterPage` — gear slot data hidden on touch.
2. `SpellScrollTooltip` via `SpellTierPip` in `CharacterSpellsTab`.
3. `AANodeTooltip` in `AATree` — AA node descriptions hidden on touch.
4. Spell-name tooltip in `GuildPage` `SpellCheckTable`.
5. Adorn missing-slots tooltip in `GuildPage` `AdornCheckTable`.

### Drag-reorder — `BossRosterEditor.tsx`

Sensors: `PointerSensor` + `KeyboardSensor` only. On iOS Safari, `PointerSensor` can lose drags to page-scroll. Add `TouchSensor` with `activationConstraint: { delay: 250, tolerance: 5 }` so a long-press is unambiguously a drag.

### `UserWidget.tsx` dropdown — **P2**

`absolute left-0` on a dropdown anchored to a button near the right edge of the header may overflow the right viewport edge at narrow widths. Switch to `absolute right-0`.

### Game-client recreations (no reflow — wrap with overflow)

- `ItemTooltip` (TIP_W = 360px) — portal, viewport-clamped; works as-is.
- `SpellScrollTooltip` (TIP_W = 360px) — portal, viewport-clamped; works as-is.
- `AATree` (native 640×480 coord space, percentage-based) — needs `overflow-x-auto` wrapper with `min-w-[420px]` so it doesn't squash in the narrow main column.

## Priority bands (the burn-down list)

### P0 — broken or unusable (six items)
1. `App.tsx` — header nav at <1024px (hamburger needed).
2. `ParsePage.tsx` line 333 — combatant grid clips, no overflow wrapper.
3. `RecipesPage.tsx` line 387 — main content collapses behind 340px cart.
4. `CharacterPage.tsx` line 651 — paperdoll sidebar collapses main column.
5. `CharacterSpellsTab.tsx` line 425 — same sidebar pattern.
6. `CharacterAAsTab.tsx` line 259 — same; AA tree renders in zero-width container.

### P1 — ugly but usable (eight items)
7. `RankingsPage` table — `overflow-hidden` clips, needs `overflow-x-auto`.
8. `RolesSettingsPage` table — same.
9. `GuildPage` — hover-only tooltips on missing-spell/adorn cells.
10. `HomePage` — `MyCharacters` sidebar pushes cards off-screen.
11. `CharacterPage` `GeneralBanner` — 6-column nowrap row overflows.
12. All hover tooltips need `onClick` tap trigger.
13. `AdminPage` — tables scrollable; acceptable for admin use.
14. `BossRosterEditor` — add `TouchSensor`.

### P2 — polish (six items)
15. `CharacterSpellsTab` search input `w-[260px]` → `w-full md:w-[260px]`.
16. `CharacterSpellsTab` inline tooltip — convert to viewport-clamped portal.
17. `SearchPage` `my-16` → `my-8 md:my-16`.
18. `UserWidget` dropdown `left-0` → `right-0`.
19. `CharacterPage` tab bar `flex-wrap`.
20. Footer link tap-target sizing.

## Out of scope (explicitly)

- No new mobile-only pages or features.
- No native app, no PWA, no service worker.
- No desktop layout changes.
- No restyling of pixel-perfect game-client tooltips (only their container).
- No bottom nav bar / iOS-style chrome.

## How we'll verify

For each page touched:

1. Visually inspect at **390px** and **768px** widths (Chrome DevTools device emulation — iPhone 14 and iPad Mini portrait). User does the final visual sign-off.
2. Confirm the desktop layout at ≥1280px is byte-identical to before — no visual diff. `npm run build` succeeds; no Tailwind class warnings.
3. Touch interactions verified by the user on a real device for: BossRosterEditor drag-reorder, all five tooltip tap-triggers, the hamburger nav overlay.

## Rollout

Each priority band is its own commit (or 1–2 commits per band if it grows). Each commit is small, scoped, and shippable independently — no big-bang merge. Once P0 is on main, the site is usable on mobile; P1 and P2 can land at any cadence after.

## Self-review

- **Placeholders:** none — every per-page row has a concrete treatment.
- **Internal consistency:** sidebar treatment, table treatment, tooltip treatment are uniform across all affected pages (per the "copy-paste patterns" section).
- **Scope check:** decomposes naturally into three execution batches (P0 → P1 → P2). Each batch is a single implementation plan.
- **Ambiguity:** "wrap below `lg:`" for the hamburger nav is concrete; "stack vertically at `md:`" for sidebar layouts is concrete; "tap trigger alongside hover" is concrete. No vague "make it responsive" lines.
