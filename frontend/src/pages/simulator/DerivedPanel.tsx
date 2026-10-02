import { Badge, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import type { CharacterStats } from '../characterSheet'
import type { DerivedModifiers, SimStats } from './types'

// The sim's CORRECTED stats: census sheet values + hidden bonuses
// auto-derived from worn gear/adorns, active set bonuses and class AAs.
// Users shouldn't need to touch anything — but every derived number is
// correctable, and every source is shown.

export type Overrides = { base: number | null; cast: number | null; reuse: number | null }

const NUM_CLASS =
  'py-1 px-2 rounded-sm2 border border-border bg-surface-raised text-text text-[0.82rem] w-20 [color-scheme:dark]'

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-[110px]">
      <div className="text-[0.68rem] text-text-muted uppercase tracking-wide">{label}</div>
      <div className="text-[0.95rem] font-semibold">
        {value}
        {note && <span className="text-[0.72rem] text-gold font-normal ml-1">{note}</span>}
      </div>
    </div>
  )
}

function BonusRow({ label, derivedValue, override, onChange, unit = '%' }: {
  label: string
  derivedValue: number
  override: number | null
  onChange: (v: number | null) => void
  unit?: string
}) {
  const effective = override ?? derivedValue
  return (
    <label className="flex items-center gap-2 text-[0.8rem]">
      <span className="w-28">{label}</span>
      <input
        type="number"
        min={0}
        max={500}
        value={effective}
        onChange={e => {
          const v = Number(e.target.value)
          if (Number.isFinite(v)) onChange(Math.min(Math.max(v, 0), 500))
        }}
        className={NUM_CLASS}
        aria-label={`${label} ${unit}`}
      />
      <span className="text-text-muted text-[0.72rem]">{unit}</span>
      {override != null && override !== derivedValue ? (
        <button
          type="button"
          onClick={() => onChange(null)}
          className="appearance-none border-0 bg-transparent cursor-pointer text-[0.72rem] text-gold hover:text-gold-bright p-0"
          title={`Reset to the auto-detected ${derivedValue}${unit}`}
        >
          auto: {derivedValue}
          {unit} ↺
        </button>
      ) : (
        <span className="text-[0.72rem] text-text-muted">auto</span>
      )}
    </label>
  )
}

export default function DerivedPanel({ derived, overrides, stats, sheet, primaryLabel, onOverride }: {
  derived: DerivedModifiers
  overrides: Overrides
  /** The corrected stats the sim actually uses. */
  stats: SimStats
  /** The raw census sheet stats, for showing the hidden delta. */
  sheet: CharacterStats
  primaryLabel: string
  onOverride: (next: Overrides) => void
}) {
  const castHidden = (stats.casting_speed ?? 0) - (sheet.casting_speed ?? 0)
  const reuseHidden = (stats.reuse_speed ?? 0) - (sheet.reuse_speed ?? 0)
  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Corrected stats</SectionLabel>
      <p className="text-[0.75rem] text-text-muted mt-1 mb-2">
        What the sim actually uses: sheet stats plus hidden bonuses detected on your gear, adorns,
        set bonuses and AAs. Correct a value if the detection missed something.
      </p>

      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Stat label={primaryLabel} value={fmtNum(Math.round(stats.primary_stat ?? 0))} />
        <Stat label="Potency" value={`${(stats.potency ?? 0).toFixed(1)}%`} />
        <Stat label="Crit chance" value={`${(stats.crit_chance ?? 0).toFixed(1)}%`} />
        <Stat label="Crit bonus" value={`${(stats.crit_bonus ?? 0).toFixed(1)}%`} />
        <Stat label="Ability mod" value={fmtNum(Math.round(stats.ability_mod ?? 0))} />
        <Stat
          label="Base damage"
          value={`+${(stats.base_damage_bonus_pct ?? 0).toFixed(0)}%`}
          note="hidden"
        />
        <Stat
          label="Casting speed"
          value={`${(stats.casting_speed ?? 0).toFixed(1)}%`}
          note={castHidden > 0 ? `+${castHidden.toFixed(0)} hidden` : undefined}
        />
        <Stat
          label="Reuse speed"
          value={`${(stats.reuse_speed ?? 0).toFixed(1)}%`}
          note={reuseHidden > 0 ? `+${reuseHidden.toFixed(0)} hidden` : undefined}
        />
        <Stat label="Doublecast" value={`${(stats.ability_doublecast ?? 0).toFixed(1)}%`} />
        <Stat label="Fervor" value={`${(stats.fervor ?? 0).toFixed(1)}%`} />
      </div>

      <div className="mt-3 flex flex-col gap-1.5">
        <BonusRow
          label="Base damage"
          derivedValue={derived.base_damage_bonus_pct}
          override={overrides.base}
          onChange={v => onOverride({ ...overrides, base: v })}
        />
        <BonusRow
          label="Casting speed"
          derivedValue={derived.cast_speed_bonus_pct}
          override={overrides.cast}
          onChange={v => onOverride({ ...overrides, cast: v })}
        />
        <BonusRow
          label="Reuse"
          derivedValue={derived.reuse_bonus_pct}
          override={overrides.reuse}
          onChange={v => onOverride({ ...overrides, reuse: v })}
        />
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
