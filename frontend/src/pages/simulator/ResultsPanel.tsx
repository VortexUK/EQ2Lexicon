import { useState } from 'react'
import { Badge, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import type { RotationAbility, SimResult } from './types'

// Simulation output: DPS headline, per-ability breakdown table, and an
// SVG timeline of the cast sequence (idle rendered as gaps).

/** Stable distinct hues assigned per rotation slot (data-driven colour). */
const SEGMENT_COLOURS = [
  '#d9a94a', '#5aa9d6', '#7bc47f', '#c77dba', '#e0805c',
  '#8f8fd9', '#5cc4b8', '#d6c65a', '#d66a7c', '#9ac45c',
]

export function abilityColour(index: number): string {
  return SEGMENT_COLOURS[index % SEGMENT_COLOURS.length]
}

function Timeline({ result, fightDurationS, colourFor, highlight }: {
  result: SimResult
  fightDurationS: number
  colourFor: (ability: string) => string
  /** Dim every segment except this ability's (hover from the table). */
  highlight?: string | null
}) {
  const W = 1000
  const H = 34
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-9 block" preserveAspectRatio="none" aria-label="Cast timeline">
      <rect x={0} y={10} width={W} height={16} fill="rgba(255,255,255,0.05)" />
      {result.timeline.filter(s => s.ability !== '').map((s, i) => {
        const x = (s.t / fightDurationS) * W
        const w = Math.max((s.dur / fightDurationS) * W, 1)
        const hot = highlight != null && s.ability === highlight
        const dim = highlight != null && !hot
        return (
          <rect
            key={i}
            x={x}
            y={hot ? 6 : 10}
            width={Math.min(w, W - x)}
            height={hot ? 24 : 16}
            fill={colourFor(s.ability)}
            fillOpacity={dim ? 0.18 : 1}
          >
            <title>{`${s.ability} @ ${s.t.toFixed(1)}s`}</title>
          </rect>
        )
      })}
    </svg>
  )
}

export default function ResultsPanel({ result, fightDurationS, rotation, abilities }: {
  result: SimResult | null
  fightDurationS: number
  rotation: string[]
  abilities: Record<string, RotationAbility>
}) {
  // Hovered table row (ability key) — drives the timeline highlight.
  const [hovered, setHovered] = useState<string | null>(null)
  if (!result || rotation.length === 0) {
    return (
      <Card className="rounded-sm px-4 py-3">
        <SectionLabel>Results</SectionLabel>
        <p className="text-[0.82rem] text-text-muted mt-2 mb-0">Build a rotation to see the simulated damage.</p>
      </Card>
    )
  }

  const colourFor = (name: string) => {
    const idx = rotation.indexOf(name)
    return idx >= 0 ? abilityColour(idx) : '#8a8a8a'
  }

  // Hovering a table row highlights that ability's casts on the timeline
  // (only abilities that actually have segments — procs/auto are streams).
  const highlight =
    hovered != null && result.timeline.some(seg => seg.ability === hovered) ? hovered : null

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Results</SectionLabel>

      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 mt-2">
        <div>
          <span className="font-heading text-[1.9rem] font-bold text-gold">{fmtNum(Math.round(result.dps))}</span>
          <span className="text-[0.85rem] text-text-muted ml-1.5">DPS</span>
        </div>
        <div className="text-[0.85rem] text-text-muted">
          {fmtNum(Math.round(result.totalDamage))} total over {fightDurationS}s
        </div>
        {result.autoAttackDamage > 0 && (
          <div className="text-[0.85rem] text-text-muted">
            auto-attack {fmtNum(Math.round(result.autoAttackDamage))}
          </div>
        )}
        <div className="text-[0.85rem] text-text-muted">idle {result.idlePct.toFixed(1)}%</div>
      </div>

      <div className="mt-3">
        <Timeline result={result} fightDurationS={fightDurationS} colourFor={colourFor} highlight={highlight} />
      </div>

      <div className="overflow-x-auto mt-3">
        <table className="w-full border-collapse text-[0.84rem]">
          <thead>
            <tr className="border-b border-border text-left text-text-muted text-[0.75rem] uppercase tracking-wide">
              <th className="py-1.5 pr-2 font-semibold">Ability</th>
              <th className="py-1.5 px-2 font-semibold text-right">Casts</th>
              <th className="py-1.5 px-2 font-semibold text-right">Damage</th>
              <th className="py-1.5 px-2 font-semibold text-right">DPS</th>
              <th className="py-1.5 px-2 font-semibold text-right">%</th>
              <th className="py-1.5 px-2 font-semibold text-right">Avg / cast</th>
              <th className="py-1.5 pl-2 font-semibold text-right">Clipped ticks</th>
            </tr>
          </thead>
          <tbody>
            {result.perAbility.map(e => (
              <tr
                key={e.ability}
                className={`border-b border-border ${highlight === e.ability ? 'bg-gold/5' : ''}`}
                onMouseEnter={() => setHovered(e.ability)}
                onMouseLeave={() => setHovered(null)}
              >
                <td className="py-1.5 pr-2">
                  <span className="inline-block w-2.5 h-2.5 rounded-[2px] mr-1.5 align-baseline" style={{ background: colourFor(e.ability) }} />
                  {e.label ?? abilities[e.ability]?.name ?? e.ability}
                  {e.isProc && (
                    <Badge variant="muted" className="ml-1.5" title="Passive proc stream — the casts column is the expected proc count">
                      proc
                    </Badge>
                  )}
                </td>
                <td className="py-1.5 px-2 text-right">{e.casts}</td>
                <td className="py-1.5 px-2 text-right">{fmtNum(Math.round(e.damage))}</td>
                <td className="py-1.5 px-2 text-right">
                  {fightDurationS > 0 ? fmtNum(Math.round(e.damage / fightDurationS)) : '—'}
                </td>
                <td className="py-1.5 px-2 text-right">{e.pct.toFixed(1)}%</td>
                <td className="py-1.5 px-2 text-right">{fmtNum(Math.round(e.avgPerCast))}</td>
                <td className="py-1.5 pl-2 text-right text-text-muted">{e.clippedDotTicks > 0 ? e.clippedDotTicks : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
