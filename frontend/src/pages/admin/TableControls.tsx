import { Button } from '../../components/ui'

// Shared search + pagination chrome for the admin tables. Pairs with the
// usePagedSearch hook (hooks/usePagedSearch.ts).

export function TableSearch({
  value,
  onChange,
  placeholder = 'Search…',
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <input
      type="search"
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      aria-label={placeholder}
      className="ml-auto w-full sm:w-[220px] text-[0.85rem] py-1 px-2.5 rounded-sm2 border border-border bg-surface-raised text-text"
    />
  )
}

export function TablePager({
  page,
  pageCount,
  start,
  perPage,
  total,
  onPage,
}: {
  page: number
  pageCount: number
  start: number
  perPage: number
  total: number
  onPage: (p: number) => void
}) {
  // Nothing to page through when it all fits on one page.
  if (total <= perPage) return null
  return (
    <div className="flex items-center justify-between mt-3 text-[0.8rem] text-text-muted">
      <span>
        Showing {start + 1}–{Math.min(start + perPage, total)} of {total}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Prev
        </Button>
        <span>Page {page} of {pageCount}</span>
        <Button variant="ghost" size="sm" disabled={page >= pageCount} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  )
}
