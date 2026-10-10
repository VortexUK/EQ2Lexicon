---
paths:
  - "frontend/**"
---

# Frontend conventions (React + TypeScript, Tailwind v4)

## Frontend styling — Tailwind v4 (ENFORCED)

Tailwind v4 is the **single** styling system. There is no `tailwind.config.js` and no PostCSS — config is CSS-first in `frontend/src/index.css` via `@theme`. New frontend work MUST follow these rules; do not reintroduce the old patterns.

**The one rule:** Tailwind **utility classes for all static styling**; `style={{…}}` **only** for runtime-computed/dynamic values (data-driven colours, computed widths/positions, `gridTemplateColumns`, gradient-text, glows). Do **not** add new static inline `style` objects, and do **not** create per-page `CSSProperties` style-object consts.

- **Tokens → utilities**: design tokens live in the `@theme` block (`--color-*`, `--font-*`, `--radius-*`) and generate utilities — `bg-surface`, `text-gold`, `text-text-muted`, `border-border`, `text-rarity-fabled`, `font-heading`, `rounded-md`, etc. Spacing uses Tailwind's built-in 4px scale (`p-4` = 1rem). Use arbitrary values (`text-[0.88rem]`, `py-[0.45rem]`) only when no token/step fits.
- **Cascade layers**: `@layer base` (reset + element defaults) → `@layer components` (the `.btn`/`.card`/nav classes) → `utilities` (last, so page utilities win). Tailwind **Preflight is intentionally NOT imported** — the app has its own reset; keep it that way.
- **Rarity/tier colours**: ONE source of truth — `frontend/src/rarityColors.ts` (`itemRarityColor`, `recipeTierColor`, `qualityStyle`) backed by the `--color-rarity-*` tokens. Never define a new `TIER_COLOUR` map in a page.
- **Legacy `var(--*)` aliases**: `:root` still aliases the old names (`--gold` → `var(--color-gold)`, etc.) so the remaining *dynamic* `style={{}}` values resolve. Fine to reference in `style` for dynamic values; for static styling use the utility instead.
- **Exceptions (keep bespoke inline)**: `ItemTooltip`, `SpellScrollTooltip`, `AATree` faithfully recreate the in-game client (Times New Roman, computed glows/positions) — leave their inline styling alone.

## Shared frontend infrastructure (use these — don't hand-roll)

The 2026-05-29 cleanliness audit introduced a set of canonical primitives, hooks, and utilities. When writing new frontend code, reach for these BEFORE rolling your own — every hand-rolled version diverges and accrues drift.

### UI primitives — `frontend/src/components/ui/`

| Primitive | Use when |
|---|---|
| `<Button variant size>` | Any action button. `variant`: `primary`/`secondary`/`ghost`/`danger`. `size`: `sm`/`md`/`lg`/`icon` (icon = compact square for emoji/icon-only). |
| `<LinkButton>` | A `<button>`-styled `<a>` (external links that should look like buttons — e.g. the Support page Sponsor CTA). |
| `<Card>` | Any surface panel with the gold-tinted edge + soft shadow. Don't hand-roll `border border-border rounded bg-surface` divs. |
| `<SectionLabel variant>` | Uppercase eyebrow heading. `variant`: `gold` (default, brand) or `muted` (secondary headings in dense forms / admin tables). |
| `<Badge variant>` | Small rounded status label. `variant`: `success`/`warning`/`danger`/`info`/`muted`/`gold`. Replaces ad-hoc badge styling. |
| `<TabButton active onClick>` | The active-underline tab button (gold border-bottom on active). Wrap a group in `<div className="flex border-b border-border">`. |
| `<Textarea mono>` | Dark-theme textarea. `mono` for code/regex/markdown editors. Includes the Preflight reset so it doesn't render white. |
| `<DiscordButton href? children?>` | The "Sign in with Discord" link. Defaults to the right href + label; just `<DiscordButton />` is usually all you need. |
| `<SortTh sortKey active dir onSort>` | Pairs with `useSortable`. Click to toggle sort key/direction, renders the active caret. |

### Hooks — `frontend/src/hooks/`

| Hook | Use when |
|---|---|
| `useFetch<T>(url, opts?)` | Auto-fetch on mount + url-change. Returns `{ data, loading, error, statusCode, refetch }`. **Enforces `credentials: 'include'` by construction** — the P0 "missing credentials" class of bug can't happen if you go through this. Use `statusCode === 404` to detect "not found" / empty-state. |
| `useLazyFetch<T>()` | Tab-triggered or button-triggered fetches. Returns `{ data, loading, error, statusCode, run, reset }`; caller invokes `run(url)` on user action. |
| `useSortable<T, K>(rows, getValue, initialKey, initialDir?, defaultDirFor?)` | Manages sort key/direction over a tabular dataset. Pre-filter rows via `useMemo` before passing in. Pass `defaultDirFor` to make numeric columns default to descending on first click. |
| `useTooltipPosition({ x, y, width, ... })` | Viewport-aware fixed-position coords with right/bottom flip. Pure helper also exported: `clampTooltipPosition(opts)`. |
| `useItemTooltip()` | `{ tooltip, showTip, hideTip, moveTip }` — the boilerplate for hover-state-with-mouse-coords used by `<ItemTooltip>`. Colocated with `ItemTooltip.tsx`. |
| `useDebounce(fn, delay)` | Stable debounced wrapper around `fn`. Cleared on unmount automatically. (No `.cancel()` method yet — see follow-up task #197 if you need synchronous cancel.) |
| `useAuth()` + `isContributor(auth)` + `isUser(data)` | Auth state hook + the canonical "can the user edit?" derivation + a runtime type guard. Don't compute `auth.user.is_admin \|\| auth.user.static_roles.includes('contributor')` inline. |
| `useServer()` | Per-server bootstrap data (`world`, `displayName`, `maxLevel`, `currentXpac`, etc.) — see "Per-server architecture" above. |
| `useCensusStream<T>` (`subscribe<T>`) | SSE refresh stream. The `subscribe` API is generic; pass the type argument and you won't need an `as Character` cast. |

### Utilities — `frontend/src/lib/`

| Utility | Use when |
|---|---|
| `toErrorMessage(err: unknown)` | Replace `String((err as Error).message ?? err)` patterns. Sound narrowing — handles `Error`, `string`, and arbitrary thrown values. |
| `handle<T>(r: Response)` | Generic fetch response handler. Throws on non-ok, returns parsed JSON otherwise. Use in hand-rolled fetches (mutation endpoints in event handlers); for read paths, prefer `useFetch`. |

### Formatters — `frontend/src/formatters.ts`

`fmtNum`, `fmtNumOrDash`, `fmtDuration`, `fmtLocalDate`, `fmtLocalTime`, `fmtLocalDateTime`, `fmtRelative`. Don't reinvent date arithmetic with inline `new Date(unix * 1000)` — the formatters handle the `* 1000` and the locale/threshold logic. `fmtRelative` switches to a date string for anything older than ~8 weeks.

### Design tokens — `frontend/src/index.css`

| Token group | Notes |
|---|---|
| Surface + text | `--color-bg`, `--color-surface`, `--color-surface-raised`, `--color-border`, `--color-text`, `--color-text-muted` → `bg-*`, `text-*`, `border-*` utilities. |
| Brand | `--color-gold`, `--color-gold-bright`, `--color-gold-dim`, `--gold-rgb` (literal for `rgba(var(--gold-rgb), α)`). |
| Semantic | `--color-success`/`--success-rgb`, `--color-warning`/`--warning-rgb`, `--color-danger`/`--danger-rgb`. Use the token, NOT the hex. Past drift bugs (`#22c55e` vs `#4ade80`, etc.) traced to hex hardcodes. |
| Stat | `--color-stat-primary` (lime), `--color-stat-secondary` (cyan) — EQ2 stat colours. |
| Rarity | `--color-rarity-*` (common/handcrafted/treasured/legendary/fabled/mythical/ethereal/celestial/ancient). Via `rarityColors.ts`. |
| Discord | `--color-discord` — ONLY the Discord sign-in button. |
| Radius | `--radius-sm` (4px), `--radius-sm2` (6px — table cells/tooltips), `--radius-md` (8px), `--radius-lg` (12px), `--radius-pill` (999px) → `rounded-*` utilities. |
| Z-index ladder | `--z-header` (200), `--z-nav-backdrop` (250), `--z-nav-panel` (260), `--z-dropdown` (300), `--z-modal` (1000), `--z-tooltip` (9999) → `z-header`, `z-dropdown` etc. utilities. Use the token, not a `z-[N]` arbitrary value. |
| Fonts | `--font-heading` (Cinzel), `--font-body` (Spectral). `font-mono` is permitted for technical content (regex, hex, CLI). Tooltip recreations use Times New Roman via inline style. |

### File-split conventions

Pages that grow past ~700 lines are split into focused sibling files under a same-named subdir:
- `pages/admin/` — UsersTable, ClaimsTable, RoleRequestsTable, ServersSection, ParsesAdminTable, types.ts
- `pages/guild/` — GuildRosterTab, GuildSpellCheckTab, GuildAdornCheckTab, types.ts
- `pages/items/` — ItemSearchFilters
- `pages/parse/` — CombatantDetailPanel
- `pages/recipes/` — RecipeCard, ShoppingListPanel, QtyBtn, types.ts
- `components/act/` — TriggerEditor, SpellTimerEditor, ActImportPanel, primitives.tsx, types.ts

Shared types + className constants for a split page go in a sibling `types.ts`. Sub-components owning their own state + fetch logic are separate files; small inline render helpers (< 100 lines, tightly coupled) stay in the parent.

### When to break the rules

- **Game-client recreations** (`ItemTooltip`, `SpellScrollTooltip`, `AATree`) — these faithfully reproduce the in-game look (Times New Roman, computed glows, percentage-based coordinates). Inline `style={{}}` is *required*; don't try to "modernise" them.
- **`<Button>` doesn't fit** — sometimes a raw `<button>` with `appearance-none border-0 bg-transparent` is the right primitive (icon-only drag handles, hamburger triggers). Use raw + the Preflight-reset utilities.
- **`useFetch` doesn't fit** — for chained / dependent fetches that need to read each other's result mid-flight, keep a hand-rolled `useEffect`. Just remember `credentials: 'include'` + `res.ok` + cleanup.

### Mandatory testing rules

- **Module-load side effects need a no-throw vitest.** Any frontend module with top-level code that mutates globals (monkey-patches History/Location/etc., installs event listeners, runs heavy init work) needs at minimum `await expect(import('./mod')).resolves.toBeDefined()` in jsdom. The v5 historyTrace disaster shipped because this check was missing — `window.location.assign = fn` throws TypeError ("assign is read-only") at import time and broke the entire site. Reference pattern: the (now-deleted) `historyTrace.test.ts` from the 2026-05-29 diagnostic.

- **URL filter state needs a "setSearchParams can throw" test.** Browser extensions (ClearURLs, Privacy Badger, uBlock) and Firefox tracking-protection internals share the per-Document History API throttle quota. When depleted, `setSearchParams` throws `DOMException SecurityError`. Any component that uses `useSearchParams` for actively-clicked filter state should have a vitest that mocks setSearchParams to throw and asserts the UI still responds. RankingsPage uses the "React state as source of truth, URL as best-effort mirror via `safeSetParams`" pattern — copy from there; don't rebuild the URL-first pattern.

## Frontend design principles

When building or changing the React frontend, hold to these — the goal is a distinctive, cohesive interface that reads as *deliberately designed for an EverQuest 2 guild tool*, not a generic dashboard. (Implementation rules live in [Frontend styling — Tailwind v4](#frontend-styling--tailwind-v4-enforced) above; these are the aesthetic intent behind them.)

- **Typography**: Use characterful, intentional fonts. Headings are **Cinzel** (`font-heading`) — a classical serif fitting Norrath's high-fantasy tone; body is **Spectral** (`font-body`), a screen serif that reinforces the "lexicon/tome" voice. In-game-style tooltips deliberately use Times New Roman to mirror EQ2's client. Never introduce generic UI fonts (Inter, Roboto, Arial, system fonts) for display text.
- **Color & theme**: One cohesive palette — gold (`--color-gold`) on deep stone/parchment. Gold is the single accent (links, focus, active states); Discord blurple is confined to the sign-in button only. Dominant base colours with sharp metallic accents beat timid, evenly-distributed palettes. Avoid the clichéd purple-gradient-on-white "AI slop" look.
- **Motion**: Favour CSS-only transitions. Spend the budget on a few high-impact moments (the staggered page-load reveal via `.page-enter`) rather than scattering small effects; honour `prefers-reduced-motion`.
- **Backgrounds & depth**: Atmosphere via the layered background overlay (warm top-glow + vignette) and the gilded card treatment (gold-tinted edge, soft shadow, top hairline) — not flat fills. Keep it legible.
- **Cohesion over novelty**: Every page should feel part of the same product. Reuse the theme utilities and the `ui/` primitives; never reinvent spacing, card, or button styles per page.

## Architecture notes

- **Frontend split**: `CharacterPage.tsx` exports `StatGroup`/`StatRow`; `CharacterAAsTab.tsx` and `CharacterSpellsTab.tsx` import them.
- **Spell icons**: served as static files at `/spell-icons/{id}.png`; backdrop + foreground layered with CSS `position: absolute; inset: 0`.

## Gotchas

- **No Preflight means raw elements keep browser styling.** A raw `<button>` or `<input>` renders with a white background and a default border on the dark theme. Add `appearance-none border-0 bg-transparent` and set the text colour, or use the `ui/` primitives.
- **Typecheck is `npm run typecheck` (`tsc -b`).** `tsc --noEmit -p tsconfig.json` checks nothing: the root tsconfig only references sub-projects. The pre-push hook and CI run `tsc -b`.
- **URL filter state**: keep the value in React state, seeded once from the URL with a lazy initialiser; handlers call the state setter; one effect mirrors state to the URL through a `safeSetParams` wrapper that swallows the `DOMException`. Do not read `useSearchParams().get()` during render. Back and forward within the page become no-ops; shareable URLs still work. Reference: `frontend/src/pages/RankingsPage.tsx`.
- **Visual changes**: build so the user can look (`npm run build`), describe what changed, and commit only after they approve.
- **Raid consumables sheet** (`frontend/src/data/raidConsumables.ts`, keyed by the server's current expansion): an entry is valid only if a recipe outputs that item id and the recipe's `out_level` falls inside the tier band. Level-80 crafted consumables belong to the expansion after Rise of Kunark even though its cap is 80. Find candidates by marker columns (temporary adornments are `items.adornment_color = 'temporary'`), never by name prefix.
