import { useEffect, useState } from 'react'

export interface PagedSearch<T> {
  search: string
  setSearch: (s: string) => void
  /** Current (clamped) 1-based page. */
  page: number
  setPage: (p: number) => void
  pageCount: number
  /** 0-based index of the first row on the current page. */
  start: number
  perPage: number
  /** Filtered row count (across all pages). */
  total: number
  /** Rows for the current page only. */
  rows: T[]
}

/**
 * Client-side search + pagination over an in-memory list — the shared engine
 * behind the admin tables (users / claims / role-requests).
 *
 * Filtering runs each render (cheap for admin-sized lists). The lowercased,
 * trimmed query is passed to `matches`. Changing the search text or `filterKey`
 * (e.g. a status pill) resets to page 1; the page clamps if the list shrinks
 * under you (e.g. after a row is approved/kicked), so you never land on an
 * empty page. Apply any non-search pre-filter (status pills) to `items` before
 * passing them in, and pass that filter's value as `filterKey`.
 */
export function usePagedSearch<T>(
  items: T[],
  matches: (item: T, q: string) => boolean,
  opts: { perPage?: number; filterKey?: unknown } = {},
): PagedSearch<T> {
  const perPage = opts.perPage ?? 10
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  // Jump back to the first page whenever the active filter or search changes.
  useEffect(() => { setPage(1) }, [search, opts.filterKey])

  const q = search.trim().toLowerCase()
  const filtered = q ? items.filter(it => matches(it, q)) : items

  const pageCount = Math.max(1, Math.ceil(filtered.length / perPage))
  const safePage = Math.min(page, pageCount)
  const start = (safePage - 1) * perPage
  const rows = filtered.slice(start, start + perPage)

  return { search, setSearch, page: safePage, setPage, pageCount, start, perPage, total: filtered.length, rows }
}
