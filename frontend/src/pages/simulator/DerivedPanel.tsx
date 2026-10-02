import { Badge, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import type { CharacterStats } from '../characterSheet'
import { primaryAttrFlat } from './buffs'
import type { BuffMods, DerivedModifiers, SimStats } from './types'

// The sim's ADJUSTED stats: census sheet values + hidden bonuses
// auto-derived from worn gear/adorns, active set bonuses and class AAs,
// plus "+x" deltas from ticked ALWAYS-ON buffs (permanents; temps act as
// timed windows in the sim and aren't flattened in here). Only pertinent
// stats appear — a buff granting AGI to a templar shows nothing.

function Stat({ label, value, note, buffNote }: {
  label: string
  value: string
  note?: string
  buffNote?: string
}) {
  return (
    <div className="min-w-[110px]">
      <div className="text-[0.68rem] text-text-muted uppercase tracking-wide">{label}</div>
      <div className="text-[0.95rem] font-semibold">
        {value}
        {buffNote && <span className="text-[0.72rem] text-success font-normal ml-1">{buffNote}</span>}
        {note && <span className="text-[0.72rem] text-gold font-normal ml-1">{note}</span>}
      </div>
    </div>
  )
}

export default function DerivedPanel({ derived, stats, sheet, buffMods, primaryLabel }: {
  derived: DerivedModifiers
  /** The adjusted stats the sim actually uses (pre-buff). */
  stats: SimStats
  /** The raw census sheet stats, for showing the hidden delta. */
  sheet: CharacterStats
  /** Summed mods from ticked always-on buffs — the "+x" deltas. */
  buffMods: BuffMods
  primaryLabel: string
}) {
  const castHidden = (stats.casting_speed ?? 0) - (sheet.casting_speed ?? 0)
  const reuseHidden = (stats.reuse_speed ?? 0) - (sheet.reuse_speed ?? 0)
  const primaryBuff = primaryAttrFlat(stats, buffMods)

  /** value+delta display: the UPDATED total with a "+x buffs" marker. */
  const pct = (base: number | null | undefined, delta?: number) => ({
    value: `${((base ?? 0) + (delta ?? 0)).toFixed(1)}%`,
    buffNote: delta ? `+${delta.toFixed(1)}` : undefined,
  })
  const flat = (base: number | null | undefined, delta?: number) => ({
    value: fmtNum(Math.round((base ?? 0) + (delta ?? 0))),
    buffNote: delta ? `+${fmtNum(Math.round(delta))}` : undefined,
  })

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Adjusted stats</SectionLabel>
      <p className="text-[0.75rem] text-text-muted mt-1 mb-2">
        What the sim actually uses: sheet stats plus hidden bonuses detected on your gear, adorns,
        set bonuses and AAs. Green deltas come from ticked always-on buffs.
      </p>

      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Stat label={primaryLabel} {...flat(stats.primary_stat, primaryBuff)} />
        <Stat label="Potency" {...pct(stats.potency, buffMods.potencyPct)} />
        <Stat label="Crit chance" {...pct(stats.crit_chance, buffMods.critChancePct)} />
        <Stat label="Crit bonus" {...pct(stats.crit_bonus, buffMods.critBonusPct)} />
        <Stat label="Ability mod" {...flat(stats.ability_mod, buffMods.abilityModFlat)} />
        <Stat
          label="Base damage"
          value={`+${(stats.base_damage_bonus_pct ?? 0).toFixed(0)}%`}
          note="hidden"
        />
        <Stat
          label="Casting speed"
          {...pct(stats.casting_speed, buffMods.castSpeedPct)}
          note={castHidden > 0 ? `+${castHidden.toFixed(0)} hidden` : undefined}
        />
        <Stat
          label="Reuse speed"
          {...pct(stats.reuse_speed, buffMods.reuseSpeedPct)}
          note={reuseHidden > 0 ? `+${reuseHidden.toFixed(0)} hidden` : undefined}
        />
        <Stat label="Recovery speed" {...pct(stats.recovery_speed, buffMods.recoverySpeedPct)} />
        <Stat label="Doublecast" value={`${(stats.ability_doublecast ?? 0).toFixed(1)}%`} />
        <Stat label="Fervor" {...pct(stats.fervor, buffMods.fervorPct)} />
      </div>

      {derived.sources.length > 0 && (
        <div className="mt-3">
          <div className="text-[0.68rem] text-text-muted uppercase tracking-wide mb-1">Detected from</div>
          <div className="flex flex-col gap-0.5">
            {derived.sources.map((s, i) => (
              <div key={i} className="text-[0.75rem] flex items-baseline gap-1.5">
                <Badge variant={s.kind === 'set' ? 'gold' : s.kind === 'aa' ? 'info' : 'muted'}>{s.kind}</Badge>
                <span className="font-medium">{s.name}</span>
                <span className="text-text-muted truncate">{s.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}
