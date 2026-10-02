/**
 * Results visualizations — replaces the old single-strip timeline:
 *
 *  1. CastLanes — a per-ability swimlane chart (one lane per rotation
 *     ability, casts as blocks at their sim times, the ability's dot
 *     duration as a faded tail) with a top lane for TEMPORARY buff
 *     windows. Hover syncs with the breakdown table's highlight.
 *  2. DpsChart — damage-per-second over the fight from the engine's
 *     exact 1s bins (they sum to totalDamage), smoothed with a short
 *     rolling window so dot ticks read as a curve, drawn as a gilded
 *     Recharts area (no animation, theme tokens, 2px line).
 */
import { useMemo } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useThemeColour } from '../../components/charts/chartUtils'
import { fmtNum } from '../../formatters'
import type { RotationAbility, SimResult } from './types'

const LANE_H = 16
const LANE_GAP = 4
const W = 1000

/** Merge overlapping windows of the same buff into contiguous spans. */
function mergedWindows(result: SimResult, fightDurationS: number): { buffId: string; start: number; end: number }[] {
  const byBuff = new Map<string, { start: number; end: number }[]>()
  for (const w of result.buffWindows ?? []) {
    const start = Math.max(0, w.start)
    const end = Math.min(w.end, fightDurationS)
    if (end <= start) continue
    if (end - start >= fightDurationS - 1e-6) continue // permanents say nothing
    const list = byBuff.get(w.buffId) ?? []
    list.push({ start, end })
    byBuff.set(w.buffId, list)
  }
  const out: { buffId: string; start: number; end: number }[] = []
  for (const [buffId, list] of byBuff) {
    list.sort((a, b) => a.start - b.start)
    let cur = { ...list[0] }
    for (const span of list.slice(1)) {
      if (span.start <= cur.end + 1e-6) cur.end = Math.max(cur.end, span.end)
      else {
        out.push({ buffId, ...cur })
        cur = { ...span }
      }
    }
    out.push({ buffId, ...cur })
  }
  return out
}

export function CastLanes({ result, fightDurationS, rotation, abilities, colourFor, highlight, onHover }: {
  result: SimResult
  fightDurationS: number
  rotation: string[]
  abilities: Record<string, RotationAbility>
  colourFor: (ability: string) => string
  highlight?: string | null
  onHover?: (ability: string | null) => void
}) {
  const lanes = rotation.filter(n => result.timeline.some(s => s.ability === n))
  const buffSpans = useMemo(() => mergedWindows(result, fightDurationS), [result, fightDurationS])
  // One labeled mini-lane PER BUFF (raid rotation stuff reads at a
  // glance) rather than a single anonymous "Buffs" strip.
  const buffIds = useMemo(() => [...new Set(buffSpans.map(s => s.buffId))].sort(), [buffSpans])
  const buffLabel = (id: string) =>
    abilities[id]?.name ?? id.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  const x = (t: number) => (t / fightDurationS) * W
  const laneY = (i: number) => i * (LANE_H + LANE_GAP)
  const H = lanes.length * (LANE_H + LANE_GAP) - LANE_GAP

  if (lanes.length === 0) return null
  return (
    <div className="flex flex-col gap-1">
      {buffIds.map(id => (
        <div key={id} className="flex items-center gap-2">
          <div
            className="w-32 shrink-0 text-right truncate text-[0.72rem] text-gold/80 pr-0"
            style={{ height: LANE_H, lineHeight: `${LANE_H}px` }}
            title={buffLabel(id)}
          >
            {buffLabel(id)}
          </div>
          <svg viewBox={`0 0 ${W} ${LANE_H}`} className="w-full block" style={{ height: LANE_H }} preserveAspectRatio="none">
            {buffSpans
              .filter(sp => sp.buffId === id)
              .map((sp, i) => (
                <rect
                  key={i}
                  x={x(sp.start)}
                  y={2}
                  width={Math.max(x(sp.end) - x(sp.start), 2)}
                  height={LANE_H - 4}
                  rx={2}
                  fill="rgba(217,169,74,0.45)"
                >
                  <title>{`${buffLabel(id)} ${sp.start.toFixed(0)}–${sp.end.toFixed(0)}s`}</title>
                </rect>
              ))}
          </svg>
        </div>
      ))}
      <div className="flex items-start gap-2">
        <div className="w-32 shrink-0 flex flex-col" style={{ gap: LANE_GAP }}>
          {lanes.map(n => (
            <button
              key={n}
              type="button"
              onMouseEnter={() => onHover?.(n)}
              onMouseLeave={() => onHover?.(null)}
              className={`appearance-none border-0 bg-transparent p-0 cursor-default text-right truncate text-[0.72rem] leading-none transition-colors ${
                highlight === n ? 'text-gold' : 'text-text-muted'
              }`}
              style={{ height: LANE_H, lineHeight: `${LANE_H}px` }}
              title={abilities[n]?.name ?? n}
            >
              {abilities[n]?.name ?? n}
            </button>
          ))}
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full block" style={{ height: H }} preserveAspectRatio="none">
          {/* Temp buff spans band every lane faintly — burst alignment. */}
          {buffSpans.map((s, i) => (
            <rect key={`b${i}`} x={x(s.start)} y={0} width={Math.max(x(s.end) - x(s.start), 1)} height={H} fill="rgba(217,169,74,0.08)" />
          ))}
          {lanes.map((n, li) => {
            const a = abilities[n]
            const dotDur = Math.max(
              0,
              ...(a?.components ?? []).filter(c => c.kind === 'dot').map(c => c.duration_s ?? 0),
            )
            const dim = highlight != null && highlight !== n
            return result.timeline
              .filter(s => s.ability === n)
              .map((s, i) => {
                const bx = x(s.t)
                const bw = Math.max(x(s.dur), 2)
                return (
                  <g key={`${li}-${i}`} opacity={dim ? 0.25 : 1}>
                    {dotDur > 0 && (
                      <rect
                        x={bx}
                        y={laneY(li) + LANE_H / 2 - 1.5}
                        width={Math.min(x(s.dur + dotDur), W - bx)}
                        height={3}
                        fill={colourFor(n)}
                        opacity={0.35}
                      />
                    )}
                    <rect x={bx} y={laneY(li)} width={Math.min(bw, W - bx)} height={LANE_H} rx={2} fill={colourFor(n)}>
                      <title>{`${a?.name ?? n} @ ${s.t.toFixed(1)}s`}</title>
                    </rect>
                  </g>
                )
              })
          })}
        </svg>
      </div>
      {/* Time axis */}
      <div className="flex items-center gap-2">
        <div className="w-32 shrink-0" />
        <div className="flex-1 flex justify-between text-[0.65rem] text-text-muted">
          {[0, 0.25, 0.5, 0.75, 1].map(f => (
            <span key={f}>{Math.round(fightDurationS * f)}s</span>
          ))}
        </div>
      </div>
    </div>
  )
}

/** Centered rolling mean — dot ticks and cast spikes read as a curve. */
export function rollingDps(bins: number[], window = 5): { t: number; dps: number }[] {
  const half = Math.floor(window / 2)
  return bins.map((_, i) => {
    const from = Math.max(0, i - half)
    const to = Math.min(bins.length - 1, i + half)
    let sum = 0
    for (let k = from; k <= to; k++) sum += bins[k]
    return { t: i, dps: sum / (to - from + 1) }
  })
}

function DpsTooltip({ active, payload }: { active?: boolean; payload?: { payload: { t: number; dps: number } }[] }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="rounded-sm2 border border-border bg-surface px-2.5 py-1.5 text-[0.78rem] shadow-lg">
      <div className="text-text-muted">{row.t}s</div>
      <div className="font-semibold text-gold">{fmtNum(Math.round(row.dps))} DPS</div>
    </div>
  )
}

export function DpsChart({ result, height = 150 }: { result: SimResult; height?: number }) {
  const gold = useThemeColour('--color-gold', '#d9a94a')
  const border = useThemeColour('--color-border', '#3a3f4e')
  const muted = useThemeColour('--color-text-muted', '#9aa1b4')
  const data = useMemo(() => rollingDps(result.dpsBins ?? []), [result])
  if (data.length < 3) return null
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="dpsFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={gold} stopOpacity={0.45} />
            <stop offset="100%" stopColor={gold} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={border} strokeOpacity={0.4} vertical={false} />
        <XAxis
          dataKey="t"
          tick={{ fill: muted, fontSize: 11 }}
          tickLine={false}
          axisLine={{ stroke: border }}
          tickFormatter={v => `${v}s`}
          minTickGap={40}
        />
        <YAxis
          tick={{ fill: muted, fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          tickFormatter={v => fmtNum(v)}
          width={52}
        />
        <Tooltip content={<DpsTooltip />} cursor={{ stroke: gold, strokeOpacity: 0.4 }} />
        <Area type="monotone" dataKey="dps" stroke={gold} strokeWidth={2} fill="url(#dpsFill)" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
