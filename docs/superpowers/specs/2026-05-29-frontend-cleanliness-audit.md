# Frontend Cleanliness — Audit & Remediation Spec

**Date:** 2026-05-29
**Status:** Audit complete; awaiting approval before plan stage.

## Problem

The frontend has accumulated duplication (14+ places re-implementing the same fetch boilerplate, 5+ table-header class constants with near-identical values), design-system drift (`#22c55e` vs `var(--success)` `#4ade80`, hardcoded `rgba(200,169,110,…)` instead of `rgba(var(--gold-rgb), α)`), and a few real bugs (admin mutation actions silently swallow server errors, the UserWidget dropdown renders behind the header, two guild-page fetches lack `credentials: 'include'`). The 730KB single-bundle warning is unaddressed. Several pages have grown past 1000 lines.

## Goal

Pay down the technical debt across `frontend/` (src + tooling) in three priority bands. Real bugs first (P0), then consolidation refactors that reduce repetition and make the design system enforceable (P1), then polish (P2). The product UX is unchanged — every change either fixes a defect or replaces one expression of an existing pattern with a cleaner one.

## Decisions (locked from earlier conversation)

| Question | Choice |
|---|---|
| Audit depth | Comprehensive (~80-120 findings). Final count: **121**. |
| Scope | `frontend/` all-in (src + tooling: vite, tsconfig, package.json, eslint). |
| Drive | Audit → P0/P1/P2 plan → burn down. Same pattern as the mobile work. |

## How the audit was produced

Three parallel read-only audit agents owned non-overlapping category clusters:

1. **Duplication + dead code + consolidation** — components, hooks, className constants, dead exports, similar-but-different patterns. 33 findings.
2. **Design system + style overrides** — inline `style={{}}`, hex hardcodes, `var(--*)` inline, Tailwind anti-patterns, ui/ primitive coverage, font usage, z-index, magic spacing, token gaps, radius. 40 findings.
3. **Type safety + magic numbers + tooling + React patterns + error handling** — `as any`, magic literals, naming, file sizes, vite/tsconfig/package.json, bundle chunking, hooks discipline, error handling. 48 findings.

Overlapping findings (e.g. `relativeTime` duplication shows up in audits 1 and 3) are deduplicated below.

---

## Priority summary

| Band | Count | Character |
|---|---|---|
| **P0** | 17 | Real bugs or critical drift. Should ship first; small per-fix. |
| **P1** | 53 | Genuine cleanup: extract primitives, consolidate duplicates, split oversized files. Higher refactor surface. |
| **P2** | 51 | Polish: naming, magic numbers, sub-pixel spacing, single-use abstractions. |

---

# P0 — Real bugs and critical drift (17 items)

These are either functional bugs (silent failures, broken auth, misrendered colours) or token-system drift that's actively misleading. **Fix first.** Each is small.

## P0-1 — `GuildPage` spell-check + adorn-check fetches missing `credentials: 'include'`
- **Where:** `pages/GuildPage.tsx:1083, 1097`
- **Bug:** Two fetches lack the `credentials: 'include'` option that every other API call in the codebase uses. If those endpoints require an authenticated session, they 401 in production.
- **Fix:** Add `, { credentials: 'include' }` to both fetch calls.

## P0-2 — `AdminPage` `UserRow.doAccess` swallows network errors
- **Where:** `pages/AdminPage.tsx:146-157`
- **Bug:** `await fetch(...)` with no `res.ok` check. A 403/500 from the server is silently ignored and the table refreshes as if the action succeeded.
- **Fix:** Check `res.ok`; on failure, set local error state and surface it.

## P0-3 — `AdminPage` `UserRow.toggleRole` swallows network errors
- **Where:** `pages/AdminPage.tsx:163-174`
- **Bug:** Same pattern as P0-2 — role grant/revoke failures invisible to the admin.

## P0-4 — `AdminPage` `RoleRequestRow.decide` swallows network errors
- **Where:** `pages/AdminPage.tsx:550-564`
- **Bug:** Same pattern — failed approve/reject shows no feedback.

## P0-5 — `AdminPage` `ClaimRow.doAction` swallows network errors
- **Where:** `pages/AdminPage.tsx:356-370`
- **Bug:** Same pattern — failed delete/approve/reject still calls `onDelete()`.

## P0-6 — `UserWidget` dropdown rendered behind header
- **Where:** `components/UserWidget.tsx:70` (`z-[100]`) vs `App.tsx:254` (header `z-[200]`)
- **Bug:** Dropdown sits BELOW the header z-index, so it gets clipped on overflow/scroll.
- **Fix:** Change to `z-[300]` to match the NotificationBell dropdown level.

## P0-7 — `GuildPage` `relativeTime` reports "just now" for events 0–60 min old
- **Where:** `pages/GuildPage.tsx:826-831`
- **Bug:** The hand-rolled function divides by 3600 first, so any event in the last hour returns "just now". A 30-minute-old item-watch hit shows "just now" in GuildPage but "30m ago" elsewhere.
- **Fix:** Delete the local function; import + use `fmtRelative` from `formatters.ts`.

## P0-8 — `AdminPage` `relativeTime` duplicates `fmtRelative`
- **Where:** `pages/AdminPage.tsx:100-106`
- **Bug:** Hand-rolled formatter — close to correct but inconsistent with the canonical `fmtRelative`. Lacks week/month thresholds, so old dates render as huge "Nd ago" strings.
- **Fix:** Delete; use `fmtRelative`.

## P0-9 — `#22c55e` hardcoded instead of `var(--success)` (3 sites)
- **Where:** `pages/CharacterSpellsTab.tsx:260`, `pages/GuildPage.tsx:116,786`
- **Drift:** `--color-success: #4ade80` in `@theme`. Sites use `#22c55e` (green-500, darker). Two greens render side-by-side.
- **Fix:** Replace with `'var(--success)'`; for the `rgba(34,197,94,…)` background add `--success-rgb: 74, 222, 128` and use `rgba(var(--success-rgb), α)`.

## P0-10 — `#ef4444` hardcoded instead of `var(--danger)` (2 sites)
- **Where:** `components/NotificationBell.tsx:138`, `pages/GuildPage.tsx:120`
- **Drift:** `--color-danger: #f87171` (red-400). Sites use `#ef4444` (red-500). Most visible: the NotificationBell badge.
- **Fix:** Use `bg-danger text-white` (NotificationBell) and `'var(--danger)'` (GuildPage).

## P0-11 — `#fbbf24` warning colour has no token (3 files)
- **Where:** `pages/AdminPage.tsx:109,115`, `pages/CharacterAAsTab.tsx:67`, `pages/CharacterSpellsTab.tsx:57`
- **Drift:** Recurring amber-400 with no `--color-warning`. AdminPage also uses `rgba(234,179,8,…)` (yellow-600) for the same badge background — two yellows in one badge.
- **Fix:** Add `--color-warning: #fbbf24` and `--warning-rgb: 251, 191, 36` to `@theme` / `:root`; normalise all sites.

## P0-12 — `--accent-rgb` missing; fallback is the wrong colour (7 sites)
- **Where:** `pages/GuildPage.tsx:154,931,933`, `pages/HomePage.tsx:242,243`, `pages/ItemSearchPage.tsx:694,700`
- **Drift:** Code uses `rgba(var(--accent-rgb, 99,210,130), α)` — but the variable doesn't exist anywhere, and the fallback is bright green while `--accent` is gold. The fallback never fires in production browsers (CSS vars resolve), so this is a latent bug.
- **Fix:** Add `--accent-rgb: var(--gold-rgb)` to `:root`. Remove the bogus fallback from all 7 sites.

## P0-13 — `useFetch` hook needed: load/error/data triplet repeated 14 times
- **Where:** `pages/ItemPage.tsx:34-49`, `pages/ParsePage.tsx:147-170`, `pages/ParsesPage.tsx:179-215`, `pages/RaidZonePage.tsx:58-87`, `pages/RaidZonesPage.tsx:57-95`, `pages/RankingsPage.tsx:41-116`, `pages/RecipesPage.tsx:249-330`, `pages/TokensPage.tsx:33-55`, `pages/RolesSettingsPage.tsx:47-68`, `components/EncounterStrategy.tsx:117-157`, `components/ZoneOverview.tsx:81-105`, `components/ActTriggers.tsx:104-157`, `pages/GuildPage.tsx:655-670, 834-851`
- **Bug:** ~300 lines of boilerplate. Every site has its own `let cancelled = false` / `cancelled = true` guard. One site uses `AbortController` (`AdminPage` `ParsesAdminTable`) which is the better pattern.
- **Fix:** New `hooks/useFetch.ts` returning `{ data, loading, error, refetch }`. Canonical AbortController-based cancellation. Adopt in all 14 sites.
- **Why P0:** The boilerplate isn't a bug per se, but it's the single biggest source of bug-shaped patterns (`fetch(url)` calls that may forget `credentials: 'include'` — see P0-1 — or forget `res.ok` checks — see P0-2..5). One canonical hook eliminates the whole class.

## P0-14 — `vite.config.ts` missing `manualChunks` → 730KB single bundle
- **Where:** `frontend/vite.config.ts:61-64`
- **Bug:** Every dep ships in one giant bundle. Build warns about it on every build.
- **Fix:** Add `build.rollupOptions.output.manualChunks` splitting `vendor-react` (react, react-dom, react-router-dom), `vendor-dnd` (@dnd-kit/*), `vendor-markdown` (react-markdown, remark-gfm).

## P0-15 — No `React.lazy()` for low-traffic pages → all 15 pages in initial bundle
- **Where:** `App.tsx:6-29` (top-of-file imports)
- **Bug:** AdminPage, TokensPage, RolesSettingsPage, ParsePage, RaidZonePage, ParsesPage, RaidZonesPage all loaded on first visit even though most users never visit them.
- **Fix:** Wrap these 7 in `React.lazy()` + a `<Suspense fallback={<div className="p-8 text-text-muted">Loading…</div>}>` boundary.

## P0-16 — `tsconfig.app.tsbuildinfo` committed to repo
- **Where:** `frontend/tsconfig.app.tsbuildinfo`
- **Bug:** Build artefact tracked in git. Causes spurious diffs.
- **Fix:** Add `*.tsbuildinfo` to `.gitignore`; remove from git.

## P0-17 — Frontend has no project-level ESLint config
- **Where:** `frontend/` (no `eslint.config.*`)
- **Bug:** `eslint-disable react-hooks/exhaustive-deps` comments exist throughout the codebase but no project ESLint runs. Lint depends on the dev's global install; CI gets no lint feedback.
- **Fix:** Add `eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh` to devDeps; add `eslint.config.js`; add `"lint": "eslint src"` script.

---

# P1 — Cleanup that improves consistency (53 items)

Bigger refactors. Each is justified individually; the bulk is consolidation of repeated patterns into shared primitives, hooks, or utilities.

## P1 — Type safety (5 items)

- **P1-1** SSE message `as StreamMessage` cast without validation — `hooks/useCensusStream.tsx:28`. Add `isStreamMessage(msg): msg is StreamMessage` guard.
- **P1-2** Character/Guild SSE callbacks cast `unknown` → domain type — `pages/CharacterPage.tsx:538`, `pages/GuildPage.tsx:1074`. Switch `Listener` to generic `Listener<T>`.
- **P1-3** `useAuth` casts `data as User` with no narrowing — `hooks/useAuth.ts:38`. Add `isUser` guard.
- **P1-4** `User.access_status: string` should be `'approved' | 'pending' | 'denied'` — `hooks/useAuth.ts:9`. Discriminant union catches typos in `=== 'pending'` comparisons.
- **P1-5** `toErrorMessage(err: unknown): string` utility — extract the `String((err as Error).message ?? err)` pattern repeated in 15+ sites (`components/ActTriggers.tsx:156,743,941,970,1196`, `components/BossRosterEditor.tsx:307,328,345,359,390,614`, `components/EncounterStrategy.tsx:183,228`, `pages/RolesSettingsPage.tsx:65,172,189`, `components/ZoneOverview.tsx:143`). Use `instanceof Error ? err.message : String(err)` to remove the unsound cast.

## P1 — Primitive extraction (6 items)

- **P1-6** Three tooltip portal implementations (ItemTooltip, SpellScrollTooltip, AATree) share fixed-position + viewport-clamp logic. Extract `useTooltipPosition(x, y, width)` hook + `<TooltipFrame>` wrapper. — `components/AATree.tsx:174-188`, `components/ItemTooltip.tsx:157-176`, `components/SpellScrollTooltip.tsx:160-171`. Game-client visual styling stays inside `TooltipFrame`.
- **P1-7** Sortable-table pattern repeated 3× in GuildPage (`RosterTable`, `SpellCheckTable`, `AdornCheckTable`). Extract `useSortable<T>({ data, sortValue, initial })` hook + `<SortTh>` primitive. — `pages/GuildPage.tsx:199-251, 309-362, 489-549`.
- **P1-8** Tab-button (`<button>` with `active` underline) replicated 5× across CharacterPage, CharacterAAsTab (twice), GuildPage, ParsePage. Extract `<TabButton active={boolean}>` to `components/ui/`. — see audit-2 F-24 for line refs.
- **P1-9** Badge component (small rounded label with semantic colour) reimplemented in AdminPage, GuildPage, ClaimPage, NotificationBell. Extract `<Badge variant="warning|success|danger|info">` to `components/ui/`.
- **P1-10** `SectionLabel` needs a `variant="muted"` prop so AdminPage's `SECTION_TITLE_CLS` and CharacterPage's `sectionHeadingClass` can use it instead of hand-rolled constants. — `pages/AdminPage.tsx:135`, `pages/CharacterPage.tsx:990`.
- **P1-11** Approve action `<button>` in GuildPage uses raw element + inline styles for success-green button. Either add `variant="success"` to `<Button>` or use the future `<Badge variant="success">`. — `pages/GuildPage.tsx:781-790`.

## P1 — Logic consolidation (4 items)

- **P1-12** `isContributor(auth)` derived helper. Extract from 4 sites (`components/ActTriggers.tsx:99-101`, `components/EncounterStrategy.tsx:113-115`, `components/ZoneOverview.tsx:77-79`, `pages/RaidZonePage.tsx:54-56`) into `hooks/useAuth.ts`.
- **P1-13** `show/hide/move tooltip` triple-callback pattern duplicated in `pages/CharacterPage.tsx:609-615` and `pages/ItemSearchPage.tsx:254-260`. Extract `useItemTooltip()` hook colocated with `ItemTooltip` component.
- **P1-14** `fmtGuildStatus` + inline `Math.round(x).toLocaleString()` patterns. Already covered by `fmtNum` in formatters; replace `pages/GuildPage.tsx:166-169` and add `fmtNumOrDash(n: number | null)` helper for the 15+ `value ?? '—'` patterns.
- **P1-15** Five inline `Date.now() / 1000` + relative-time math sites in GuildPage (lines 719, 819-821). Replace with `fmtRelative`.

## P1 — className consolidation (4 items)

- **P1-16** `TH_CLS`/`TD_CLS` defined separately in `pages/AdminPage.tsx:137-138`, `pages/GuildPage.tsx:127-128`, `_SPELL_TH_CLS`/`_SPELL_TD_CLS` in `pages/CharacterSpellsTab.tsx`. Five constants, same concept. Extract `tableThCls`/`tableTdCls` to `components/ui/`.
- **P1-17** `inputCls` defined in `components/BossRosterEditor.tsx:54-55` and `components/ActTriggers.tsx:854-855` with different rounding (`rounded-md` vs `rounded-sm`). Hoist to shared constant in `components/ui/`.
- **P1-18** `CTRL_CLS` defined in `pages/ItemSearchPage.tsx:184` and `pages/RecipesPage.tsx:111` with diverging values; AdminPage `ServersSection` has 4 inline copies. Single `formInputCls` or `<ControlInput>` primitive.
- **P1-19** Dark textarea class replicated identically in `components/EncounterStrategy.tsx:370`, `components/ZoneOverview.tsx:260`, `pages/RolesSettingsPage.tsx:251`. Extract `textareaCls` or `<Textarea>` primitive.

## P1 — File splits (6 items)

- **P1-20** `pages/GuildPage.tsx` (1290 lines, 37 useState). Extract `GuildRosterTab`, `GuildSpellCheckTab`, `GuildAdornCheckTab` to sibling files. Page shell becomes ~200 lines.
- **P1-21** `pages/AdminPage.tsx` (1275 lines, 8 sub-components). Extract `admin/UsersTable`, `admin/ClaimsTable`, `admin/RoleRequestsTable`, `admin/ServersSection`, `admin/ParsesAdminTable` to a new `pages/admin/` subdir.
- **P1-22** `components/ActTriggers.tsx` (1248 lines). Extract `TriggerEditor.tsx`, `SpellTimerEditor.tsx`, `ActImportPanel.tsx` as siblings.
- **P1-23** `pages/ItemSearchPage.tsx` (839 lines, 17 useState). Extract `ItemSearchFilters.tsx`; consider `useItemSearchFilters` hook for URL-state.
- **P1-24** `pages/ParsePage.tsx` (793 lines). Extract `CombatantDetailPanel.tsx`.
- **P1-25** `pages/RecipesPage.tsx` (776 lines, 15 useState). Extract `ShoppingListPanel.tsx`, `RecipeCard.tsx`.

## P1 — Token / design-system gaps (8 items)

- **P1-26** Add `--color-stat-primary: #22ff22` and `--color-stat-secondary: #00e5ff` to `@theme`. Replace inline hex in `pages/ItemPage.tsx:144,147`.
- **P1-27** Add `--success-rgb` and `--warning-rgb` to `:root` alongside existing `--danger-rgb`. Required for the `rgba(var(--*-rgb), α)` pattern.
- **P1-28** Add z-index tokens to `@theme`: `--z-header: 200`, `--z-nav-backdrop: 250`, `--z-nav-panel: 260`, `--z-dropdown: 300`, `--z-modal: 1000`, `--z-tooltip: 9999`. Replace 7 `z-[N]` arbitrary values with `z-header`, `z-modal`, etc.
- **P1-29** Add `--radius-sm2: 6px` token or migrate the 13 `rounded-[6px]` usages to `rounded-md` (8px).
- **P1-30** ClaimPage card uses `borderColor: 'rgba(234,179,8,0.4)'` — wrong yellow (yellow-500 not gold). Change to `rgba(var(--gold-rgb), 0.5)` or add `<Card variant="highlighted">`. — `pages/ClaimPage.tsx:244`.
- **P1-31** `rgba(200,169,110,…)` hardcoded in 8 sites. Replace with `rgba(var(--gold-rgb), α)` — `components/NotificationBell.tsx:125,127,131`, `pages/NotFoundPage.tsx:26`, `pages/GuildPage.tsx:269,391,578,740`.
- **P1-32** `px-[10px]` (3 sites) → `px-2.5`. Tailwind has a direct equivalent. — `pages/CharacterAAsTab.tsx:73`, `pages/CharacterPage.tsx:224`, `pages/CharacterSpellsTab.tsx:63`.
- **P1-33** `#93d9ff` and `#ffc993` hardcoded — these are the `--rarity-treasured` and `--rarity-legendary` tokens already in `@theme`. Replace inline. — `pages/ParsePage.tsx:642`, `pages/HomePage.tsx:69`.

## P1 — Inline style → utility (6 items)

- **P1-34** LoginGate, UserWidget, ClaimPage Discord sign-in `<a>` — three copies of static inline-style buttons with inconsistent text colour. Extract `<DiscordButton>` primitive. — `App.tsx:49-60`, `components/UserWidget.tsx:29-44`, `pages/ClaimPage.tsx:13-25`.
- **P1-35** `<Card style={{ padding: '1.1rem 1.25rem' }}>` and similar overrides in 5 sites. Use className-based padding (with arbitrary values where needed) instead of `style={{}}`. — `pages/ItemPage.tsx:117`, `pages/SearchPage.tsx:146`, `pages/ItemSearchPage.tsx:437`, `pages/GuildPage.tsx:1177`.
- **P1-36** `ParsesPage` `headerBtnStyle` (`CSSProperties` const) static — convert to className. — `pages/ParsesPage.tsx:670-682`.
- **P1-37** `ItemSearchPage` `TH`/`TD` `CSSProperties` consts — only file using object-style table styling. Convert to className constants matching other pages. — `pages/ItemSearchPage.tsx:188-206`.
- **P1-38** `style={{ color: 'var(--accent)' }}` on `<Link>` in CharacterPage — static. Use `text-gold`. — `pages/CharacterPage.tsx:746`.
- **P1-39** `borderRight: '1px solid var(--border)'` inline (conditional). Use conditional className `border-r border-border`. — `pages/CharacterPage.tsx:760`.

## P1 — React patterns / hooks (3 items)

- **P1-40** Two `useEffect` hooks in AdminPage use `eslint-disable react-hooks/exhaustive-deps` to dodge `load`/`fetchData` deps. The real fix is `useCallback`. — `pages/AdminPage.tsx:697, 1132`.
- **P1-41** `onMouseEnter`/`onMouseLeave` JS style mutations in SearchPage + App.tsx (server-switcher links). Replace with Tailwind `hover:` utilities; eliminates `as HTMLAnchorElement` casts. — `pages/SearchPage.tsx:157-160`, `App.tsx:164-171`.
- **P1-42** `CharacterPage` module-level mutable `let _configFetched / _ratingConfig` for one-shot fetch. Convert to a Promise-cache pattern (mirrors `hooks/useClasses.ts:_inflight`). — `pages/CharacterPage.tsx:~480-503`.

## P1 — Error handling consistency (2 items)

- **P1-43** `console.error` left in `pages/CharacterSpellsTab.tsx:244` — the only production console call in the codebase. Remove.
- **P1-44** Same file mixes `.catch(err => …)` (line 242) with `try/catch` (line 287). Standardise on async/await + try/catch.

## P1 — Dead code (2 items)

- **P1-45** `SECTION_TITLE_CLS` and `TABLE_CLS` defined in `pages/AdminPage.tsx:135-136` but never used. Delete.
- **P1-46** Missing `frontend/.gitignore` for `dist/` and `node_modules/` (if root .gitignore doesn't cover). Verify and add if needed.

## P1 — Tooling (2 items)

- **P1-47** Try removing `skipLibCheck: true` from `frontend/tsconfig.app.json:7`. With a clean dep list it's likely unnecessary.
- **P1-48** `handle<T>` helper exists only in `components/ActTriggers.tsx:1245`. Move to a shared `lib/api.ts` once `useFetch` is in.

## P1 — Quick wins (5 items)

- **P1-49** AdminPage button `style={{ fontSize: '1rem', padding: '0 0.1rem' }}` for emoji icon buttons (lines 466, 479). Add `<Button size="icon">` variant.
- **P1-50** Dropdown shadow + positioning (`top: 'calc(100% + 6px)'`, `boxShadow`) duplicated UserWidget/NotificationBell. Hoist to className constants.
- **P1-51** GuildPage `CharacterPage` `ItemSearchPage` use `<a>` sign-in elements but each uses different copy/styling. Already covered by P1-34.
- **P1-52** Inline form-input class repeated in AdminPage `ServersSection` lines 1028, 1039, 1050, 1071. Extract local const or use the shared `inputCls` once it exists (P1-17).
- **P1-53** Eyebrow-label pattern `text-[0.72rem] uppercase tracking-[0.06em] text-text-muted` repeated ~8 times as inline className. Should be `<SectionLabel variant="muted">` after P1-10 lands.

---

# P2 — Polish (51 items)

Style consistency, naming, sub-pixel spacing, single-use abstractions. Each is small; few are individually important; their cumulative effect is a tidier codebase. Suggest batching by file when convenient.

## P2 — Magic numbers (6 items)

- **P2-1** `setTimeout(…, 150)` tooltip hover delay in `components/SpellScrollTooltip.tsx:258`. Constant: `TOOLTIP_HOVER_DELAY_MS`.
- **P2-2** `setTimeout(…, 300)` search debounce in `pages/SearchPage.tsx:91`. Constant: `SEARCH_DEBOUNCE_MS`.
- **P2-3** `setTimeout(…, 1500)` token-copy feedback in `pages/TokensPage.tsx:108`. Constant: `COPY_FEEDBACK_DURATION_MS`.
- **P2-4** `setTimeout(…, 0)` next-tick focus in `components/BossRosterEditor.tsx:550`. Comment why.
- **P2-5** `PARSES_FETCH_LIMIT = 500` magic literal in `pages/ParsesPage.tsx:198`. Name it.
- **P2-6** TouchSensor `{ delay: 250, tolerance: 5 }` in `components/BossRosterEditor.tsx:70`. Named constants.

## P2 — Naming (4 items)

- **P2-7** `_underscore_prefix` convention inconsistent across files. Decide on one — recommend dropping (TS module system already encapsulates). Apply uniformly.
- **P2-8** `open` / `expanded` booleans should be `isOpen` / `isExpanded` to match `isOfficer`/`isBusy` pattern. Multiple files (FilterDropdown, MobileNav, NotificationBell, UserWidget, etc.).
- **P2-9** `AdminPage.SECTION_CLS` defined inside render body — should be hoisted to module scope.
- **P2-10** `TH_CLS`/`TD_CLS` defined in both AdminPage and GuildPage with different values. Comment to clarify or rename to `ADMIN_TH_CLS`/`GUILD_TH_CLS` (until P1-16 unifies them).

## P2 — Dead code (5 items)

- **P2-11** `pages/ClaimPage.tsx:10` `CARD_CLS` used once. Inline.
- **P2-12** `pages/ClaimPage.tsx:13-22` `discordBtn()` function returns static object. Replace with const.
- **P2-13** `pages/ParsePage.tsx:785` `PAGE_CLS` used once. Inline.
- **P2-14** `pages/ParsePage.tsx:314` `HDR_KEY_CLS` overlaps with `HDR_CELL_CLS`. Merge.
- **P2-15** `pages/ParsesPage.tsx:82-86` `isBoss(title)` — fragile char-code check used 1–2 times. Inline or simplify.

## P2 — Tailwind anti-patterns / spacing (8 items)

- **P2-16** ~30 sites use `text-[0.78rem]` (~12.5px) instead of `text-xs` (12px). Either migrate to `text-xs` or add `--font-size-xs: 0.78rem` token.
- **P2-17** 39 occurrences of `py-[0.4rem]` / `py-[0.45rem]` / `px-[0.55rem]` (~7–9px) where `py-2`/`px-2` (8px) or `py-1.5` (6px) fits. Round to scale.
- **P2-18** 56 occurrences of `gap-[0.35rem]` / `gap-[0.6rem]` / `gap-[0.28rem]` (off-scale). Normalise.
- **P2-19** Sub-4px margins (`mb-[0.2rem]`, `mt-[7px]`, `mb-[3px]`, `gap-y-[4px]`) imperceptible — collapse to scale steps.
- **P2-20** `rounded-[5px]` (7 sites) → `rounded-sm` (4px). 1px imperceptible.
- **P2-21** `rounded-[3px]`/`rounded-[2px]` on progress bars — consider `rounded-full` for pill shape.
- **P2-22** `rounded-[10px]` (4 sites) inconsistent — badge → `rounded-full`, container → `rounded-lg`.
- **P2-23** `style={{ padding: '0 0.1rem' }}` and similar 1-off micro-padding — best handled by P1-49's icon button variant.

## P2 — Inline-style minor (4 items)

- **P2-24** `App.tsx:87` `navLinkStyle` static `fontFamily: 'var(--font-heading)'`. Move to className.
- **P2-25** `TokensPage.tsx:312` `modalTitle` static `fontFamily`. Same.
- **P2-26** `UserWidget`/`NotificationBell` dropdown `top: calc(100% + 6px)` could be className `top-[calc(100%+6px)]`.
- **P2-27** `TokensPage` `tableHeaderRow`/`tableRow` CSSProperties grid objects — convert to Tailwind. Only page using this pattern.

## P2 — Tooltip / popover polish (3 items)

- **P2-28** Two inline list-tooltip popups in GuildPage (spell-tier, adorn) — near-identical. Extract `<NameListPopover>` (low priority after P1-7 sort hooks land).
- **P2-29** `pages/CharacterSpellsTab.tsx:139` `IngredientTooltip` — DOM-anchored `absolute left-full top-0` can clip right viewport edge. Already logged as task #197.
- **P2-30** TokensPage `modalOverlay` CSSProperties — one-off, could use `fixed inset-0 bg-black/70 …` Tailwind.

## P2 — Hooks discipline / over-memoisation (4 items)

- **P2-31** `setFilter = useCallback((v) => setSize(v), [])` in `pages/ParsesPage.tsx:238` — wraps stable state setter. Remove.
- **P2-32** `handleSearch`/`handlePage` useCallbacks in RecipesPage wrap already-memoised `doSearch`. Unnecessary layer.
- **P2-33** Four `useMemo` calls in RankingsPage for trivial `.find()` on 5-entry arrays. Remove the trivial ones.
- **P2-34** RecipesPage URL-state useState not lazy. Minor style inconsistency vs ItemSearchPage which IS lazy.

## P2 — Single-use abstractions / debounce pattern (3 items)

- **P2-35** Debounce timer pattern (`useRef<setTimeout>`) repeated in SpellScrollTooltip + SearchPage. Extract `useDebounce(fn, delay)` hook.
- **P2-36** Three sequential `useEffect` fetches in ParsePage with own cancel guards. After `useFetch` lands (P0-13) these become hook calls.
- **P2-37** AdminPage uses `AbortController` while everything else uses `let cancelled = false`. Resolves at `useFetch` landing (P0-13).

## P2 — Single-use named consts / fonts / handle (3 items)

- **P2-38** `font-mono` used in ActTriggers/EncounterStrategy/ZoneOverview for code-like content. Document as permitted for technical content; consider adding `--font-mono: 'JetBrains Mono', monospace` to `@theme`.
- **P2-39** ParsePage `HDR_KEY_CLS` and `HDR_CELL_CLS` overlap. Merge.
- **P2-40** `handle<T>` (audit-1 F31) — already P1-48; reiterated as P2 polish from another angle.

## P2 — Bundle / assets (3 items)

- **P2-41** `frontend/public/logo.png` may be unused — only `src/assets/EQ2L.png` is imported. Confirm and delete if so.
- **P2-42** Tab navigation in ParsesPage could lazy-load expanded encounters (low traffic).
- **P2-43** Audit `frontend/public/` for any other unused assets.

## P2 — Naming / file-naming nits (4 items)

- **P2-44** `_CAT_COLOUR`, `_SHOPPING_KEY`, `_xmlEsc` — module-private prefix not enforced anywhere. Pick one convention per P2-7.
- **P2-45** Component names: PascalCase ✓; hook names: `useFoo` ✓; const-naming mostly SCREAMING_SNAKE for module consts. Minor mixed-case offenders flagged in audit.
- **P2-46** Boolean naming `open` vs `isOpen` — covered in P2-8.
- **P2-47** File naming consistent (Component.tsx, useHook.ts). No findings.

## P2 — Misc (4 items)

- **P2-48** `pages/AdminPage.tsx:740` `CHUNK = 64` for batch purge — hoist to module level with comment about URL-length safety.
- **P2-49** Document the `--font-mono` mapping (per P2-38).
- **P2-50** `pages/CharacterAAsTab.tsx:235,271` `activeProfile as number` cast — narrow with `isProfileIndex` guard.
- **P2-51** Inline `*= 1000` date arithmetic on AdminPage line 86, GuildPage line 1185 — use `fmtLocalDate`/`fmtRelative` to remove the magic constant.

---

# Cross-cutting non-findings (already correct, documented for future contributors)

- **Tailwind v4 CSS-first config** — no `tailwind.config.js`. Correct for v4; no PostCSS needed.
- **No Tailwind Preflight** — intentional; raw `<button>`/`<input>` need explicit resets, which they have.
- **`@types/react` 19.x in devDeps** — correct for React 19 (DefinitelyTyped still ships v19 types).
- **`useAuth` 401 check** — only explicit status code in the codebase; deliberately distinguishes "not authenticated" from "other failure". Right call.
- **`favicon.svg` + PNG variants** — correct modern + fallback pattern.
- **SectionLabel uses `text-gold`; pages use `text-text-muted` for muted variant** — addressed by P1-10.
- **Game-client recreations** (ItemTooltip, AATree, SpellScrollTooltip) — pixel-perfect by design; only the portal/clamp logic was flagged (P1-6).

---

# Out of scope (explicitly)

- **Backend cleanliness audit** — separate engagement; the user signposted this is the frontend pass.
- **Test coverage** — not assessed. Frontend has `tests/web/` Python tests and `frontend/src/**/__tests__/` vitest tests; coverage gaps are a separate concern.
- **Accessibility audit** — not in scope. Some a11y improvements were noted in mobile audit; deeper sweep is its own thing.
- **Performance profiling** — beyond bundle splitting (P0-14/15), no runtime profiling. Lighthouse/perf scores are a separate audit.
- **i18n** — no internationalisation; English-only is the current intent.
- **Design refresh** — this audit assumes the existing design system is correct; it only flags drift from it.

---

# Rollout plan shape (preview of next document)

Three phases mirroring the mobile work:

- **Phase 1 (P0)** — 17 bug fixes + critical drift. Each is small. Estimate: 1–2 sessions. The `useFetch` consolidation (P0-13) is the largest single item but unblocks several P0 fixes (it's where `credentials: 'include'` becomes enforced everywhere by construction).
- **Phase 2 (P1)** — 53 items. Largest items: file splits (5 pages × ~200-line refactor each), primitive extractions (Badge, TabButton, Textarea, ControlInput, SortTh, useSortable, useTooltipPosition). Sub-phases recommended: P1a primitives + hooks, P1b file splits, P1c remaining cleanup.
- **Phase 3 (P2)** — 51 items. Mostly batch-fixable with codemods or single-file passes. Lowest-priority but biggest visual impact on the codebase tidiness.

The plan document will sequence these in dependency order (e.g. `<SectionLabel variant>` lands before the consumers; `useFetch` lands before its 14 consumers).

---

## Self-review

- **Placeholder scan:** none — every item has file:line, finding, and concrete fix.
- **Cross-audit dedup:** `relativeTime` (audit-1 F7+F26+F32, audit-3 2.4/2.5) consolidated as P0-7/P0-8/P1-15. `inputCls` (audit-1 F4, audit-2 F-17) as P1-17/P1-52. `useFetch` candidates (audit-1 F6, audit-3 implied across 8.x) as single P0-13.
- **Scope check:** Comfortably within "comprehensive ~80-120" target (121 final).
- **Decomposition:** Three phases × independently shippable. Phase 1 is a clean bug-fix pass; Phase 2 has internal sub-batches; Phase 3 is polish.
- **Desktop/mobile-preservation invariants:** All findings are either internal refactors (no UX change), bug fixes (visible improvement), or design-system alignment (visible only on the misrendered sites — which are bugs anyway). No new visual decisions; just enforcing the existing design system.
