import { useState } from 'react'
import { Badge, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import { CastLanes, DpsChart } from './ResultsCharts'
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

export default function ResultsPanel({ result, fightDurationS, rotation, abilities, firstCastAt, onFirstCastAt, timedExternals, onExternalStartAt }: {
  result: SimResult | null
  fightDurationS: number
  rotation: string[]
  abilities: Record<string, RotationAbility>
  /** Temp-buff timing: earliest FIRST cast per ability (absent = on
   * ready). The sliders sit right above the lanes so dragging visibly
   * slides the buff's window and reshapes the DPS curve. */
  firstCastAt: Record<string, number>
  onFirstCastAt: (name: string, t: number) => void
  /** On/off external ally buffs that are ACTIVE (Bolster): same slider,
   * moving the first window along the fight. */
  timedExternals: { id: string; name: string; startAt: number }[]
  onExternalStartAt: (id: string, t: number) => void
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

      {/* Temp-buff timing: drag to move WHEN each buff is first used —
          its window block below and the DPS curve follow live. Only
          buffs whose STAT MODS boost other abilities get a slider;
          proc-only temps (Consumption) are just their own damage. */}
      {rotation
        .map(n => abilities[n])
        .filter(
          (a): a is RotationAbility =>
            !!a &&
            !!a.beneficial &&
            (a.duration_s ?? 0) > 0 &&
            Object.keys(a.mods ?? {}).length > 0,
        )
        .map(a => {
          const delay = firstCastAt[a.base_name] ?? 0
          return (
            <div
              key={a.base_name}
              className="flex items-center gap-2 mt-2"
              title="When the FIRST use lands — later uses follow the cooldown."
            >
              <span className="inline-block w-2.5 h-2.5 rounded-[2px] shrink-0" style={{ background: colourFor(a.base_name) }} />
              <span className="text-[0.75rem] w-40 shrink-0 truncate">{a.name}</span>
              <input
                type="range"
                min={0}
                max={Math.max(0, Math.floor(fightDurationS - 1))}
                step={1}
                value={Math.min(delay, fightDurationS)}
                onChange={e => onFirstCastAt(a.base_name, Number(e.target.value))}
                className="flex-1 accent-[var(--color-gold)] h-1 cursor-pointer"
              />
              <span className="text-[0.72rem] w-16 shrink-0 text-right tabular-nums text-text-muted">
                {delay > 0 ? 'use @ ' + delay + 's' : 'on ready'}
              </span>
            </div>
          )
        })}
      {timedExternals.map(b => (
        <div
          key={b.id}
          className="flex items-center gap-2 mt-2"
          title="When the buff first lands on you — later applications follow its recast."
        >
          <span className="inline-block w-2.5 h-2.5 rounded-[2px] shrink-0 bg-gold/60" />
          <span className="text-[0.75rem] w-40 shrink-0 truncate">{b.name}</span>
          <input
            type="range"
            min={0}
            max={Math.max(0, Math.floor(fightDurationS - 1))}
            step={1}
            value={Math.min(b.startAt, fightDurationS)}
            onChange={e => onExternalStartAt(b.id, Number(e.target.value))}
            className="flex-1 accent-[var(--color-gold)] h-1 cursor-pointer"
          />
          <span className="text-[0.72rem] w-16 shrink-0 text-right tabular-nums text-text-muted">
            {b.startAt > 0 ? 'use @ ' + b.startAt + 's' : 'at start'}
          </span>
        </div>
      ))}

      <div className="mt-3">
        <CastLanes
          result={result}
          fightDurationS={fightDurationS}
          rotation={rotation}
          abilities={abilities}
          colourFor={colourFor}
          highlight={highlight}
          onHover={setHovered}
        />
      </div>

      <div className="mt-4">
        <div className="text-[0.68rem] text-text-muted uppercase tracking-wide mb-1">DPS over the fight</div>
        <DpsChart result={result} />
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
