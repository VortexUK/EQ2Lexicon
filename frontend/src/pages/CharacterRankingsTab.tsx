/**
 * CharacterRankingsTab — WCL-style per-boss ranking summary for one character.
 *
 * Data comes pre-fetched from CharacterPage (which uses it to gate the tab).
 * All percentiles are class-scoped (computed server-side): Best % is the rank
 * percentile of the character's best parse among all parses of their class on
 * that boss; Med % is the median of their parses' percentiles. All Stars
 * Points = 100 × best ÷ class record (closeness to the record); Rank is the
 * position among same-class characters' bests.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge } from '../components/ui'
import { fmtDuration } from '../formatters'
import { percentileColor } from '../percentileColors'
import { useClasses } from '../useClasses'

export interface BossMetricStats {
  best_pct: number
  best_score: number
  median_pct: number
  encounter_id: number
  points: number
  rank: number
  out_of: number
}

export interface BossRankingRow {
  boss: string
  kills: number
  fastest_s: number
  fastest_encounter_id: number
  dps: BossMetricStats | null
  hps: BossMetricStats | null
}

export interface ZoneRankings {
  zone: string
  scope: string
  expansion: string | null
  bosses: BossRankingRow[]
  dps_allstars: { points: number; rank: number; out_of: number } | null
  hps_allstars: { points: number; rank: number; out_of: number } | null
}

export interface CharacterRankings {
  name: string
  cls: string | null
  zones: ZoneRankings[]
  expansions: { short: string; name: string }[]
}

type Metric = 'dps' | 'hps'

const fmtScore = (n: number): string => n.toLocaleString(undefined, { maximumFractionDigits: 1 })

function Pct({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-text-muted">—</span>
  return <span className="font-bold" style={{ color: percentileColor(value) }}>{value}</span>
}

export default function CharacterRankingsTab({ data }: { data: CharacterRankings }) {
  const { byName } = useClasses()
  // Healers default to the Healing board; everyone else to Damage.
  const [metric, setMetric] = useState<Metric>(() =>
    data.cls && byName.get(data.cls)?.archetype === 'Priest' ? 'hps' : 'dps',
  )
  // Expansion filter — the server sends only expansions this character has
  // ranked kills in, newest first.
  const [xpac, setXpac] = useState(() => data.expansions[0]?.short ?? '')
  // Scope filter — raids by default; fall back to heroics only when the
  // character has no raid sections at all.
  const [scope, setScope] = useState<'raid' | 'group'>(() =>
    data.zones.some(z => z.scope === 'raid') ? 'raid' : 'group',
  )
  const zones = data.zones.filter(z => z.scope === scope && (!xpac || z.expansion === xpac))

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {data.expansions.length > 0 && (
          <select
            value={xpac}
            onChange={e => setXpac(e.target.value)}
            className="px-2 py-1 text-[0.82rem]"
            aria-label="Expansion"
          >
            {data.expansions.map(e => (
              <option key={e.short} value={e.short}>{e.name}</option>
            ))}
          </select>
        )}
        <select
          value={scope}
          onChange={e => setScope(e.target.value as 'raid' | 'group')}
          className="px-2 py-1 text-[0.82rem]"
          aria-label="Content type"
        >
          <option value="raid">Raids</option>
          <option value="group">Heroics</option>
        </select>
        {(['dps', 'hps'] as Metric[]).map(m => (
          <button
            key={m}
            onClick={() => setMetric(m)}
            className={`appearance-none border border-border cursor-pointer rounded-sm px-3 py-1 text-[0.78rem] ${
              metric === m ? 'bg-surface-raised text-gold font-semibold' : 'bg-transparent text-text-muted'
            }`}
          >
            {m === 'dps' ? 'Damage' : 'Healing'}
          </button>
        ))}
        <span className="text-[0.72rem] text-text-muted ml-2">
          Percentiles are against other {data.cls ?? 'class'}s on this server.
        </span>
      </div>

      {zones.length === 0 && (
        <p className="text-text-muted text-[0.85rem] mb-4">
          No ranked {scope === 'raid' ? 'raid' : 'heroic'} kills{xpac ? ' in this expansion' : ''}.
        </p>
      )}

      {zones.map(zone => {
        const allstars = metric === 'dps' ? zone.dps_allstars : zone.hps_allstars
        return (
          <div key={`${zone.zone}-${zone.scope}`} className="mb-6">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
              <h3 className="font-heading text-[1.05rem] font-bold text-gold m-0">{zone.zone}</h3>
              <Badge variant={zone.scope === 'raid' ? 'gold' : 'info'}>
                {zone.scope === 'raid' ? 'Raid' : 'Group'}
              </Badge>
              {allstars && (
                <span className="text-[0.78rem] text-text-muted">
                  All Stars: <span className="text-text font-semibold">{allstars.points.toFixed(2)} pts</span>
                  {' · '}#{allstars.rank} of {allstars.out_of} {data.cls ?? 'peer'}s
                </span>
              )}
            </div>
            <div className="border border-border rounded-sm overflow-x-auto">
              <table className="w-full border-collapse text-[0.82rem] min-w-[640px]">
                <thead>
                  <tr className="text-left text-[0.68rem] uppercase tracking-[0.06em] text-text-muted">
                    <th className="px-3 py-1.5 font-semibold">Boss</th>
                    <th className="px-3 py-1.5 font-semibold text-right">Best %</th>
                    <th className="px-3 py-1.5 font-semibold text-right">
                      {metric === 'dps' ? 'Highest DPS' : 'Highest HPS'}
                    </th>
                    <th className="px-3 py-1.5 font-semibold text-right">Kills</th>
                    <th className="px-3 py-1.5 font-semibold text-right">Fastest</th>
                    <th className="px-3 py-1.5 font-semibold text-right">Med %</th>
                    <th className="px-3 py-1.5 font-semibold text-right">Points</th>
                    <th className="px-3 py-1.5 font-semibold text-right">Rank</th>
                  </tr>
                </thead>
                <tbody>
                  {zone.bosses.map(row => {
                    const stats = metric === 'dps' ? row.dps : row.hps
                    return (
                      <tr key={row.boss} className="border-t border-border">
                        <td className="px-3 py-1.5">
                          {stats ? (
                            <Link
                              to={`/parse/${stats.encounter_id}`}
                              className="text-text no-underline hover:text-gold"
                            >
                              {row.boss}
                            </Link>
                          ) : (
                            row.boss
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums"><Pct value={stats?.best_pct} /></td>
                        <td className="px-3 py-1.5 text-right tabular-nums">
                          {stats ? (
                            <Link
                              to={`/parse/${stats.encounter_id}`}
                              className="no-underline"
                              style={{ color: percentileColor(stats.best_pct) }}
                            >
                              {fmtScore(stats.best_score)}
                            </Link>
                          ) : (
                            <span className="text-text-muted">—</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{row.kills}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">
                          <Link
                            to={`/parse/${row.fastest_encounter_id}`}
                            className="text-text no-underline hover:text-gold"
                          >
                            {fmtDuration(row.fastest_s)}
                          </Link>
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums"><Pct value={stats?.median_pct} /></td>
                        <td className="px-3 py-1.5 text-right tabular-nums">
                          {stats ? stats.points.toFixed(2) : <span className="text-text-muted">—</span>}
                        </td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-text-muted">
                          {stats ? `#${stats.rank} / ${stats.out_of}` : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}

      <p className="text-[0.7rem] text-text-muted">
        Built from ranked boss kills uploaded via the ACT plugin. Best % ranks your best parse against every{' '}
        {data.cls ?? 'same-class'} parse on that boss; Med % is the median across your kills. Points measure how
        close your best is to the class record (100 = you hold it); Rank is your position among{' '}
        {data.cls ?? 'class'}s who've logged that boss.
      </p>
    </div>
  )
}
