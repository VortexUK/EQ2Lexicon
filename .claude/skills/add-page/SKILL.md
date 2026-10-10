---
name: add-page
description: Checklist for adding a frontend page or route - lazy import, Route, both navigation menus, data fetching and the mandatory tests. Use when creating a new page under frontend/src/pages or adding a nav entry.
---

# Add a frontend page

Reading any file under `frontend/` loads `.claude/rules/frontend.md` (Tailwind rules, the
`ui/` primitives, hooks, tokens). This skill is only the wiring that is easy to miss.

## Steps

1. **Page file**: `frontend/src/pages/<Name>Page.tsx`, default export, with a colocated
   `<Name>Page.test.tsx`. Once a page passes roughly 700 lines, split it into
   `frontend/src/pages/<name>/` with a `types.ts` for shared types.
2. **Import in `frontend/src/App.tsx`**: lazy unless it is a landing page.
   `const XPage = lazy(() => import('./pages/XPage'))` goes with the other lazy imports;
   the Suspense fallback already exists in the layout.
3. **Route**: add `<Route path="..." element={<XPage />} />` inside the `<Route element={<Layout />}>`
   block, above the catch-all `*` route.
4. **Login-free page**: add the path to `PUBLIC_PATHS` in `App.tsx`. Only the privacy page
   is public today; adding to that list is a product decision, so confirm it first.
5. **Navigation, in two files that are not linked to each other**:
   - desktop: the matching array in `App.tsx` (`BROWSE_ITEMS`, `RAIDS_ITEMS` or
     `LEADERBOARD_ITEMS`), entries shaped `{ to, label, also? }`;
   - mobile: `GROUPS` in `frontend/src/components/MobileNav.tsx`.
   Editing one and not the other ships a page that is unreachable on phones.
6. **Data**: read with `useFetch` (or `useLazyFetch` for tab or button triggered loads) from
   `frontend/src/hooks/`; they set `credentials: 'include'` for you. Per-server values come
   from `useServer()`. Mutations in handlers use `handle()` from `frontend/src/lib/api.ts`.
7. **Tests that are mandatory when they apply**:
   - a module with top-level side effects needs an "imports without throwing" test;
   - filter state kept in the URL needs a test where `setSearchParams` throws and the UI
     still responds. Keep state in React, seeded once from the URL, and mirror it back
     through the `safeSetParams` pattern in `frontend/src/pages/RankingsPage.tsx`.

## Check

```bash
uv run --frozen python scripts/tools/verify.py --fe
```

Then build (`npm run build` in `frontend/`) so the user can look at the page. Visual work
is not committed until they have seen it.
