import { Card, SectionLabel } from '../../components/ui'
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

function Timeline({ result, fightDurationS, colourFor }: {
  result: SimResult
  fightDurationS: number
  colourFor: (ability: string) => string
}) {
  const W = 1000
  const H = 34
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-9 block" preserveAspectRatio="none" aria-label="Cast timeline">
      <rect x={0} y={10} width={W} height={16} fill="rgba(255,255,255,0.05)" />
      {result.timeline.filter(s => s.ability !== '').map((s, i) => {
        const x = (s.t / fightDurationS) * W
        const w = Math.max((s.dur / fightDurationS) * W, 1)
        return (
          <rect key={i} x={x} y={10} width={Math.min(w, W - x)} height={16} fill={colourFor(s.ability)}>
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
  if (!result || rotation.length === 0) {
    return (
      <Card className="rounded-sm px-4 py-3">
        <SectionLabel>Results</SectionLabel>
        <p className="text-[0.82rem] text-text-muted mt-2 mb-0">Build a rotation to see the simulated damage.</p>
      </Card>
    )
  }

  const colourFor = (name: string) => abilityColour(rotation.indexOf(name))

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
        <Timeline result={result} fightDurationS={fightDurationS} colourFor={colourFor} />
      </div>

      <div className="overflow-x-auto mt-3">
        <table className="w-full border-collapse text-[0.84rem]">
          <thead>
            <tr className="border-b border-border text-left text-text-muted text-[0.75rem] uppercase tracking-wide">
              <th className="py-1.5 pr-2 font-semibold">Ability</th>
              <th className="py-1.5 px-2 font-semibold text-right">Casts</th>
              <th className="py-1.5 px-2 font-semibold text-right">Damage</th>
              <th className="py-1.5 px-2 font-semibold text-right">%</th>
              <th className="py-1.5 px-2 font-semibold text-right">Avg / cast</th>
              <th className="py-1.5 pl-2 font-semibold text-right">Clipped ticks</th>
            </tr>
          </thead>
          <tbody>
            {result.perAbility.map(e => (
              <tr key={e.ability} className="border-b border-border">
                <td className="py-1.5 pr-2">
                  <span className="inline-block w-2.5 h-2.5 rounded-[2px] mr-1.5 align-baseline" style={{ background: colourFor(e.ability) }} />
                  {abilities[e.ability]?.name ?? e.ability}
                </td>
                <td className="py-1.5 px-2 text-right">{e.casts}</td>
                <td className="py-1.5 px-2 text-right">{fmtNum(Math.round(e.damage))}</td>
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
