import { useState, useEffect, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ItemTooltip, useItemTooltip } from '../components/ItemTooltip'
import { Button, Card } from '../components/ui'
import { itemRarityColor } from '../rarityColors'
import ItemSearchFilters, { ItemSearchQuery, StatFilter } from './items/ItemSearchFilters'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ItemSearchResult {
  id:               number
  name:             string
  tier:             string | null
  slot:             string | null
  item_type:        string | null
  level:            number | null
  class_label:      string | null
  icon_id:          number | null
  stats:            string[]
  stat_values:      Record<string, number>
}

interface ItemSearchResponse {
  results:  ItemSearchResult[]
  total:    number
  page:     number
  per_page: number
}

// ── Quality colour map ─────────────────────────────────────────────────────────

/** Title-case each word of an ALL-CAPS DB tier string: "FABLED" → "Fabled". */
function displayTier(tier: string | null): string {
  if (!tier) return '—'
  return tier
    .split(' ')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

// ── Table header / cell styles ────────────────────────────────────────────────

const TH = 'px-[0.7rem] py-2 text-[0.72rem] uppercase tracking-[0.05em] text-text-muted font-semibold whitespace-nowrap text-left border-b-2 border-border bg-surface-raised'
const TD = 'px-[0.7rem] py-[0.42rem] text-[0.85rem] whitespace-nowrap border-b border-border'

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Build an ItemSearchQuery seeded from the current URL search params. */
function queryFromParams(searchParams: URLSearchParams): ItemSearchQuery {
  return {
    q:        searchParams.get('q')    ?? '',
    tier:     searchParams.get('tier') ?? '',
    slot:     searchParams.get('slot') ?? '',
    itemType: searchParams.get('type') ?? '',
    cls:      searchParams.get('cls')  ?? '',
    minLevel: searchParams.get('minLv') ?? '',
    maxLevel: searchParams.get('maxLv') ?? '',
    stats: searchParams.getAll('sf').map(sf => {
      const parts = sf.split(':')
      return parts.length === 3
        ? { id: 0, stat: parts[0], op: parts[1] as 'gte' | 'lte', value: parts[2] }
        : { id: 0, stat: sf, op: 'gte' as const, value: '' }
    }),
  }
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ItemSearchPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Snapshot the initial URL params once (for ItemSearchFilters seed)
  const initialQuery = useRef(queryFromParams(searchParams))

  // ── Sort + pagination state ──────────────────────────────────────────────────
  const [sortBy,  setSortBy]  = useState<string>(() => searchParams.get('sort') ?? 'name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(() =>
    (searchParams.get('dir') as 'asc' | 'desc' | null) ?? 'asc',
  )
  const [page, setPage] = useState(() => {
    const p = Number(searchParams.get('page'))
    return p > 0 ? p : 1
  })

  // ── Results state ─────────────────────────────────────────────────────────
  const [results,  setResults]  = useState<ItemSearchResponse | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  // ── Active filter query (from last search or initial URL) ─────────────────
  // Stored so we can re-run on sort/page changes without the child re-submitting.
  const [activeQuery, setActiveQuery] = useState<ItemSearchQuery | null>(() => {
    const q = queryFromParams(searchParams)
    const hasParams = ['q', 'tier', 'slot', 'type', 'cls', 'minLv', 'maxLv'].some(k => searchParams.has(k))
      || searchParams.has('sf')
    return hasParams ? q : null
  })

  // ── Tooltip state ─────────────────────────────────────────────────────────
  const { tooltip, showTip, hideTip, moveTip } = useItemTooltip()

  // ── Keep URL in sync ──────────────────────────────────────────────────────
  // Runs whenever sort, page, or the active query changes (query → activeQuery).
  useEffect(() => {
    if (!activeQuery) return
    const p = new URLSearchParams()
    if (activeQuery.q.trim())        p.set('q',     activeQuery.q.trim())
    if (activeQuery.tier)            p.set('tier',  activeQuery.tier)
    if (activeQuery.slot)            p.set('slot',  activeQuery.slot)
    if (activeQuery.itemType)        p.set('type',  activeQuery.itemType)
    if (activeQuery.cls)             p.set('cls',   activeQuery.cls)
    if (activeQuery.minLevel.trim()) p.set('minLv', activeQuery.minLevel.trim())
    if (activeQuery.maxLevel.trim()) p.set('maxLv', activeQuery.maxLevel.trim())
    if (sortBy !== 'name')           p.set('sort',  sortBy)
    if (sortDir !== 'asc')           p.set('dir',   sortDir)
    if (page > 1)                    p.set('page',  String(page))
    for (const f of activeQuery.stats) {
      if (!f.stat) continue
      const v = f.value.trim()
      p.append('sf', v ? `${f.stat}:${f.op}:${v}` : f.stat)
    }
    setSearchParams(p, { replace: true })
  }, [activeQuery, sortBy, sortDir, page]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Search execution ──────────────────────────────────────────────────────

  async function runSearch(query: ItemSearchQuery, p: number, sb: string, sd: 'asc' | 'desc') {
    const params = new URLSearchParams()
    if (query.q.trim())        params.set('name',      query.q.trim())
    if (query.tier)            params.set('tier',       query.tier)
    if (query.slot)            params.set('slot',       query.slot)
    if (query.itemType)        params.set('item_type',  query.itemType)
    if (query.minLevel.trim()) params.set('min_level',  query.minLevel.trim())
    if (query.maxLevel.trim()) params.set('max_level',  query.maxLevel.trim())
    params.set('sort_by',  sb)
    params.set('sort_dir', sd)
    params.set('page',     String(p))

    if (query.cls && !query.cls.includes(',')) {
      params.set('class_name', query.cls)
    } else if (query.cls && query.cls.includes(',')) {
      params.set('class_name', query.cls.split(',')[0].trim())
    }

    for (const f of query.stats) {
      if (!f.stat) continue
      const v = f.value.trim()
      params.append('stat_filter', v ? `${f.stat}:${f.op}:${v}` : f.stat)
    }

    setLoading(true)
    setError(null)
    setSearched(true)
    try {
      const res = await fetch(`/api/items/search?${params}`, { credentials: 'include' })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`
        setError(detail)
        return
      }
      setResults(await res.json())
      setPage(p)
    } catch {
      setError('Network error — please try again.')
    } finally {
      setLoading(false)
    }
  }

  // ── Handlers exposed to children / pagination ─────────────────────────────

  function handleSearch(query: ItemSearchQuery) {
    setActiveQuery(query)
    runSearch(query, 1, sortBy, sortDir)
  }

  function handleSortChange(newSortBy: string, newSortDir: 'asc' | 'desc') {
    setSortBy(newSortBy)
    setSortDir(newSortDir)
  }

  function handleSortByStat(stat: string) {
    const newDir = sortBy === stat ? (sortDir === 'asc' ? 'desc' : 'asc') : 'desc'
    const newSortBy = stat
    setSortBy(newSortBy)
    setSortDir(newDir)
    if (activeQuery) runSearch(activeQuery, page, newSortBy, newDir)
  }

  function handlePageChange(newPage: number) {
    if (activeQuery) runSearch(activeQuery, newPage, sortBy, sortDir)
  }

  // ── Auto-search on mount when URL already has params ─────────────────────
  const didAutoSearch = useRef(false)
  useEffect(() => {
    if (didAutoSearch.current) return
    didAutoSearch.current = true
    if (activeQuery) runSearch(activeQuery, page, sortBy, sortDir)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const totalPages = results ? Math.ceil(results.total / results.per_page) : 0

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <main className="max-w-[1100px] mx-auto pt-8 px-6 pb-16">
      <h1
        className="font-heading text-[1.9rem] font-bold tracking-[0.06em] mt-4 mx-0 mb-1 inline-block"
        style={{
          background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 40%, var(--gold) 70%, var(--gold-dim) 100%)',
          WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
        }}
      >
        Item Search
      </h1>
      <p className="text-text-muted text-[0.88rem] mb-6">
        Search the local item database by name, quality, slot, class, level and stats.
      </p>

      <ItemSearchFilters
        initial={initialQuery.current}
        onSearch={handleSearch}
        onSortChange={handleSortChange}
        sortBy={sortBy}
        loading={loading}
      />

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && <p className="text-danger mb-4">{error}</p>}

      {/* ── Prompt ─────────────────────────────────────────────────────────── */}
      {!searched && !loading && (
        <p className="text-text-muted text-[0.9rem]">
          Set at least one filter to search.
        </p>
      )}

      {/* ── Results ────────────────────────────────────────────────────────── */}
      {searched && !loading && !error && results && (
        <div onMouseMove={moveTip}>
          <ResultsHeader
            total={results.total} page={page} totalPages={totalPages}
            onPrev={() => handlePageChange(page - 1)}
            onNext={() => handlePageChange(page + 1)}
          />

          {results.total > 0 && <ItemTable
            items={results.results}
            sortBy={sortBy}
            sortDir={sortDir}
            statFilters={activeQuery?.stats ?? []}
            onSortByStat={handleSortByStat}
            onShowTip={showTip}
            onHideTip={hideTip}
          />}

          {totalPages > 1 && (
            <div className="flex justify-end gap-[0.4rem] mt-3">
              <Button variant="secondary" size="sm" onClick={() => handlePageChange(page - 1)} disabled={page <= 1}>← Prev</Button>
              <Button variant="secondary" size="sm" onClick={() => handlePageChange(page + 1)} disabled={page >= totalPages}>Next →</Button>
            </div>
          )}
        </div>
      )}

      {tooltip && <ItemTooltip state={tooltip} />}
    </main>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ResultsHeader({
  total, page, totalPages, onPrev, onNext,
}: { total: number; page: number; totalPages: number; onPrev: () => void; onNext: () => void }) {
  return (
    <div className="flex justify-between items-center mb-[0.6rem] flex-wrap gap-2">
      <span className="text-[0.83rem] text-text-muted">
        {total === 0
          ? 'No items found.'
          : `${total.toLocaleString()} item${total === 1 ? '' : 's'} found`}
        {totalPages > 1 && ` · page ${page} of ${totalPages}`}
      </span>
      {totalPages > 1 && (
        <div className="flex gap-[0.4rem]">
          <Button variant="secondary" size="sm" onClick={onPrev} disabled={page <= 1}>← Prev</Button>
          <Button variant="secondary" size="sm" onClick={onNext} disabled={page >= totalPages}>Next →</Button>
        </div>
      )}
    </div>
  )
}

function StatPills({ stats, highlight }: { stats: string[]; highlight: string[] }) {
  if (!stats.length) return <span className="text-text-muted">—</span>
  const highlightSet = new Set(highlight)
  return (
    <div className="flex flex-wrap gap-[0.2rem]">
      {stats.map(s => (
        <span
          key={s}
          className="inline-block px-[0.35rem] py-[0.1rem] rounded-[3px] text-[0.72rem] border"
          style={{
            background:   highlightSet.has(s)
              ? 'rgba(var(--accent-rgb),0.18)'
              : 'var(--surface-raised)',
            color: highlightSet.has(s)
              ? 'var(--accent)'
              : 'var(--text-muted)',
            borderColor: highlightSet.has(s)
              ? 'rgba(var(--accent-rgb),0.35)'
              : 'var(--border)',
          }}
        >
          {s}
        </span>
      ))}
    </div>
  )
}

function ItemTable({
  items, sortBy, sortDir, statFilters, onSortByStat, onShowTip, onHideTip,
}: {
  items: ItemSearchResult[]
  sortBy: string
  sortDir: 'asc' | 'desc'
  statFilters: StatFilter[]
  onSortByStat: (stat: string) => void
  onShowTip: (itemId: string, e: React.MouseEvent) => void
  onHideTip: () => void
}) {
  const statCols = statFilters.filter(f => f.stat).slice(0, 3)

  return (
    <Card className="overflow-x-auto p-0">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH}>Name</th>
            <th className={TH}>Quality</th>
            <th className={TH}>Slot</th>
            <th className={`${TH} text-right`}>Level</th>
            {statCols.map(f => {
              const active = sortBy === f.stat
              return (
                <th
                  key={f.id}
                  className={`${TH} text-right cursor-pointer select-none ${active ? 'text-gold' : 'text-text-muted'}`}
                  onClick={() => onSortByStat(f.stat)}
                  title={`Sort by ${f.stat}`}
                >
                  {f.stat}&thinsp;{active ? (sortDir === 'desc' ? '↓' : '↑') : '↕'}
                </th>
              )
            })}
            <th className={TH}>Classes</th>
            <th className={TH}>Stats</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr
              key={item.id}
              className="transition-[background] duration-100 cursor-default"
              onMouseEnter={e => {
                e.currentTarget.style.background = 'var(--surface-raised)'
                onShowTip(String(item.id), e)
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = ''
                onHideTip()
              }}
            >
              <td className={TD}>
                <div className="flex items-center gap-[0.45rem]">
                  {item.icon_id ? (
                    <img
                      src={`/icons/${item.icon_id}.png`}
                      alt=""
                      width={28}
                      height={28}
                      className="rounded-[3px] border border-border shrink-0 block"
                      onError={e => { (e.target as HTMLImageElement).style.visibility = 'hidden' }}
                    />
                  ) : (
                    <div className="w-7 h-7 shrink-0" />
                  )}
                  <Link
                    to={`/item/${item.id}`}
                    className="no-underline font-medium"
                    style={{ color: itemRarityColor(item.tier, 'var(--accent)') }}
                  >
                    {item.name}
                  </Link>
                </div>
              </td>
              <td className={`${TD} text-[0.8rem] font-medium`} style={{ color: itemRarityColor(item.tier, 'var(--text-muted)') }}>
                {displayTier(item.tier)}
              </td>
              <td className={`${TD} text-text-muted text-[0.82rem]`}>
                {item.slot ?? (item.item_type ?? '—')}
              </td>
              <td className={`${TD} text-right`}>
                {item.level ?? '—'}
              </td>
              {statCols.map(f => {
                const val = item.stat_values[f.stat]
                const active = sortBy === f.stat
                return (
                  <td
                    key={f.id}
                    className={`${TD} text-right ${active ? 'font-semibold text-gold' : ''}`}
                  >
                    {val != null
                      ? val
                      : <span className="text-text-muted font-normal">—</span>}
                  </td>
                )
              })}
              <td className={`${TD} text-text-muted text-[0.8rem] max-w-[160px] whitespace-normal leading-[1.45]`}>
                {item.class_label
                  ? item.class_label.split(' / ').map((part, i) => (
                      <span key={i} className="block">{part}</span>
                    ))
                  : '—'}
              </td>
              <td className={`${TD} text-[0.78rem] text-text-muted max-w-[260px]`}>
                <StatPills stats={item.stats} highlight={statFilters.map(f => f.stat)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
