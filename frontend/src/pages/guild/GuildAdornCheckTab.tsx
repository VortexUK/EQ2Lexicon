import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSortable } from '../../hooks/useSortable'
import { SortTh } from '../../components/ui/SortTh'
import { TH_CLS, TD_CLS, GuildAdornCheck, MemberAdornStats } from './types'

// ── Constants ─────────────────────────────────────────────────────────────────

// Colour name → display colour for the tooltip border/header
const ADORN_COLOURS: Record<string, string> = {
  White:     '#e2e8f0',
  Yellow:    '#eab308',
  Red:       '#ef4444',
  Blue:      '#60a5fa',
  Turquoise: '#2dd4bf',
  Green:     '#22c55e',
  Orange:    '#f97316',
  Purple:    '#a855f7',
}

/** Consolidate repeated slot names: ["Ring", "Ring", "Ear"] → ["Ring x2", "Ear"] */
function consolidateSlots(slots: string[]): string[] {
  const counts: Record<string, number> = {}
  for (const s of slots) counts[s] = (counts[s] ?? 0) + 1
  return Object.entries(counts).map(([s, n]) => n > 1 ? `${s} ×${n}` : s)
}

/** Adorn fill rate → colour */
function adornCellStyle(filled: number, total: number): React.CSSProperties {
  if (total === 0) return { color: 'var(--text-muted)' }
  const pct = filled / total
  if (pct === 1)   return { color: 'var(--success)' }
  if (pct >= 0.75) return { color: '#84cc16' }
  if (pct >= 0.5)  return { color: '#eab308' }
  if (pct >= 0.25) return { color: '#f97316' }
  return { color: 'var(--danger)' }
}

// ── Types ─────────────────────────────────────────────────────────────────────

type AdornSortKey = string  // 'name' | 'rank' | colour names

interface AdornTooltip {
  x: number
  y: number
  colour: string
  slots: string[]   // already consolidated
}

// ── Adorn sort value ──────────────────────────────────────────────────────────

function adornSortValue(m: MemberAdornStats, key: AdornSortKey): string | number {
  if (key === 'rank') return m.rank_id ?? 9999
  if (key === 'name') return m.name.toLowerCase()
  const s = m.adorns[key]
  if (!s || s.total === 0) return -1
  return s.filled / s.total
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface GuildAdornCheckTabProps {
  data: GuildAdornCheck
  filter: string
  hiddenRanks: Set<string>
  myChars: Set<string>
}

// ── Component ─────────────────────────────────────────────────────────────────

export function GuildAdornCheckTab({ data, filter, hiddenRanks, myChars }: GuildAdornCheckTabProps) {
  const [tooltip, setTooltip] = useState<AdornTooltip | null>(null)

  // Only show colour columns where at least one member has a filled adorn of that colour
  const activeColors = useMemo(() =>
    data.colors.filter(c =>
      data.members.some(m => (m.adorns[c]?.filled ?? 0) > 0)
    ),
  [data])

  const filteredMembers = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return data.members.filter(m => {
      if (m.rank && hiddenRanks.has(m.rank)) return false
      if (!q) return true
      return m.name.toLowerCase().includes(q) || (m.rank ?? '').toLowerCase().includes(q)
    })
  }, [data.members, filter, hiddenRanks])

  const { sorted, sortKey, sortDir, handleSort } = useSortable<MemberAdornStats, AdornSortKey>(
    filteredMembers,
    adornSortValue,
    'rank',
    'asc',
    (k) => (k === 'name' || k === 'rank') ? 'asc' : 'desc',
  )

  function showTooltip(e: React.MouseEvent<HTMLTableCellElement>, colour: string, rawSlots: string[]) {
    if (rawSlots.length === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    setTooltip({
      x: Math.min(rect.left + rect.width / 2, window.innerWidth - 160),
      y: rect.top - 6,
      colour,
      slots: consolidateSlots(rawSlots),
    })
  }

  return (
    <>
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b-2 border-border bg-surface-raised">
            <SortTh sortKey="name" active={sortKey} dir={sortDir} onSort={handleSort} className={`${TH_CLS} text-left`}>
              Name
            </SortTh>
            <SortTh sortKey="rank" active={sortKey} dir={sortDir} onSort={handleSort} className={`${TH_CLS} text-left`}>
              Rank
            </SortTh>
            {activeColors.map(c => (
              <SortTh
                key={c}
                sortKey={c}
                active={sortKey}
                dir={sortDir}
                onSort={handleSort}
                className={`${TH_CLS} text-right`}
              >
                {c}
              </SortTh>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(m => (
            <tr key={m.name} className="border-b border-border" style={{ background: myChars.has(m.name.toLowerCase()) ? 'rgba(var(--gold-rgb), 0.06)' : undefined }}>
              <td className={TD_CLS}>
                <Link to={`/character/${encodeURIComponent(m.name)}`}
                  className="text-gold no-underline font-medium">
                  {m.name}
                </Link>
                {myChars.has(m.name.toLowerCase()) && (
                  <span className="ml-[0.4rem] text-[0.65rem] text-gold align-middle">★</span>
                )}
              </td>
              <td className={`${TD_CLS} text-text-muted text-[0.85rem]`}>{m.rank ?? '—'}</td>
              {activeColors.map(c => {
                const stats = m.adorns[c]
                const missingSlots = m.missing?.[c] ?? []
                if (!stats) return (
                  <td key={c} className={`${TD_CLS} text-right text-text-muted`}>—</td>
                )
                return (
                  <td
                    key={c}
                    onMouseEnter={missingSlots.length > 0 ? e => showTooltip(e, c, missingSlots) : undefined}
                    onMouseLeave={missingSlots.length > 0 ? () => setTooltip(null) : undefined}
                    onClick={missingSlots.length > 0 ? e => showTooltip(e, c, missingSlots) : undefined}
                    className={`${TD_CLS} text-right font-medium`}
                    style={{
                      cursor: missingSlots.length > 0 ? 'default' : undefined,
                      ...adornCellStyle(stats.filled, stats.total),
                    }}
                  >
                    {stats.filled}/{stats.total}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>

      {/* Missing adorn tooltip — fixed so it escapes the scrollable container */}
      {tooltip && (
        <div
          className="fixed -translate-x-1/2 -translate-y-full rounded-sm2 px-[0.8rem] py-2 z-[9999] pointer-events-none max-w-[220px]"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            background: '#1a1d26',
            border: `1px solid ${ADORN_COLOURS[tooltip.colour] ?? 'var(--border)'}`,
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          }}
        >
          <div
            className="text-[0.68rem] uppercase tracking-[0.06em] font-bold mb-1.5"
            style={{ color: ADORN_COLOURS[tooltip.colour] ?? 'var(--text-muted)' }}
          >
            Missing {tooltip.colour}
          </div>
          {tooltip.slots.map((s, i) => (
            <div key={i} className="text-[0.83rem] text-text leading-[1.65]">
              {s}
            </div>
          ))}
        </div>
      )}
    </>
  )
}
