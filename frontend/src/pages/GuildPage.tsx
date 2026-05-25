import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import Breadcrumb from '../components/Breadcrumb'
import { useClaim } from '../hooks/useClaim'
import { useAuth, discordAvatarUrl } from '../hooks/useAuth'
import { useClasses } from '../useClasses'
import { SPELL_TIER_COLOURS as TIER_COLOURS } from '../spellConstants'
import { Button, Card } from '../components/ui'

// ── Types ─────────────────────────────────────────────────────────────────────

interface GuildMember {
  name: string
  level: number | null
  cls: string | null
  ts_class: string | null
  ts_level: number | null
  aa_level: number | null
  deity: string | null
  rank: string | null
  rank_id: number | null
  guild_status: number | null
  played_time: number | null
}

interface GuildData {
  name: string
  world: string
  members: GuildMember[]
}

interface GuildInfo {
  name: string
  world: string
  dateformed: number | null
  description: string | null
  alignment: string | null
  type: string | null
  level: number | null
  members: number | null
  accounts: number | null
  achievement_count: number
}

interface MemberSpellTiers {
  name: string
  rank: string | null
  rank_id: number | null
  tiers: Record<string, number>
  total: number
  spell_names: Record<string, string[]>
}

interface GuildSpellCheck {
  guild_name: string
  world: string
  tiers: string[]
  members: MemberSpellTiers[]
}

interface AdornColorStats {
  filled: number
  total: number
}

interface MemberAdornStats {
  name: string
  rank: string | null
  rank_id: number | null
  adorns: Record<string, AdornColorStats>
  missing: Record<string, string[]>
}

interface GuildAdornCheck {
  guild_name: string
  world: string
  colors: string[]
  members: MemberAdornStats[]
}

interface GuildClaimItem {
  id: number
  discord_id: string
  discord_name: string
  avatar: string | null
  character_name: string
  requested_at: number
  is_own: boolean
}

interface ItemWatchEntry {
  id: number
  character_name: string
  item_id: number
  item_name: string
  added_by_name: string
  added_at: number
  first_seen_at: number | null
  last_seen_at: number | null
  last_checked_at: number | null
}

type Tab = 'roster' | 'spells' | 'adorns' | 'claims' | 'watch'

// ── Style helpers ─────────────────────────────────────────────────────────────

// Adorn fill rate → colour
function adornCellStyle(filled: number, total: number): React.CSSProperties {
  if (total === 0) return { color: 'var(--text-muted)' }
  const pct = filled / total
  if (pct === 1)   return { color: '#22c55e' }
  if (pct >= 0.75) return { color: '#84cc16' }
  if (pct >= 0.5)  return { color: '#eab308' }
  if (pct >= 0.25) return { color: '#f97316' }
  return { color: '#ef4444' }
}

// ── Shared table styles ───────────────────────────────────────────────────────

// Invariant table-cell utilities. Dynamic bits (active colour, alignment,
// per-cell colour/background) stay inline at each call site.
const TH_CLS = 'px-[0.6rem] py-2 text-[0.72rem] uppercase tracking-[0.05em] font-semibold whitespace-nowrap'
const TD_CLS = 'px-[0.6rem] py-[0.42rem] text-[0.88rem] whitespace-nowrap'

// ── Guild info stat chip ──────────────────────────────────────────────────────

function InfoStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-[0.1rem]">
      <span className="text-[0.68rem] uppercase tracking-[0.07em] text-text-muted">
        {label}
      </span>
      <span className="text-[0.92rem] text-text font-medium">
        {value}
      </span>
    </div>
  )
}

// ── Tab button ────────────────────────────────────────────────────────────────

function TabBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="px-4 py-[0.4rem] rounded-[6px] border cursor-pointer text-[0.88rem]"
      style={{
        borderColor: active ? 'var(--accent)' : 'var(--border)',
        background: active ? 'rgba(var(--accent-rgb,99,210,130),0.12)' : 'var(--surface)',
        color: active ? 'var(--accent)' : 'var(--text-muted)',
        fontWeight: active ? 600 : 400,
      }}
    >
      {label}
    </button>
  )
}

// ── Roster table ──────────────────────────────────────────────────────────────

function fmtPlayTime(secs: number | null): string {
  if (secs == null) return '—'
  const h = Math.floor(secs / 3600)
  if (h === 0) return '<1h'
  return h.toLocaleString() + 'h'
}

function fmtGuildStatus(pts: number | null): string {
  if (pts == null) return '—'
  return pts.toLocaleString()
}

type RosterSortKey = 'rank' | 'name' | 'level' | 'aa' | 'ts_level' | 'deity' | 'guild_status' | 'played_time'

const ROSTER_COLS: { label: string; key: RosterSortKey; align?: 'right' }[] = [
  { label: 'Name',             key: 'name'         },
  { label: 'Rank',             key: 'rank'         },
  { label: 'Class (Level)',    key: 'level'        },
  { label: 'AA',               key: 'aa',          align: 'right' },
  { label: 'Tradeskill (Lvl)', key: 'ts_level'     },
  { label: 'Deity',            key: 'deity'        },
  { label: 'Guild Status',     key: 'guild_status', align: 'right' },
  { label: 'Play Time',        key: 'played_time',  align: 'right' },
]

function rosterSortValue(m: GuildMember, key: RosterSortKey): string | number {
  switch (key) {
    case 'rank':         return m.rank_id ?? 9999
    case 'name':         return m.name.toLowerCase()
    case 'level':        return m.level ?? -1
    case 'aa':           return m.aa_level ?? -1
    case 'ts_level':     return m.ts_level ?? -1
    case 'deity':        return (m.deity ?? '').toLowerCase()
    case 'guild_status': return m.guild_status ?? -1
    case 'played_time':  return m.played_time ?? -1
  }
}

function RosterTable({ members, filter, hiddenRanks, myChars }: { members: GuildMember[]; filter: string; hiddenRanks: Set<string>; myChars: Set<string> }) {
  const { colourFor } = useClasses()
  const [sortKey, setSortKey] = useState<RosterSortKey>('rank')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  function handleSort(key: RosterSortKey) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      // Numeric columns default to descending (highest first); others ascending
      setSortDir(['level', 'aa', 'ts_level', 'guild_status', 'played_time'].includes(key) ? 'desc' : 'asc')
    }
  }

  const sorted = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const base = members.filter(m => {
      if (m.rank && hiddenRanks.has(m.rank)) return false
      if (!q) return true
      return m.name.toLowerCase().includes(q) ||
        (m.cls ?? '').toLowerCase().includes(q) ||
        (m.rank ?? '').toLowerCase().includes(q)
    })

    base.sort((a, b) => {
      const av = rosterSortValue(a, sortKey)
      const bv = rosterSortValue(b, sortKey)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
    return base
  }, [members, filter, hiddenRanks, sortKey, sortDir])

  return (
    <table className="w-full border-collapse">
      <thead>
        <tr className="border-b-2 border-border bg-surface-raised">
          {ROSTER_COLS.map(col => {
            const active = sortKey === col.key
            return (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                className={`${TH_CLS} cursor-pointer select-none`}
                style={{
                  textAlign: col.align ?? 'left',
                  color: active ? 'var(--accent)' : 'var(--text-muted)',
                }}
              >
                {col.label}
                <span className="ml-[0.3rem] text-[0.65rem]" style={{ opacity: active ? 1 : 0.3 }}>
                  {active ? (sortDir === 'asc' ? '▲' : '▼') : '▲'}
                </span>
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.length === 0 ? (
          <tr><td colSpan={8} className={`${TD_CLS} text-center text-text-muted`}>No members match your filter.</td></tr>
        ) : sorted.map(m => {
          const clsLabel = m.cls
            ? m.level != null ? `${m.cls} (${m.level})` : m.cls
            : '—'
          const tsLabel = m.ts_class
            ? m.ts_level != null
              ? `${m.ts_class.charAt(0).toUpperCase()}${m.ts_class.slice(1)} (${m.ts_level})`
              : m.ts_class
            : '—'
          return (
            <tr key={m.name} className="border-b border-border" style={{ background: myChars.has(m.name.toLowerCase()) ? 'rgba(200,169,110,0.06)' : undefined }}>
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
              <td className={TD_CLS} style={{ color: m.cls ? colourFor(m.cls, 'var(--text)') : 'var(--text-muted)' }}>{clsLabel}</td>
              <td className={`${TD_CLS} text-right text-text-muted`}>{m.aa_level ?? '—'}</td>
              <td className={`${TD_CLS} text-text-muted`}>{tsLabel}</td>
              <td className={`${TD_CLS} text-text-muted text-[0.82rem]`}>{m.deity ?? '—'}</td>
              <td className={`${TD_CLS} text-right text-text-muted text-[0.82rem]`}>{fmtGuildStatus(m.guild_status)}</td>
              <td className={`${TD_CLS} text-right text-text-muted text-[0.82rem]`}>{fmtPlayTime(m.played_time)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Spell check table ─────────────────────────────────────────────────────────

const TIER_SHORT: Record<string, string> = {
  Apprentice: 'App', Journeyman: 'Journ', Adept: 'Adept',
  Expert: 'Expert', Master: 'Master', Grandmaster: 'GM',
}

interface SpellTooltip {
  x: number
  y: number
  tier: string
  names: string[]
}

function SpellCheckTable({ data, filter, hiddenRanks, myChars }: { data: GuildSpellCheck; filter: string; hiddenRanks: Set<string>; myChars: Set<string> }) {
  const [sortKey, setSortKey] = useState<string>('rank')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [tooltip, setTooltip] = useState<SpellTooltip | null>(null)

  function handleSort(key: string) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'name' || key === 'rank' ? 'asc' : 'desc')
    }
  }

  function sortValue(m: MemberSpellTiers): string | number {
    if (sortKey === 'rank')  return m.rank_id ?? 9999
    if (sortKey === 'name')  return m.name.toLowerCase()
    if (sortKey === 'total') return m.total
    return m.tiers[sortKey] ?? 0
  }

  const sorted = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const base = data.members.filter(m => {
      if (m.rank && hiddenRanks.has(m.rank)) return false
      if (!q) return true
      return m.name.toLowerCase().includes(q) || (m.rank ?? '').toLowerCase().includes(q)
    })

    base.sort((a, b) => {
      const av = sortValue(a), bv = sortValue(b)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
    return base
  }, [data.members, filter, hiddenRanks, sortKey, sortDir])

  function SortTh({ label, colKey, align, color }: { label: string; colKey: string; align?: 'right'; color?: string }) {
    const active = sortKey === colKey
    return (
      <th
        onClick={() => handleSort(colKey)}
        className={`${TH_CLS} cursor-pointer select-none`}
        style={{
          textAlign: align ?? 'left',
          color: active ? 'var(--accent)' : (color ?? 'var(--text-muted)'),
        }}
      >
        {label}
        <span className="ml-[0.3rem] text-[0.65rem]" style={{ opacity: active ? 1 : 0.3 }}>
          {active ? (sortDir === 'asc' ? '▲' : '▼') : '▲'}
        </span>
      </th>
    )
  }

  function showTooltip(e: React.MouseEvent<HTMLTableCellElement>, tier: string, names: string[]) {
    if (names.length === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    // Position above the cell, centred horizontally
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
            <SortTh label="Name"  colKey="name" />
            <SortTh label="Rank"  colKey="rank" />
            {data.tiers.map(t => (
              <SortTh key={t} label={TIER_SHORT[t] ?? t} colKey={t} align="right" color={TIER_COLOURS[t]?.text} />
            ))}
            <SortTh label="Total" colKey="total" align="right" />
          </tr>
        </thead>
        <tbody>
          {sorted.map(m => (
            <tr key={m.name} className="border-b border-border" style={{ background: myChars.has(m.name.toLowerCase()) ? 'rgba(200,169,110,0.06)' : undefined }}>
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
          className="fixed -translate-x-1/2 -translate-y-full rounded-[6px] px-[0.8rem] py-2 z-[9999] pointer-events-none max-w-[280px]"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            background: '#1a1d26',
            border: `1px solid ${TIER_COLOURS[tooltip.tier]?.text ?? 'var(--border)'}`,
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          }}
        >
          <div
            className="text-[0.68rem] uppercase tracking-[0.06em] font-bold mb-[0.35rem]"
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

// ── Adorn check table ─────────────────────────────────────────────────────────

// Colour name → a display colour for the tooltip border/header
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

interface AdornTooltip {
  x: number
  y: number
  colour: string
  slots: string[]   // already consolidated
}

function AdornCheckTable({ data, filter, hiddenRanks, myChars }: { data: GuildAdornCheck; filter: string; hiddenRanks: Set<string>; myChars: Set<string> }) {
  const [sortKey, setSortKey] = useState<string>('rank')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [tooltip, setTooltip] = useState<AdornTooltip | null>(null)

  function handleSort(key: string) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'name' || key === 'rank' ? 'asc' : 'desc')
    }
  }

  // Only show colour columns where at least one member has a filled adorn of that colour
  const activeColors = useMemo(() =>
    data.colors.filter(c =>
      data.members.some(m => (m.adorns[c]?.filled ?? 0) > 0)
    ),
  [data])

  function sortValue(m: MemberAdornStats): string | number {
    if (sortKey === 'rank') return m.rank_id ?? 9999
    if (sortKey === 'name') return m.name.toLowerCase()
    const s = m.adorns[sortKey]
    if (!s || s.total === 0) return -1
    return s.filled / s.total
  }

  const sorted = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const base = data.members.filter(m => {
      if (m.rank && hiddenRanks.has(m.rank)) return false
      if (!q) return true
      return m.name.toLowerCase().includes(q) || (m.rank ?? '').toLowerCase().includes(q)
    })

    base.sort((a, b) => {
      const av = sortValue(a), bv = sortValue(b)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
    return base
  }, [data.members, filter, hiddenRanks, sortKey, sortDir])

  function SortTh({ label, colKey, align }: { label: string; colKey: string; align?: 'right' }) {
    const active = sortKey === colKey
    return (
      <th
        onClick={() => handleSort(colKey)}
        className={`${TH_CLS} cursor-pointer select-none`}
        style={{
          textAlign: align ?? 'left',
          color: active ? 'var(--accent)' : 'var(--text-muted)',
        }}
      >
        {label}
        <span className="ml-[0.3rem] text-[0.65rem]" style={{ opacity: active ? 1 : 0.3 }}>
          {active ? (sortDir === 'asc' ? '▲' : '▼') : '▲'}
        </span>
      </th>
    )
  }

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
            <SortTh label="Name" colKey="name" />
            <SortTh label="Rank" colKey="rank" />
            {activeColors.map(c => (
              <SortTh key={c} label={c} colKey={c} align="right" />
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(m => (
            <tr key={m.name} className="border-b border-border" style={{ background: myChars.has(m.name.toLowerCase()) ? 'rgba(200,169,110,0.06)' : undefined }}>
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
          className="fixed -translate-x-1/2 -translate-y-full rounded-[6px] px-[0.8rem] py-2 z-[9999] pointer-events-none max-w-[220px]"
          style={{
            left: tooltip.x,
            top: tooltip.y,
            background: '#1a1d26',
            border: `1px solid ${ADORN_COLOURS[tooltip.colour] ?? 'var(--border)'}`,
            boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          }}
        >
          <div
            className="text-[0.68rem] uppercase tracking-[0.06em] font-bold mb-[0.35rem]"
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

// ── Claim requests tab (officers only) ───────────────────────────────────────


function ClaimRequestsTab({
  guildName,
  currentDiscordId,
}: {
  guildName: string
  currentDiscordId: string
}) {
  const [claims, setClaims]     = useState<GuildClaimItem[] | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [busy, setBusy]         = useState<number | null>(null)   // claim ID being actioned
  const [rejectId, setRejectId] = useState<number | null>(null)   // claim ID open for reject note
  const [rejectNote, setRejectNote] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/claims`, { credentials: 'include' })
      .then(async res => {
        if (!res.ok) { setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        setClaims(await res.json())
      })
      .catch(() => setError('Network error — please try again.'))
      .finally(() => setLoading(false))
  }, [guildName])

  async function handleApprove(id: number) {
    setBusy(id)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/claims/${id}/approve`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail ?? 'Failed'); return }
      setClaims(prev => prev ? prev.filter(c => c.id !== id) : prev)
    } finally { setBusy(null) }
  }

  async function handleReject(id: number, note: string) {
    setBusy(id)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/claims/${id}/reject`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: note.trim() || null }),
      })
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail ?? 'Failed'); return }
      setClaims(prev => prev ? prev.filter(c => c.id !== id) : prev)
      setRejectId(null)
      setRejectNote('')
    } finally { setBusy(null) }
  }

  if (loading) return <p className="text-text-muted p-4">Loading claim requests…</p>
  if (error)   return <p className="text-danger p-4">{error}</p>
  if (!claims) return null

  if (claims.length === 0) {
    return (
      <div className="p-8 text-center text-text-muted">
        No pending claim requests for this guild.
      </div>
    )
  }

  return (
    <div className="px-4 py-3">
      {claims.map(c => {
        const isOwn    = c.discord_id === currentDiscordId
        const isBusy   = busy === c.id
        const rejecting = rejectId === c.id
        const age = Math.floor((Date.now() / 1000 - c.requested_at) / 3600)
        const ageStr = age < 1 ? 'just now' : age < 24 ? `${age}h ago` : `${Math.floor(age / 24)}d ago`

        return (
          <div key={c.id} className="flex items-start gap-[0.85rem] py-[0.85rem] border-b border-border">
            {/* Discord avatar */}
            <img
              src={discordAvatarUrl(c.discord_id, c.avatar)}
              alt=""
              className="w-[38px] h-[38px] rounded-full shrink-0 mt-0.5"
            />

            {/* Info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="font-semibold text-text">{c.discord_name}</span>
                <span className="text-text-muted text-[0.8rem]">is claiming</span>
                <span className="text-gold font-semibold">{c.character_name}</span>
                {isOwn && (
                  <span
                    className="text-[0.68rem] font-bold px-[0.4rem] py-[0.1rem] rounded-sm text-gold uppercase tracking-[0.05em]"
                    style={{ background: 'rgba(200,169,110,0.15)', border: '1px solid rgba(200,169,110,0.3)' }}
                  >Your claim</span>
                )}
              </div>
              <div className="text-[0.78rem] text-text-muted mt-[0.15rem]">
                Submitted {ageStr}
              </div>

              {/* Reject note input */}
              {rejecting && (
                <div className="mt-[0.6rem] flex gap-[0.4rem] flex-wrap">
                  <input
                    type="text"
                    placeholder="Reason (optional)…"
                    value={rejectNote}
                    onChange={e => setRejectNote(e.target.value)}
                    className="flex-1 min-w-[160px] text-[0.85rem]"
                    autoFocus
                  />
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => handleReject(c.id, rejectNote)}
                    disabled={isBusy}
                  >
                    {isBusy ? '…' : 'Confirm reject'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => { setRejectId(null); setRejectNote('') }}
                  >
                    Cancel
                  </Button>
                </div>
              )}
            </div>

            {/* Action buttons — hidden for own claims and while reject form is open */}
            {!isOwn && !rejecting && (
              <div className="flex gap-[0.4rem] shrink-0">
                <button
                  onClick={() => handleApprove(c.id)}
                  disabled={isBusy}
                  className="px-[0.85rem] py-[0.3rem] rounded-[5px] cursor-pointer text-[0.85rem] font-semibold"
                  style={{
                    background: 'rgba(34,197,94,0.15)', color: '#22c55e',
                    border: '1px solid rgba(34,197,94,0.35)',
                  }}
                >
                  {isBusy ? '…' : 'Approve'}
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setRejectId(c.id); setRejectNote('') }}
                  disabled={isBusy}
                >
                  Reject
                </Button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Item watch tab (officers only) ───────────────────────────────────────────

function watchStatus(w: ItemWatchEntry): { icon: string; label: string; colour: string } {
  if (w.last_checked_at === null) {
    return { icon: '⏳', label: 'Not yet checked', colour: 'var(--text-muted)' }
  }
  if (w.last_seen_at !== null && w.last_seen_at === w.last_checked_at) {
    return { icon: '🟢', label: 'Currently wearing', colour: '#22c55e' }
  }
  if (w.last_seen_at !== null) {
    const ago = Math.floor((Date.now() / 1000 - w.last_seen_at) / 3600)
    const label = ago < 1 ? 'last seen just now' : ago < 24 ? `last seen ${ago}h ago` : `last seen ${Math.floor(ago / 24)}d ago`
    return { icon: '🟡', label, colour: '#eab308' }
  }
  return { icon: '🔴', label: 'Never seen wearing it', colour: '#ef4444' }
}

function relativeTime(unix: number): string {
  const diff = Math.floor((Date.now() / 1000 - unix) / 3600)
  if (diff < 1)  return 'just now'
  if (diff < 24) return `${diff}h ago`
  return `${Math.floor(diff / 24)}d ago`
}

function ItemWatchTab({ guildName }: { guildName: string }) {
  const [watches, setWatches]   = useState<ItemWatchEntry[] | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [charInput, setCharInput] = useState('')
  const [itemInput, setItemInput] = useState('')
  const [adding, setAdding]     = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [removing, setRemoving] = useState<number | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/item-watch`, { credentials: 'include' })
      .then(async res => {
        if (!res.ok) { setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        setWatches(await res.json())
      })
      .catch(() => setError('Network error — please try again.'))
      .finally(() => setLoading(false))
  }, [guildName])

  async function handleAdd() {
    const char = charInput.trim()
    const item = itemInput.trim()
    if (!char || !item) return
    setAdding(true)
    setAddError(null)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/item-watch`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_name: char, item_name: item }),
      })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail ?? 'Failed to add watch'
        setAddError(detail)
        return
      }
      const entry: ItemWatchEntry = await res.json()
      setWatches(prev => prev ? [entry, ...prev] : [entry])
      setCharInput('')
      setItemInput('')
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(id: number) {
    setRemoving(id)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/item-watch/${id}`, {
        method: 'DELETE', credentials: 'include',
      })
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail ?? 'Failed'); return }
      setWatches(prev => prev ? prev.filter(w => w.id !== id) : prev)
    } finally {
      setRemoving(null)
    }
  }

  if (loading) return <p className="text-text-muted p-4">Loading item watches…</p>
  if (error)   return <p className="text-danger p-4">{error}</p>

  return (
    <div className="px-4 py-[0.85rem]">

      {/* Add form */}
      <div className="flex gap-2 flex-wrap items-start mb-[1.1rem] pb-4 border-b border-border">
        <div className="flex flex-col gap-[0.2rem]">
          <label className="text-[0.7rem] text-text-muted uppercase tracking-[0.06em]">Character</label>
          <input
            type="text"
            placeholder="e.g. Sihtric"
            value={charInput}
            onChange={e => setCharInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            className="w-[160px] text-[0.88rem]"
          />
        </div>
        <div className="flex flex-col gap-[0.2rem]">
          <label className="text-[0.7rem] text-text-muted uppercase tracking-[0.06em]">Item name</label>
          <input
            type="text"
            placeholder="e.g. Faded Black Hood"
            value={itemInput}
            onChange={e => setItemInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            className="w-[240px] text-[0.88rem]"
          />
        </div>
        <div className="flex flex-col gap-[0.2rem]">
          <label className="text-[0.7rem] text-transparent select-none">_</label>
          <button
            onClick={handleAdd}
            disabled={adding || !charInput.trim() || !itemInput.trim()}
            className="px-4 py-[0.42rem] rounded-[6px] cursor-pointer text-[0.88rem] font-semibold"
            style={{
              background: 'rgba(var(--accent-rgb,99,210,130),0.15)',
              color: 'var(--accent)',
              border: '1px solid rgba(var(--accent-rgb,99,210,130),0.35)',
              opacity: adding || !charInput.trim() || !itemInput.trim() ? 0.5 : 1,
            }}
          >
            {adding ? 'Adding…' : '+ Add Watch'}
          </button>
        </div>
        {addError && (
          <div className="w-full text-danger text-[0.83rem] mt-[0.2rem]">
            {addError}
          </div>
        )}
      </div>

      {/* Watch list */}
      {watches && watches.length === 0 ? (
        <div className="text-center text-text-muted py-6">
          No items being watched for this guild yet.
        </div>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b-2 border-border bg-surface-raised">
              <th className={`${TH_CLS} text-text-muted text-left`}>Item</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Character</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Added by</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Added</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Status</th>
              <th className={`${TH_CLS} text-text-muted text-left w-12`}></th>
            </tr>
          </thead>
          <tbody>
            {(watches ?? []).map(w => {
              const { icon, label, colour } = watchStatus(w)
              return (
                <tr key={w.id} className="border-b border-border">
                  <td className={`${TD_CLS} font-medium text-text`}>{w.item_name}</td>
                  <td className={`${TD_CLS} text-gold`}>{w.character_name}</td>
                  <td className={`${TD_CLS} text-text-muted text-[0.82rem]`}>{w.added_by_name}</td>
                  <td className={`${TD_CLS} text-text-muted text-[0.82rem]`}>{relativeTime(w.added_at)}</td>
                  <td className={`${TD_CLS} text-[0.85rem] whitespace-nowrap`} style={{ color: colour }}>
                    {icon} {label}
                  </td>
                  <td className={`${TD_CLS} text-right px-2 py-[0.3rem]`}>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRemove(w.id)}
                      disabled={removing === w.id}
                      title="Remove watch"
                    >
                      {removing === w.id ? '…' : '✕'}
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function GuildPage() {
  const { guildName } = useParams<{ guildName: string }>()
  const claimState = useClaim()
  const auth = useAuth()

  const myChars = useMemo<Set<string>>(() => {
    if (claimState.status !== 'ready') return new Set()
    return new Set(claimState.data.approved.map(c => c.character_name.toLowerCase()))
  }, [claimState])

  const [isOfficer, setIsOfficer] = useState(false)

  const [tab, setTab] = useState<Tab>('roster')
  const [filter, setFilter] = useState('')
  const [hiddenRanks, setHiddenRanks] = useState<Set<string>>(new Set())

  // Guild info state
  const [info, setInfo] = useState<GuildInfo | null>(null)

  // Roster state
  const [roster, setRoster] = useState<GuildData | null>(null)
  const [rosterError, setRosterError] = useState<string | null>(null)
  const [rosterLoading, setRosterLoading] = useState(true)

  // Spell check state
  const [spells, setSpells] = useState<GuildSpellCheck | null>(null)
  const [spellsError, setSpellsError] = useState<string | null>(null)
  const [spellsLoading, setSpellsLoading] = useState(false)

  // Adorn check state
  const [adorns, setAdorns] = useState<GuildAdornCheck | null>(null)
  const [adornsError, setAdornsError] = useState<string | null>(null)
  const [adornsLoading, setAdornsLoading] = useState(false)

  // Load roster + info + officer status on mount
  useEffect(() => {
    if (!guildName) return
    setRosterLoading(true)
    setRosterError(null)

    Promise.all([
      fetch(`/api/guild/${encodeURIComponent(guildName)}`, { credentials: 'include' }),
      fetch(`/api/guild/${encodeURIComponent(guildName)}/info`, { credentials: 'include' }),
      fetch(`/api/guild/${encodeURIComponent(guildName)}/officer-status`, { credentials: 'include' }),
    ]).then(async ([rosterRes, infoRes, officerRes]) => {
      if (!rosterRes.ok) {
        setRosterError((await rosterRes.json().catch(() => ({}))).detail ?? `Error ${rosterRes.status}`)
      } else {
        setRoster(await rosterRes.json())
      }
      if (infoRes.ok) setInfo(await infoRes.json())
      if (officerRes.ok) {
        const d = await officerRes.json()
        setIsOfficer(d.is_officer === true)
      }
    })
      .catch(() => setRosterError('Network error — please try again.'))
      .finally(() => setRosterLoading(false))
  }, [guildName])

  // Load spell check when tab first selected
  function loadSpells() {
    if (spells || spellsLoading || !guildName) return
    setSpellsLoading(true)
    setSpellsError(null)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/spell-check`)
      .then(async res => {
        if (!res.ok) { setSpellsError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        setSpells(await res.json())
      })
      .catch(() => setSpellsError('Network error — please try again.'))
      .finally(() => setSpellsLoading(false))
  }

  // Load adorn check when tab first selected
  function loadAdorns() {
    if (adorns || adornsLoading || !guildName) return
    setAdornsLoading(true)
    setAdornsError(null)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/adorn-check`)
      .then(async res => {
        if (!res.ok) { setAdornsError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        setAdorns(await res.json())
      })
      .catch(() => setAdornsError('Network error — please try again.'))
      .finally(() => setAdornsLoading(false))
  }

  function switchTab(t: Tab) {
    setTab(t)
    setFilter('')
    if (t === 'spells') loadSpells()
    if (t === 'adorns') loadAdorns()
  }

  const currentDiscordId = auth.status === 'authenticated' ? auth.user.id : ''

  const guildDisplayName = roster?.name ?? spells?.guild_name ?? adorns?.guild_name ?? '…'
  const guildWorld = roster?.world ?? ''
  const memberCount = roster?.members.length

  // Unique ranks ordered by rank_id, derived from roster
  const ranksOrdered = useMemo(() => {
    if (!roster) return []
    const seen = new Map<string, number>()
    for (const m of roster.members) {
      if (m.rank && !seen.has(m.rank)) seen.set(m.rank, m.rank_id ?? 9999)
    }
    return [...seen.entries()]
      .sort((a, b) => a[1] - b[1])
      .map(([name]) => name)
  }, [roster])

  function toggleRank(rank: string) {
    setHiddenRanks(prev => {
      const next = new Set(prev)
      next.has(rank) ? next.delete(rank) : next.add(rank)
      return next
    })
  }

  const isLoading = tab === 'roster' ? rosterLoading
    : tab === 'spells' ? spellsLoading
    : tab === 'adorns' ? adornsLoading
    : false   // claims / watch tabs handle their own loading state

  const error = tab === 'roster' ? rosterError
    : tab === 'spells' ? spellsError
    : tab === 'adorns' ? adornsError
    : null

  return (
    <main className="max-w-[1000px] mx-auto my-12 px-4">
      <Breadcrumb items={[{ label: 'Guilds', to: '/guilds' }, { label: guildName ?? '…' }]} />

      {/* Header */}
      <div className="mt-4 mb-6">
        <h1
          className="font-heading text-[2.2rem] font-bold tracking-[0.06em] leading-[1.1] mb-1 inline-block"
          style={{
            background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 40%, var(--gold) 70%, var(--gold-dim) 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}
        >
          {guildDisplayName}
        </h1>
        {guildWorld && (
          <div className="text-text-muted text-[0.88rem] mb-4">
            {guildWorld}{memberCount != null ? ` · ${memberCount} members` : ''}
          </div>
        )}

        {/* Guild info panel */}
        {info && (
          <Card className="flex flex-wrap gap-x-6 gap-y-2 px-[1.1rem] py-[0.85rem]">
            {info.level    != null && <InfoStat label="Guild Level"  value={String(info.level)} />}
            {info.members  != null && <InfoStat label="Characters"   value={String(info.members)} />}
            {info.accounts != null && <InfoStat label="Accounts"     value={String(info.accounts)} />}
            {info.achievement_count > 0 && <InfoStat label="Achievements" value={String(info.achievement_count)} />}
            {info.alignment && <InfoStat label="Alignment" value={info.alignment} />}
            {info.type      && <InfoStat label="Type"      value={info.type} />}
            {info.dateformed && (
              <InfoStat label="Founded" value={new Date(info.dateformed * 1000).toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' })} />
            )}
            {info.description && (
              <div className="w-full pt-[0.4rem] border-t border-border mt-[0.2rem]">
                <span className="text-[0.75rem] text-text-muted uppercase tracking-[0.06em]">Description</span>
                <p className="text-[0.88rem] text-text mt-[0.2rem] leading-normal">{info.description}</p>
              </div>
            )}
          </Card>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-[0.4rem] mb-4 flex-wrap">
        <TabBtn label="Roster"       active={tab === 'roster'} onClick={() => switchTab('roster')} />
        <TabBtn label="Spell Check"  active={tab === 'spells'} onClick={() => switchTab('spells')} />
        <TabBtn label="Adorn Check"  active={tab === 'adorns'} onClick={() => switchTab('adorns')} />
        {isOfficer && (
          <TabBtn label="Claim Requests" active={tab === 'claims'} onClick={() => switchTab('claims')} />
        )}
        {isOfficer && (
          <TabBtn label="Item Watch" active={tab === 'watch'} onClick={() => switchTab('watch')} />
        )}
      </div>

      {/* Filters — hidden on claims and watch tabs */}
      {tab !== 'claims' && tab !== 'watch' && !isLoading && !error && (
        <div className="mb-3 flex flex-col gap-[0.55rem]">
          <input
            type="text"
            placeholder="Filter by name, class or rank…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="max-w-[300px] box-border"
          />
          {ranksOrdered.length > 0 && (
            <div className="flex items-center gap-[0.4rem] flex-wrap">
              <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em] mr-[0.2rem]">
                Ranks
              </span>
              {ranksOrdered.map(rank => {
                const hidden = hiddenRanks.has(rank)
                return (
                  <button
                    key={rank}
                    onClick={() => toggleRank(rank)}
                    className="px-[0.65rem] py-[0.2rem] rounded-[20px] border text-[0.78rem] cursor-pointer transition-all duration-150"
                    style={{
                      borderColor: hidden ? 'var(--border)' : 'rgba(200,169,110,0.45)',
                      background: hidden ? 'transparent' : 'rgba(200,169,110,0.1)',
                      color: hidden ? 'var(--text-muted)' : 'rgba(232,213,163,0.9)',
                      textDecoration: hidden ? 'line-through' : 'none',
                      opacity: hidden ? 0.5 : 1,
                    }}
                  >
                    {rank}
                  </button>
                )
              })}
              {hiddenRanks.size > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setHiddenRanks(new Set())}
                >
                  reset
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="text-text-muted mt-2">
          <p>
            {tab === 'spells'
              ? 'Loading spell data for all members… this takes a minute for large guilds.'
              : 'Fetching guild data…'}
          </p>
        </div>
      )}

      {/* Error */}
      {!isLoading && error && (
        <p className="text-danger">{error}</p>
      )}

      {/* Tables */}
      {tab !== 'claims' && tab !== 'watch' && !isLoading && !error && (
        <Card className="p-0 overflow-x-auto">
          {tab === 'roster' && roster && (
            <RosterTable members={roster.members} filter={filter} hiddenRanks={hiddenRanks} myChars={myChars} />
          )}
          {tab === 'spells' && spells && (
            <SpellCheckTable data={spells} filter={filter} hiddenRanks={hiddenRanks} myChars={myChars} />
          )}
          {tab === 'adorns' && adorns && (
            <AdornCheckTable data={adorns} filter={filter} hiddenRanks={hiddenRanks} myChars={myChars} />
          )}
        </Card>
      )}

      {/* Claim requests — officers only, self-contained loading */}
      {tab === 'claims' && isOfficer && guildName && (
        <Card className="p-0">
          <ClaimRequestsTab guildName={guildName} currentDiscordId={currentDiscordId} />
        </Card>
      )}

      {/* Item watch — officers only, self-contained loading */}
      {tab === 'watch' && isOfficer && guildName && (
        <Card className="p-0">
          <ItemWatchTab guildName={guildName} />
        </Card>
      )}
    </main>
  )
}
