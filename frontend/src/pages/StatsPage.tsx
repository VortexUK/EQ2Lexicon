/**
 * StatsPage — per-server lifetime statistics from the census character.stat
 * aggregate family: population + class distribution, server lifetime totals,
 * and named leaderboards per statistic. Data refreshes server-side every
 * 6 h (census recomputes the aggregates daily).
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
import { Card, SectionLabel } from '../components/ui'
import { TabButton } from '../components/ui/TabButton'
import { fmtLocalDate } from '../formatters'
import { useFetch } from '../hooks/useFetch'
import { useServer } from '../hooks/useServer'
import { useClasses } from '../useClasses'

interface ClassStatsRow {
  classid: number
  name: string
  count: number
  avg: Record<string, number>
  global_avg: Record<string, number>
}

interface LeaderEntry {
  name: string
  cls: string | null
  level: number | null
  value: number
}

interface ServerStats {
  world: string
  ts: number
  fetched_at: number
  population: number
  totals: Record<string, number>
  records: Record<string, number>
  averages: Record<string, number>
  global_averages: Record<string, number>
  classes: ClassStatsRow[]
  leaders: Record<string, LeaderEntry[]>
}

const LEADER_BOARDS: { key: string; title: string; hint?: string }[] = [
  { key: 'kills', title: 'Most Kills' },
  { key: 'deaths', title: 'Most Deaths' },
  { key: 'kills_deaths_ratio', title: 'Best K/D Ratio', hint: 'min 1,000 kills' },
  { key: 'max_melee_hit', title: 'Biggest Melee Hit' },
  { key: 'max_magic_hit', title: 'Biggest Magic Hit' },
  { key: 'items_crafted', title: 'Items Crafted' },
  { key: 'rare_harvests', title: 'Rare Harvests' },
]

const TOTAL_CARDS: { key: string; label: string }[] = [
  { key: 'kills', label: 'NPCs slain' },
  { key: 'deaths', label: 'Deaths suffered' },
  { key: 'quests.complete', label: 'Quests completed' },
  { key: 'collections.complete', label: 'Collections finished' },
  { key: 'items_crafted', label: 'Items crafted' },
  { key: 'rare_harvests', label: 'Rares harvested' },
]

const fmt = (n: number | undefined | null, digits = 0): string =>
  n == null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: digits })

// ── Explorer ──────────────────────────────────────────────────────────────────

const EXPLORE_STATS: { key: string; label: string; group: string; digits?: number }[] = [
  { key: 'ability_mod', label: 'Ability Mod', group: 'Combat' },
  { key: 'potency', label: 'Potency', group: 'Combat', digits: 1 },
  { key: 'crit_bonus', label: 'Crit Bonus', group: 'Combat', digits: 1 },
  { key: 'crit_chance', label: 'Crit Chance', group: 'Combat', digits: 1 },
  { key: 'multi_attack', label: 'Multi Attack', group: 'Combat', digits: 1 },
  { key: 'dps', label: 'DPS Mod', group: 'Combat', digits: 1 },
  { key: 'attack_speed', label: 'Attack Speed', group: 'Combat', digits: 1 },
  { key: 'flurry', label: 'Flurry', group: 'Combat', digits: 1 },
  { key: 'block_chance', label: 'Block Chance', group: 'Combat', digits: 1 },
  { key: 'strikethrough', label: 'Strikethrough', group: 'Combat', digits: 1 },
  { key: 'max_health', label: 'Max Health', group: 'Combat' },
  { key: 'max_power', label: 'Max Power', group: 'Combat' },
  { key: 'quests_completed', label: 'Quests Completed', group: 'Progression' },
  { key: 'collections_completed', label: 'Collections Completed', group: 'Progression' },
  { key: 'achievement_points', label: 'Achievement Points', group: 'Progression' },
  { key: 'achievements_completed', label: 'Achievements Completed', group: 'Progression' },
  { key: 'aa_points', label: 'AA Points Spent', group: 'Progression' },
  { key: 'kills', label: 'Kills', group: 'Lifetime' },
  { key: 'deaths', label: 'Deaths', group: 'Lifetime' },
  { key: 'kd_ratio', label: 'K/D Ratio (min 1,000 kills)', group: 'Lifetime', digits: 1 },
  { key: 'items_crafted', label: 'Items Crafted', group: 'Lifetime' },
  { key: 'rare_harvests', label: 'Rare Harvests', group: 'Lifetime' },
]

interface ExploreResponse {
  stat: string
  cls: string | null
  entries: LeaderEntry[]
}

function ExplorerTab({ colourFor }: { colourFor: (cls: string | null | undefined, fallback: string) => string }) {
  const { classes } = useClasses()
  const [stat, setStat] = useState('ability_mod')
  const [cls, setCls] = useState('')

  const url = `/api/stats/explore?stat=${encodeURIComponent(stat)}${cls ? `&cls=${encodeURIComponent(cls)}` : ''}`
  const { data, loading, error } = useFetch<ExploreResponse>(url)
  const statDef = EXPLORE_STATS.find(s => s.key === stat)

  const groups = [...new Set(EXPLORE_STATS.map(s => s.group))]
  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-4 items-center">
        <label className="text-[0.78rem] text-text-muted">
          Stat{' '}
          <select value={stat} onChange={e => setStat(e.target.value)} className="px-2 py-1 text-[0.85rem]" aria-label="Stat">
            {groups.map(g => (
              <optgroup key={g} label={g}>
                {EXPLORE_STATS.filter(s => s.group === g).map(s => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="text-[0.78rem] text-text-muted">
          Class{' '}
          <select value={cls} onChange={e => setCls(e.target.value)} className="px-2 py-1 text-[0.85rem]" aria-label="Class">
            <option value="">All classes</option>
            {classes.map(c => (
              <option key={c.name} value={c.name}>{c.name}</option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p className="text-text-muted text-[0.85rem]">Asking Census…</p>}
      {error && <p className="text-danger text-[0.85rem]">{error}</p>}

      {data && !loading && (
        <Card className="rounded-sm px-1 py-1 max-w-[640px]">
          <table className="w-full border-collapse text-[0.82rem]">
            <thead>
              <tr className="text-left text-[0.68rem] uppercase tracking-[0.06em] text-text-muted">
                <th className="px-3 py-1.5 font-semibold w-[2.5rem]">#</th>
                <th className="px-3 py-1.5 font-semibold">Character</th>
                <th className="px-3 py-1.5 font-semibold">Class</th>
                <th className="px-3 py-1.5 font-semibold text-right">{statDef?.label ?? stat}</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.length === 0 && (
                <tr><td colSpan={4} className="px-3 py-3 text-text-muted text-center">No characters matched.</td></tr>
              )}
              {data.entries.map((e, i) => (
                <tr key={`${e.name}-${i}`} className="border-t border-border">
                  <td className="px-3 py-1 tabular-nums text-text-muted">{i + 1}</td>
                  <td className="px-3 py-1">
                    <Link
                      to={`/character/${encodeURIComponent(e.name)}`}
                      className="no-underline"
                      style={{ color: colourFor(e.cls, 'var(--text)') }}
                    >
                      {e.name}
                    </Link>
                  </td>
                  <td className="px-3 py-1 text-text-muted">{e.cls ?? '—'}{e.level ? ` (${e.level})` : ''}</td>
                  <td className="px-3 py-1 text-right tabular-nums font-medium">{fmt(e.value, statDef?.digits ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <p className="text-[0.7rem] text-text-muted mt-3">
        Combat stats are Census snapshots from each character's last index — close to live, not real-time.
      </p>
    </div>
  )
}

export default function StatsPage() {
  const server = useServer()
  const { colourFor } = useClasses()
  // A cold server answers 202 {status:'building'} while it assembles the
  // stats in the background (~1-2 min of census queries) — poll until real
  // data lands. Normally the startup prewarm means this never fires.
  const { data: raw, loading, error, statusCode, refetch } = useFetch<ServerStats | { status: string }>(
    '/api/stats/server',
  )
  const building = statusCode === 202
  const data = raw && 'population' in raw ? raw : null

  // A connection-level failure (statusCode never set — server restarting,
  // e.g. a Railway deploy or a dev-server reload) is transient: ride it out
  // like the 202 state instead of dead-ending on "NetworkError…".
  const MAX_NET_RETRIES = 4
  const [netRetries, setNetRetries] = useState(0)
  const netFailure = error !== null && statusCode === null
  const reconnecting = netFailure && netRetries < MAX_NET_RETRIES

  useEffect(() => {
    if (statusCode !== null && netRetries !== 0) setNetRetries(0)
  }, [statusCode, netRetries])

  useEffect(() => {
    if (!building && !reconnecting) return
    const t = setTimeout(() => {
      if (reconnecting) setNetRetries(r => r + 1)
      refetch()
    }, 5000)
    return () => clearTimeout(t)
  }, [building, reconnecting, raw, refetch])

  const [tab, setTab] = useState<'server' | 'explore'>('server')
  const maxClassCount = data ? Math.max(1, ...data.classes.map(c => c.count)) : 1

  return (
    <main className="max-w-[1100px] my-8 mx-auto px-4">
      <Breadcrumb items={[{ label: 'Stats' }]} />
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mt-2 mb-1">
        <h1 className="font-heading text-[1.5rem] font-bold text-gold m-0">
          {server?.displayName ?? data?.world ?? 'Server'} Statistics
        </h1>
        {data && (
          <span className="text-[0.78rem] text-text-muted">
            {fmt(data.population)} characters known to Census · aggregates from {fmtLocalDate(data.ts)}
          </span>
        )}
      </div>
      <p className="text-[0.78rem] text-text-muted mb-5">
        Lifetime statistics across every character Census has seen on this server.
      </p>

      {/* Tabs: the aggregate view vs the live stat explorer */}
      <div className="flex border-b border-border mb-4">
        <TabButton active={tab === 'server'} onClick={() => setTab('server')}>Server Records</TabButton>
        <TabButton active={tab === 'explore'} onClick={() => setTab('explore')}>Explorer</TabButton>
      </div>

      {tab === 'explore' && <ExplorerTab colourFor={colourFor} />}

      {tab === 'server' && (loading || building || reconnecting) && (
        <p className="text-text-muted">
          {building
            ? 'Crunching server statistics — the first build after a restart takes a minute or two…'
            : reconnecting || netRetries > 0
              ? 'Lost contact with the server — retrying…'
              : 'Loading server statistics…'}
        </p>
      )}
      {tab === 'server' && error && !reconnecting && <p className="text-danger">{error}</p>}

      {tab === 'server' && data && (
        <>
          {/* ── Lifetime totals ── */}
          <SectionLabel>Server Lifetime Totals</SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 mb-6">
            {TOTAL_CARDS.map(t => (
              <Card key={t.key} className="rounded-sm px-3 py-2.5 text-center">
                <div className="font-heading text-[1.15rem] font-bold text-gold leading-tight tabular-nums">
                  {fmt(data.totals[t.key])}
                </div>
                <div className="text-[0.68rem] uppercase tracking-[0.06em] text-text-muted mt-1">{t.label}</div>
              </Card>
            ))}
          </div>

          {/* ── Class distribution ── */}
          <SectionLabel>Class Population</SectionLabel>
          <Card className="rounded-sm px-4 py-3 mb-6">
            <div className="flex flex-col gap-1">
              {data.classes.map(c => (
                <div key={c.classid} className="flex items-center gap-2 text-[0.8rem]">
                  <span className="w-[7.5rem] shrink-0 truncate" style={{ color: colourFor(c.name, 'var(--text)') }}>
                    {c.name}
                  </span>
                  <div className="flex-1 h-[10px] rounded-full bg-border overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(c.count / maxClassCount) * 100}%`,
                        background: colourFor(c.name, 'var(--color-gold)'),
                        opacity: 0.85,
                      }}
                    />
                  </div>
                  <span className="w-[6.5rem] shrink-0 text-right tabular-nums text-text-muted">
                    {fmt(c.count)} · {((c.count / Math.max(1, data.population)) * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          </Card>

          {/* ── Leaderboards ── */}
          <SectionLabel>Server Records</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
            {LEADER_BOARDS.filter(b => data.leaders[b.key]?.length).map(board => (
              <Card key={board.key} className="rounded-sm px-3 py-2.5">
                <div className="flex items-baseline justify-between mb-1.5">
                  <span className="text-[0.72rem] uppercase tracking-[0.08em] text-gold font-semibold">
                    {board.title}
                  </span>
                  {board.hint && <span className="text-[0.65rem] text-text-muted">{board.hint}</span>}
                </div>
                <ol className="m-0 p-0 list-none">
                  {data.leaders[board.key].map((e, i) => (
                    <li
                      key={`${e.name}-${i}`}
                      className="flex items-baseline gap-2 py-[2px] border-b border-border last:border-b-0 text-[0.8rem]"
                    >
                      <span className="w-[1.4rem] shrink-0 text-right text-text-muted tabular-nums text-[0.7rem]">
                        {i + 1}.
                      </span>
                      <Link
                        to={`/character/${encodeURIComponent(e.name)}`}
                        className="truncate no-underline"
                        style={{ color: colourFor(e.cls, 'var(--text)') }}
                      >
                        {e.name}
                      </Link>
                      <span className="ml-auto shrink-0 tabular-nums font-medium">{fmt(e.value, 1)}</span>
                    </li>
                  ))}
                </ol>
              </Card>
            ))}
          </div>

          {/* ── Class averages ── */}
          <SectionLabel>Class Averages</SectionLabel>
          <Card className="rounded-sm px-1 py-1 mb-4 overflow-x-auto">
            <table className="w-full border-collapse text-[0.8rem]">
              <thead>
                <tr className="text-left text-[0.68rem] uppercase tracking-[0.06em] text-text-muted">
                  <th className="px-3 py-1.5 font-semibold">Class</th>
                  <th className="px-3 py-1.5 font-semibold text-right">Characters</th>
                  <th className="px-3 py-1.5 font-semibold text-right">Avg kills</th>
                  <th className="px-3 py-1.5 font-semibold text-right">Avg K/D</th>
                  <th className="px-3 py-1.5 font-semibold text-right" title="Same class, every server">
                    Global avg K/D
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.classes.map(c => (
                  <tr key={c.classid} className="border-t border-border">
                    <td className="px-3 py-1" style={{ color: colourFor(c.name, 'var(--text)') }}>{c.name}</td>
                    <td className="px-3 py-1 text-right tabular-nums">{fmt(c.count)}</td>
                    <td className="px-3 py-1 text-right tabular-nums">{fmt(c.avg['kills'])}</td>
                    <td className="px-3 py-1 text-right tabular-nums">{fmt(c.avg['kills_deaths_ratio'], 1)}</td>
                    <td className="px-3 py-1 text-right tabular-nums text-text-muted">
                      {fmt(c.global_avg['kills_deaths_ratio'], 1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <p className="text-[0.7rem] text-text-muted">
            Counts cover every character Census has indexed on this server (including inactive ones). Aggregates are
            recomputed daily by Daybreak; leaderboards refresh with them.
          </p>
        </>
      )}
    </main>
  )
}
