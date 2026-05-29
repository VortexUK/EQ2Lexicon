import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSortable } from '../../hooks/useSortable'
import { SortTh } from '../../components/ui/SortTh'
import { SPELL_TIER_COLOURS as TIER_COLOURS } from '../../spellConstants'
import { TH_CLS, TD_CLS, GuildSpellCheck, MemberSpellTiers } from './types'

// ── Constants ─────────────────────────────────────────────────────────────────

const TIER_SHORT: Record<string, string> = {
  Apprentice: 'App', Journeyman: 'Journ', Adept: 'Adept',
  Expert: 'Expert', Master: 'Master', Grandmaster: 'GM',
}

// ── Types ─────────────────────────────────────────────────────────────────────

type SpellSortKey = string  // 'name' | 'rank' | 'total' | tier names

interface SpellTooltip {
  x: number
  y: number
  tier: string
  names: string[]
}

// ── Spell sort value ──────────────────────────────────────────────────────────

function spellSortValue(m: MemberSpellTiers, key: SpellSortKey): string | number {
  if (key === 'rank')  return m.rank_id ?? 9999
  if (key === 'name')  return m.name.toLowerCase()
  if (key === 'total') return m.total
  return m.tiers[key] ?? 0
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface GuildSpellCheckTabProps {
  data: GuildSpellCheck
  filter: string
  hiddenRanks: Set<string>
  myChars: Set<string>
}

// ── Component ─────────────────────────────────────────────────────────────────

export function GuildSpellCheckTab({ data, filter, hiddenRanks, myChars }: GuildSpellCheckTabProps) {
  const [tooltip, setTooltip] = useState<SpellTooltip | null>(null)

  const filteredMembers = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return data.members.filter(m => {
      if (m.rank && hiddenRanks.has(m.rank)) return false
      if (!q) return true
      return m.name.toLowerCase().includes(q) || (m.rank ?? '').toLowerCase().includes(q)
    })
  }, [data.members, filter, hiddenRanks])

  const { sorted, sortKey, sortDir, handleSort } = useSortable<MemberSpellTiers, SpellSortKey>(
    filteredMembers,
    spellSortValue,
    'rank',
    'asc',
    (k) => (k === 'name' || k === 'rank') ? 'asc' : 'desc',
  )

  function showTooltip(e: React.MouseEvent<HTMLTableCellElement>, tier: string, names: string[]) {
    if (names.length === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    setTooltip({
      x: Math.min(rect.left + rect.width / 2, window.innerWidth - 160),
      y: rect.top - 6,
      tier,
      names,
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
            {data.tiers.map(t => {
              const tierColour = TIER_COLOURS[t]?.text
              // When this column is active, text-gold class from SortTh takes over;
              // when inactive show the tier colour via inline style.
              const inactiveStyle = sortKey !== t && tierColour
                ? { color: tierColour }
                : undefined
              return (
                <SortTh
                  key={t}
                  sortKey={t}
                  active={sortKey}
                  dir={sortDir}
                  onSort={handleSort}
                  className={`${TH_CLS} text-right`}
                  style={inactiveStyle}
                >
                  {TIER_SHORT[t] ?? t}
                </SortTh>
              )
            })}
            <SortTh sortKey="total" active={sortKey} dir={sortDir} onSort={handleSort} className={`${TH_CLS} text-right`}>
              Total
            </SortTh>
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
              {data.tiers.map(t => {
                const count = m.tiers[t] ?? 0
                const tc = TIER_COLOURS[t]
                const names = m.spell_names?.[t] ?? []
                return (
                  <td
                    key={t}
                    onMouseEnter={count > 0 ? e => showTooltip(e, t, names) : undefined}
                    onMouseLeave={count > 0 ? () => setTooltip(null) : undefined}
                    onClick={count > 0 ? e => showTooltip(e, t, names) : undefined}
                    className={`${TD_CLS} text-right`}
                    style={{
                      color: count > 0 ? (tc?.text ?? 'var(--text)') : 'var(--text-muted)',
                      background: count > 0 ? (tc?.bg ?? 'transparent') : 'transparent',
                      fontWeight: count > 0 ? 500 : 400,
                      cursor: count > 0 ? 'default' : undefined,
                    }}
                  >
                    {count > 0 ? count : '—'}
                  </td>
                )
              })}
              <td className={`${TD_CLS} text-right font-semibold text-text`}>
                {m.total}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Spell name tooltip — fixed so it escapes the scrollable table container */}
      {tooltip && (
        <div
          className="fixed -translate-x-1/2 -translate-y-full rounded-sm2 px-[0.8rem] py-2 z-[9999] pointer-events-none max-w-[280px]"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            background: '#1a1d26',
            border: `1px solid ${TIER_COLOURS[tooltip.tier]?.text ?? 'var(--border)'}`,
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          }}
        >
          <div
            className="text-[0.68rem] uppercase tracking-[0.06em] font-bold mb-1.5"
            style={{ color: TIER_COLOURS[tooltip.tier]?.text ?? 'var(--text-muted)' }}
          >
            {tooltip.tier} · {tooltip.names.length}
          </div>
          {tooltip.names.map((name, i) => (
            <div key={i} className="text-[0.83rem] text-text leading-[1.65]">
              {name}
            </div>
          ))}
        </div>
      )}
    </>
  )
}
