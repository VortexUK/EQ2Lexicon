/**
 * GuildHistoryChart — one time-series line chart over guild_history rows.
 *
 * Rules baked in (see the dataviz notes in the plan): one y-axis per chart
 * (callers put measures of different scale on separate charts), fixed
 * colour per series (a series keeps its colour whatever else is drawn),
 * a legend only when two or more series share the chart, thin 2px lines,
 * no animation, and text in text tokens rather than series colours.
 *
 * Colours come from the theme's CSS variables so the chart matches the
 * page; `fallback` covers jsdom (no computed custom properties). Pass an
 * explicit `width` to skip ResponsiveContainer — tests need it because
 * jsdom reports every element as 0×0.
 */
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { fmtLocalDate, fmtNum } from '../../formatters'
import type { GuildHistoryPoint } from '../../pages/guild/types'
import { dayToUnix, useThemeColour } from './chartUtils'

export interface HistorySeries {
  dataKey: keyof GuildHistoryPoint
  label: string
  /** Theme token, e.g. `--color-gold`. */
  colourVar: string
  /** Literal used when the token cannot be read (jsdom, early paint). */
  fallback: string
  /** Step interpolation for integer levels that only ever move in jumps. */
  step?: boolean
}

interface Props {
  rows: GuildHistoryPoint[]
  series: HistorySeries[]
  height?: number
  width?: number
  emptyText?: string
}

type Row = GuildHistoryPoint & { t: number }

function HistoryTooltip({
  active,
  payload,
  series,
}: {
  active?: boolean
  payload?: { payload: Row }[]
  series: HistorySeries[]
}) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="rounded-sm2 border border-border bg-surface-raised px-3 py-2 text-[0.8rem] shadow-md">
      <div className="mb-1 font-semibold text-text">{fmtLocalDate(row.t)}</div>
      {series.map((s) => {
        const v = row[s.dataKey]
        return (
          <div key={s.dataKey} className="flex items-center gap-2 text-text-muted">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.colourVar }} aria-hidden />
            <span>{s.label}</span>
            <span className="ml-auto pl-3 text-text">{typeof v === 'number' ? fmtNum(v) : '—'}</span>
          </div>
        )
      })}
    </div>
  )
}

function SeriesLine({ s, colour }: { s: HistorySeries; colour: string }) {
  return (
    <Line
      type={s.step ? 'stepAfter' : 'monotone'}
      dataKey={s.dataKey}
      name={s.label}
      stroke={colour}
      strokeWidth={2}
      dot={{ r: 3, fill: colour, strokeWidth: 0 }}
      activeDot={{ r: 5, stroke: 'var(--color-surface)', strokeWidth: 2 }}
      connectNulls
      isAnimationActive={false}
    />
  )
}

export function GuildHistoryChart({
  rows,
  series,
  height = 220,
  width,
  emptyText = 'Not enough history yet — charts appear once two days have been captured.',
}: Props) {
  const gridColour = useThemeColour('--color-border', 'rgba(46, 49, 80, 0.85)')
  const tickColour = useThemeColour('--color-text-muted', '#8b90ab')
  // Hooks must run unconditionally; resolve every series colour up front.
  const colours = series.map((s) => useThemeColour(s.colourVar, s.fallback)) // eslint-disable-line react-hooks/rules-of-hooks
  const data: Row[] = rows.map((r) => ({ ...r, t: dayToUnix(r.day) }))

  if (data.length < 2) {
    return <p className="m-0 px-4 py-6 text-center text-[0.85rem] text-text-muted">{emptyText}</p>
  }

  // Resolved token values for the tooltip swatches (not the raw var names).
  const resolvedSeries = series.map((s, i) => ({ ...s, colourVar: colours[i] }))

  const chart = (
    <LineChart data={data} width={width} height={height} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
      <CartesianGrid stroke={gridColour} strokeDasharray="3 3" vertical={false} />
      <XAxis
        dataKey="t"
        type="number"
        scale="time"
        domain={['dataMin', 'dataMax']}
        tickFormatter={(t: number) => fmtLocalDate(t)}
        tick={{ fill: tickColour, fontSize: 11 }}
        stroke={gridColour}
        tickLine={false}
        minTickGap={48}
      />
      <YAxis
        allowDecimals={false}
        domain={['auto', 'auto']}
        tickFormatter={(v: number) => fmtNum(v)}
        tick={{ fill: tickColour, fontSize: 11 }}
        stroke={gridColour}
        tickLine={false}
        width={44}
      />
      <Tooltip content={<HistoryTooltip series={resolvedSeries} />} cursor={{ stroke: gridColour }} />
      {series.length >= 2 && (
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: '0.78rem', color: tickColour, paddingTop: 8 }}
        />
      )}
      {series.map((s, i) => (
        <SeriesLine key={s.dataKey} s={s} colour={colours[i]} />
      ))}
    </LineChart>
  )

  if (width !== undefined) return chart
  return (
    <ResponsiveContainer width="100%" height={height}>
      {chart}
    </ResponsiveContainer>
  )
}
