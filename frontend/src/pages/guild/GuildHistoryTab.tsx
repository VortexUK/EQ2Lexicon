/**
 * GuildHistoryTab — how the guild has changed over time.
 *
 * Three stacked charts over GET /api/guild/{name}/history: members and
 * accounts (one count axis), guild level, and members at the server's
 * max level. One row per UTC day is captured by the guild refresh, so a
 * guild nobody has viewed before starts its history the day it is first
 * opened. Default export so GuildPage can lazy-load it: Recharts only
 * downloads when this tab opens.
 */
import { useState } from 'react'
import { FilterPill } from '../../components/FilterPill'
import { GuildHistoryChart, type HistorySeries } from '../../components/charts/GuildHistoryChart'
import { Card, SectionLabel } from '../../components/ui'
import { useFetch } from '../../hooks/useFetch'
import { useServer } from '../../hooks/useServer'
import type { GuildHistoryResponse } from './types'

const RANGES = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
] as const

type Range = (typeof RANGES)[number]['days']

// Fixed colour per measure — a series never changes colour because of what
// else is on screen. Gold + cyan validated against the card surface (CVD
// ΔE 18.5, normal ΔE 22.9); lime is the EQ2 primary-stat colour.
const MEMBERSHIP: HistorySeries[] = [
  { dataKey: 'members', label: 'Members', colourVar: '--color-gold', fallback: '#c8a96e' },
  { dataKey: 'accounts', label: 'Accounts', colourVar: '--color-stat-secondary', fallback: '#00e5ff' },
]
const LEVEL: HistorySeries[] = [
  { dataKey: 'level', label: 'Guild level', colourVar: '--color-gold', fallback: '#c8a96e', step: true },
]
const MAX_LEVEL: HistorySeries[] = [
  { dataKey: 'max_level_members', label: 'Max-level members', colourVar: '--color-stat-primary', fallback: '#22ff22' },
]

interface Props {
  guildName: string
  /** Test hook: fixed chart width so jsdom (0×0 layout) still renders SVG. */
  chartWidth?: number
}

export default function GuildHistoryTab({ guildName, chartWidth }: Props) {
  const [days, setDays] = useState<Range>(90)
  const maxLevel = useServer()?.maxLevel
  const { data, loading, error } = useFetch<GuildHistoryResponse>(
    `/api/guild/${encodeURIComponent(guildName)}/history?days=${days}`,
  )
  const rows = data?.points ?? []

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="History range">
        {RANGES.map((r) => (
          <FilterPill key={r.days} active={days === r.days} onClick={() => setDays(r.days)}>
            {r.label}
          </FilterPill>
        ))}
      </div>

      {error && <p className="m-0 text-[0.85rem] text-danger">{error}</p>}
      {!error && !loading && data && rows.length === 0 && (
        <Card className="px-4 py-6 text-center text-[0.9rem] text-text-muted">
          History starts collecting from today. Each guild refresh records one point per day, so come back
          tomorrow for the first line.
        </Card>
      )}

      {!error && (loading || rows.length > 0) && (
        <>
          <Card className="p-4">
            <SectionLabel>Members and accounts</SectionLabel>
            {loading ? <ChartSkeleton /> : <GuildHistoryChart rows={rows} series={MEMBERSHIP} width={chartWidth} />}
          </Card>
          <Card className="p-4">
            <SectionLabel>Guild level</SectionLabel>
            {loading ? <ChartSkeleton /> : <GuildHistoryChart rows={rows} series={LEVEL} width={chartWidth} />}
          </Card>
          <Card className="p-4">
            <SectionLabel>{maxLevel ? `Members at level ${maxLevel}` : 'Max-level members'}</SectionLabel>
            {loading ? <ChartSkeleton /> : <GuildHistoryChart rows={rows} series={MAX_LEVEL} width={chartWidth} />}
          </Card>
        </>
      )}

      <p className="m-0 text-[0.78rem] text-text-muted">
        One point per day, taken from the guild refresh (every 15 minutes while the guild is being viewed). History
        is kept for 400 days.
      </p>
    </div>
  )
}

function ChartSkeleton() {
  return <div className="h-[220px] animate-pulse rounded-md bg-surface-raised/60" aria-busy="true" aria-label="Loading chart" />
}
