import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '../../components/ui'
import { useDebounce } from '../../hooks/useDebounce'
import { fmtLocalDate, fmtLocalDateTime } from '../../formatters'
import { outcomeLabel } from '../../encounterOutcome'
import { warningsNeedingAttention } from '../../clientWarnings'
import { PARSE_BATCH_CHUNK_SIZE } from '../parses/api'
import {
  type AdminParse,
  SECTION_TITLE_CLS,
  TH_CLS, TD_CLS, TABLE_CLS,
} from './types'
import {
  type AdminParseFilters,
  type ResultFilter,
  type StatusFilter,
  EMPTY_FILTERS,
  filtersToParams,
  hasActiveFilters,
} from './parsesAdminFilters'

// ── helpers ───────────────────────────────────────────────────────────────────

// Page size of GET /api/admin/parses (mirrors the backend default limit).
// A full page implies more history below it — the Load-older button pages
// down via the (started_at, id) keyset cursor.
const ADMIN_PAGE_LIMIT = 200

// "Load all matching" pages at the backend's maximum (ADMIN_PARSE_LIST_MAX_LIMIT)
// and stops at a cap, so an unfiltered click can't pull the whole history
// into one table.
const LOAD_ALL_PAGE = 1000
const LOAD_ALL_CAP = 10_000

// Typing in a filter box waits this long before refetching.
const FILTER_DEBOUNCE_MS = 350

// The batch delete route allows 30 calls a minute; a large purge that hits
// the limit waits and retries instead of stopping half way.
const RATE_LIMIT_WAIT_MS = 10_000
const RATE_LIMIT_RETRIES = 6

const FILTER_TD_CLS = 'px-1.5 py-1.5 border-b border-border align-top'
const FILTER_INPUT_CLS =
  'w-full min-w-[4.5rem] appearance-none bg-surface border border-border rounded-sm px-2 py-1 text-[0.8rem] text-text outline-none focus:border-gold/60'

// Trash = an encounter whose title starts lowercase ("a krait warrior");
// named bosses start uppercase. Mirrors isBoss() in ParsesPage and the
// authoritative is_boss() server-side. Inlined here so the admin chunk
// doesn't pull in the whole ParsesPage module.
const isTrashTitle = (title: string): boolean => !/^[A-Z]/.test(title)

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

// Soft-warning chip for a parse whose uploading client stamped something
// worth a look. Which stamps are worth a look is decided in ONE place,
// clientWarnings.ts: EQ2Parser stamps `log_writer_eq2` (the game held the
// log: the good case) on every automatic upload, so "any stamp at all" put
// the chip on every parse from the desktop parser. The chip counts only the
// values needing attention and is absent when there are none; its tooltip
// still lists every value as the client sent it, the benign one included.
// The hard tamper signals are NOT here: those landed in the tamper-reports
// table and never reached encounters.
function ClientWarningChip({ warnings }: { warnings: string[] | null }) {
  const attention = warningsNeedingAttention(warnings)
  if (attention.length === 0) return null
  const label =
    `Client warnings needing a look: ${attention.join(', ')}. ` +
    `Everything the client sent: ${(warnings ?? []).join(', ')}`
  return (
    <span
      className="ml-2 bg-warning/15 text-warning border border-warning/40 rounded px-1.5 py-0.5 text-[0.7rem] uppercase tracking-[0.05em] align-middle"
      title={label}
      aria-label={label}
    >
      ⚠ {attention.length}
    </span>
  )
}

// ── ParsesAdminTable ──────────────────────────────────────────────────────────

export function ParsesAdminTable() {
  // `filters` is what the inputs show; `applied` drives the fetch. Text boxes
  // commit after a pause, selects and dates at once.
  const [filters, setFilters] = useState<AdminParseFilters>(EMPTY_FILTERS)
  const [applied, setApplied] = useState<AdminParseFilters>(EMPTY_FILTERS)
  const filtersRef = useRef(filters)
  filtersRef.current = filters
  const [rows, setRows] = useState<AdminParse[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)  // long load / purge status line
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Bumped whenever the applied filters change, so a long "Load all" stops
  // instead of appending rows from the old filter.
  const generation = useRef(0)

  // Reads the latest filters at fire time: a select changed during the pause
  // is not overwritten by the older snapshot of the text box.
  const commitTyped = useDebounce(() => setApplied(filtersRef.current), FILTER_DEBOUNCE_MS)

  function setTextFilter(key: 'id' | 'title' | 'zone' | 'guild' | 'uploader' | 'minPlayers', value: string) {
    setFilters(prev => ({ ...prev, [key]: value }))
    commitTyped()
  }

  function setNowFilter<K extends 'from' | 'to' | 'result' | 'status'>(key: K, value: AdminParseFilters[K]) {
    const next = { ...filtersRef.current, [key]: value }
    setFilters(next)
    setApplied(next)
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS)
    setApplied(EMPTY_FILTERS)
  }

  const fetchPage = useCallback(
    async (page: { limit?: number; before?: number; beforeId?: number }, signal?: AbortSignal) => {
      const res = await fetch(`/api/admin/parses?${filtersToParams(applied, page)}`, { credentials: 'include', signal })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? 'Failed to load parses')
      }
      return (await res.json()) as AdminParse[]
    },
    [applied],
  )

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchPage({}, signal)
      setRows(data)
      setHasMore(data.length >= ADMIN_PAGE_LIMIT)
      setSelected(new Set())
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      setError(e instanceof TypeError ? 'Network error — could not load parses.' : `Error: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [fetchPage])

  useEffect(() => {
    generation.current += 1
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  function appendRows(page: AdminParse[]) {
    setRows(prev => {
      const seen = new Set(prev.map(r => r.id))
      return [...prev, ...page.filter(r => !seen.has(r.id))]
    })
  }

  // Page down through history: fetch the window strictly older than the
  // last loaded row. Selection is preserved — appending never unchecks.
  const loadOlder = useCallback(async () => {
    const oldest = rows[rows.length - 1]
    if (!oldest || loadingOlder) return
    setLoadingOlder(true)
    setError(null)
    try {
      const page = await fetchPage({ before: oldest.started_at, beforeId: oldest.id })
      appendRows(page)
      setHasMore(page.length >= ADMIN_PAGE_LIMIT)
    } catch (e) {
      setError(e instanceof TypeError ? 'Network error — could not load older parses.' : `Error: ${(e as Error).message}`)
    } finally {
      setLoadingOlder(false)
    }
  }, [rows, loadingOlder, fetchPage])

  // Every row matching the filters, in pages of LOAD_ALL_PAGE, up to
  // LOAD_ALL_CAP — what a filter-then-purge-everything needs.
  async function loadAll() {
    const start = generation.current
    let oldest = rows[rows.length - 1]
    if (!oldest) return
    let total = rows.length
    setLoadingOlder(true)
    setError(null)
    try {
      while (total < LOAD_ALL_CAP) {
        setProgress(`Loading… ${total.toLocaleString()} rows`)
        const page = await fetchPage({ limit: LOAD_ALL_PAGE, before: oldest.started_at, beforeId: oldest.id })
        if (generation.current !== start) return  // filters changed under us
        appendRows(page)
        total += page.length
        if (page.length < LOAD_ALL_PAGE) {
          setHasMore(false)
          return
        }
        oldest = page[page.length - 1]
      }
      setHasMore(true)
      setError(`Stopped at ${LOAD_ALL_CAP.toLocaleString()} rows. Narrow the filters to see the rest.`)
    } catch (e) {
      setError(e instanceof TypeError ? 'Network error — could not load parses.' : `Error: ${(e as Error).message}`)
    } finally {
      setProgress(null)
      setLoadingOlder(false)
    }
  }

  function toggleRow(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const allSelected = rows.length > 0 && rows.every(r => selected.has(r.id))
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map(r => r.id)))
  }

  const trashCount = rows.reduce((n, r) => (isTrashTitle(r.title) ? n + 1 : n), 0)
  function selectAllTrash() {
    setSelected(new Set(rows.filter(r => isTrashTitle(r.title)).map(r => r.id)))
  }

  const hiddenCount = rows.reduce((n, r) => (r.hidden ? n + 1 : n), 0)
  function selectAllHidden() {
    setSelected(new Set(rows.filter(r => r.hidden).map(r => r.id)))
  }
  // Only hidden rows can be restored; the button counts those in the selection.
  const selectedHiddenIds = rows.filter(r => r.hidden && selected.has(r.id)).map(r => r.id)

  async function purgeOne(p: AdminParse) {
    if (!confirm(`Permanently delete "${p.title}"? This removes it from all leaderboards and cannot be undone.`)) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/parses/${p.id}?purge=1`, { method: 'DELETE', credentials: 'include' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Purge failed'}`)
        return
      }
      await load()
    } catch {
      setError('Network error — purge failed.')
    } finally {
      setBusy(false)
    }
  }

  async function unhideOne(p: AdminParse) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/parses/${p.id}/unhide`, { method: 'POST', credentials: 'include' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Unhide failed'}`)
        return
      }
      await load()
    } catch {
      setError('Network error — unhide failed.')
    } finally {
      setBusy(false)
    }
  }

  async function purgeSelected() {
    const ids = [...selected]
    if (ids.length === 0) return
    if (!confirm(`Permanently delete ${ids.length} parse${ids.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setBusy(true)
    setError(null)
    let done = 0
    try {
      for (let i = 0; i < ids.length; i += PARSE_BATCH_CHUNK_SIZE) {
        const chunk = ids.slice(i, i + PARSE_BATCH_CHUNK_SIZE)
        const url = `/api/parses/batch?ids=${chunk.join(',')}&purge=1`
        setProgress(`Purging… ${done.toLocaleString()} of ${ids.length.toLocaleString()}`)
        let res = await fetch(url, { method: 'DELETE', credentials: 'include' })
        for (let retry = 0; res.status === 429 && retry < RATE_LIMIT_RETRIES; retry++) {
          setProgress(`Purging… ${done.toLocaleString()} of ${ids.length.toLocaleString()} (rate limited, waiting)`)
          await sleep(RATE_LIMIT_WAIT_MS)
          res = await fetch(url, { method: 'DELETE', credentials: 'include' })
        }
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          setError(`Error after purging ${done} of ${ids.length}: ${body.detail ?? 'Bulk purge failed'}`)
          return
        }
        done += chunk.length
      }
      setSelected(new Set())
      await load()
    } catch {
      setError(`Network error after purging ${done} of ${ids.length}.`)
    } finally {
      setProgress(null)
      setBusy(false)
    }
  }

  async function unhideSelected() {
    const ids = selectedHiddenIds
    if (ids.length === 0) return
    if (!confirm(`Restore ${ids.length} hidden parse${ids.length === 1 ? '' : 's'} to /parses?`)) return
    setBusy(true)
    setError(null)
    try {
      let restored = 0
      for (let i = 0; i < ids.length; i += PARSE_BATCH_CHUNK_SIZE) {
        const chunk = ids.slice(i, i + PARSE_BATCH_CHUNK_SIZE)
        const res = await fetch(`/api/parses/batch/unhide?ids=${chunk.join(',')}`, { method: 'POST', credentials: 'include' })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          setError(`Error after restoring ${restored} of ${ids.length}: ${body.detail ?? 'Bulk unhide failed'}`)
          return
        }
        const body = (await res.json()) as { unhidden: number }
        restored += body.unhidden
      }
      setSelected(new Set())
      await load()
    } catch {
      setError('Network error — bulk unhide failed.')
    } finally {
      setBusy(false)
    }
  }

  const selectedCount = selected.size
  const filtering = hasActiveFilters(applied)

  const textFilter = (key: 'id' | 'title' | 'zone' | 'guild' | 'uploader', label: string, placeholder: string) => (
    <input
      type="text"
      value={filters[key]}
      onChange={e => setTextFilter(key, e.target.value)}
      placeholder={placeholder}
      aria-label={label}
      className={FILTER_INPUT_CLS}
    />
  )

  return (
    <div>
      <p className={SECTION_TITLE_CLS}>
        Parses ({rows.length.toLocaleString()}{hasMore ? '+' : ''})
      </p>

      {/* Bulk action bar */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={selectAllTrash}
          disabled={busy || trashCount === 0}
          title="Select every trash encounter (lowercase title) loaded below"
        >
          Select all trash ({trashCount})
        </Button>
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={selectAllHidden}
          disabled={busy || hiddenCount === 0}
          title="Select every hidden (soft-deleted) parse loaded below"
        >
          Select all hidden ({hiddenCount})
        </Button>
        <Button
          variant="primary"
          size="sm"
          type="button"
          onClick={unhideSelected}
          disabled={busy || selectedHiddenIds.length === 0}
          title="Make the selected hidden parses visible on /parses again"
        >
          Unhide selected ({selectedHiddenIds.length})
        </Button>
        <Button
          variant="danger"
          size="sm"
          type="button"
          onClick={purgeSelected}
          disabled={busy || selectedCount === 0}
        >
          Purge selected ({selectedCount.toLocaleString()})
        </Button>
        <Button
          variant="ghost"
          size="sm"
          type="button"
          onClick={clearFilters}
          disabled={busy || !hasActiveFilters(filters)}
          className="ml-auto"
        >
          Clear filters
        </Button>
      </div>

      {progress && <p className="text-text-muted text-[0.85rem] mb-2" role="status">{progress}</p>}
      {error && <p className="text-danger mb-2">{error}</p>}

      <div className="overflow-x-auto border border-border rounded-md">
        <table className={TABLE_CLS}>
          <thead>
            <tr className="bg-white/2">
              <th className={`${TH_CLS} w-[1%]`}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="Select all visible parses"
                  disabled={rows.length === 0}
                />
              </th>
              <th className={TH_CLS}>#</th>
              <th className={TH_CLS}>Title</th>
              <th className={TH_CLS}>Zone</th>
              <th className={TH_CLS}>Guild</th>
              <th className={TH_CLS}>Uploader</th>
              <th className={TH_CLS}>Date</th>
              <th className={`${TH_CLS} text-center`}>Players</th>
              <th className={TH_CLS}>Result</th>
              <th className={TH_CLS}>Actions</th>
            </tr>
            {/* One filter per column, all applied together. */}
            <tr className="bg-white/2">
              <td className={FILTER_TD_CLS} />
              <td className={FILTER_TD_CLS}>{textFilter('id', 'Filter by parse id', '#id')}</td>
              <td className={FILTER_TD_CLS}>{textFilter('title', 'Filter by title', 'Title')}</td>
              <td className={FILTER_TD_CLS}>{textFilter('zone', 'Filter by zone', 'Zone')}</td>
              <td className={FILTER_TD_CLS}>{textFilter('guild', 'Filter by guild', 'Guild')}</td>
              <td className={FILTER_TD_CLS}>{textFilter('uploader', 'Filter by uploader', 'Uploader')}</td>
              <td className={FILTER_TD_CLS}>
                <div className="flex flex-col gap-1">
                  <input
                    type="date"
                    value={filters.from}
                    onChange={e => setNowFilter('from', e.target.value)}
                    aria-label="Filter from date"
                    title="From (inclusive)"
                    className={FILTER_INPUT_CLS}
                  />
                  <input
                    type="date"
                    value={filters.to}
                    onChange={e => setNowFilter('to', e.target.value)}
                    aria-label="Filter to date"
                    title="To (inclusive)"
                    className={FILTER_INPUT_CLS}
                  />
                </div>
              </td>
              <td className={FILTER_TD_CLS}>
                <input
                  type="text"
                  inputMode="numeric"
                  value={filters.minPlayers}
                  onChange={e => setTextFilter('minPlayers', e.target.value)}
                  placeholder="≥ min"
                  aria-label="Filter by minimum players"
                  className={`${FILTER_INPUT_CLS} text-center`}
                />
              </td>
              <td className={FILTER_TD_CLS}>
                <select
                  value={filters.result}
                  onChange={e => setNowFilter('result', e.target.value as ResultFilter)}
                  aria-label="Filter by result"
                  className={`${FILTER_INPUT_CLS} cursor-pointer`}
                >
                  <option value="">Any</option>
                  <option value="1">win</option>
                  <option value="2">partial</option>
                  <option value="3">loss</option>
                  <option value="0">unknown</option>
                </select>
              </td>
              <td className={FILTER_TD_CLS}>
                <select
                  value={filters.status}
                  onChange={e => setNowFilter('status', e.target.value as StatusFilter)}
                  aria-label="Filter by status"
                  className={`${FILTER_INPUT_CLS} cursor-pointer`}
                >
                  <option value="all">All rows</option>
                  <option value="visible">Visible only</option>
                  <option value="hidden">Hidden only</option>
                </select>
              </td>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={10} className={`${TD_CLS} text-text-muted text-center p-6`}>Loading…</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={10} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  {filtering ? 'No parses match the filters.' : 'No parses.'}
                </td>
              </tr>
            ) : (
              rows.map(p => (
                <tr key={p.id}>
                  <td className={TD_CLS}>
                    <input
                      type="checkbox"
                      checked={selected.has(p.id)}
                      onChange={() => toggleRow(p.id)}
                      aria-label={`Select ${p.title}`}
                    />
                  </td>
                  <td className={`${TD_CLS} text-text-muted font-mono whitespace-nowrap`}>{p.id}</td>
                  <td className={TD_CLS}>
                    <Link
                      to={`/parse/${p.id}`}
                      className="font-semibold text-text hover:text-gold"
                      title={`Open parse #${p.id}`}
                    >
                      {p.title}
                    </Link>
                    {p.hidden && (
                      <span
                        className="ml-2 bg-gold/15 text-gold border border-gold/40 rounded px-1.5 py-0.5 text-[0.7rem] uppercase tracking-[0.05em] align-middle"
                        title={p.hidden_at ? `Hidden ${fmtLocalDateTime(p.hidden_at)}` : undefined}
                      >
                        Hidden{p.hidden_by ? ` · ${p.hidden_by_name ?? p.hidden_by}` : ''}
                      </span>
                    )}
                    <ClientWarningChip warnings={p.client_warnings} />
                  </td>
                  <td className={`${TD_CLS} text-text-muted`}>{p.zone ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{p.guild_name ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{p.uploaded_by ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>{fmtLocalDate(p.started_at)}</td>
                  <td className={`${TD_CLS} text-center text-text-muted`}>{p.player_count}</td>
                  {/* win / partial / loss / — : the shared mapping in encounterOutcome.ts */}
                  <td className={`${TD_CLS} text-text-muted`}>{outcomeLabel(p.success_level)}</td>
                  <td className={`${TD_CLS} whitespace-nowrap`}>
                    {p.hidden && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => unhideOne(p)}
                        disabled={busy}
                        title="Make this parse visible on /parses again"
                      >
                        Unhide
                      </Button>
                    )}
                    <Button variant="danger" size="sm" className={p.hidden ? 'ml-1.5' : ''} onClick={() => purgeOne(p)} disabled={busy}>
                      Purge
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {!loading && hasMore && (
        <div className="flex justify-center gap-2 mt-3">
          <Button variant="secondary" size="sm" onClick={loadOlder} disabled={busy || loadingOlder}>
            {loadingOlder ? 'Loading…' : `Load older (${rows.length.toLocaleString()} loaded)`}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={loadAll}
            disabled={busy || loadingOlder}
            title={`Load every parse matching the filters, up to ${LOAD_ALL_CAP.toLocaleString()}`}
          >
            Load all matching
          </Button>
        </div>
      )}
    </div>
  )
}
