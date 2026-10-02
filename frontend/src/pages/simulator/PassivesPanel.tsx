import { Badge, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import { SpellIcon } from './RotationBuilder'
import type { RotationAbility, SimResult } from './types'

// Always-on damage sources: maintained toggles (self pulses kept up
// permanently — Exorcise) and AA proc innates (Bolt of Power). Each
// shows its cadence/trigger and the damage the last sim attributed to
// it. Proc chances come from census effect text, which can be stale vs
// live (Bolt of Power reads 50% at rank 10 but fires on every attack) —
// the chance is editable, with a reset back to the census value.

const TRIGGER_LABEL: Record<string, string> = {
  any_hit: 'any hit',
  melee_hit: 'melee hit',
  ability_cast: 'hostile ability',
  spell_cast: 'hostile spell',
}

function PassiveRow({ p, enabled, dmg, chanceOverride, onToggle, onChanceChange }: {
  p: RotationAbility
  enabled: boolean
  dmg: number | null
  chanceOverride: number | undefined
  onToggle: (baseName: string, enabled: boolean) => void
  onChanceChange: (name: string, pct: number | null) => void
}) {
  const censusChance = p.procs[0]?.chance_pct
  const editable = !p.maintained && p.procs.length > 0 && p.procs.every(pr => pr.per_minute == null)
  return (
    <div className="flex items-center gap-2 px-2 py-1.5 rounded-sm bg-surface-raised border border-border">
      <input
        type="checkbox"
        checked={enabled}
        onChange={e => onToggle(p.base_name, e.target.checked)}
        className="accent-[var(--color-gold)] cursor-pointer"
      />
      <SpellIcon ability={p} />
      <div className="min-w-0 flex-1">
        <div className="text-[0.85rem] font-medium truncate flex items-center gap-1.5">
          {p.name}
          {p.rank != null && <Badge variant="gold">rank {p.rank}</Badge>}
          {p.maintained && <Badge variant="info">maintained</Badge>}
        </div>
        <div className="text-[0.72rem] text-text-muted truncate">
          {p.maintained
            ? p.components
                .filter(c => c.kind === 'dot' && c.interval_s)
                .map(c => `pulses every ${c.interval_s}s`)
                .join(' · ') || 'maintained pulse'
            : p.procs
                .map(pr =>
                  pr.per_minute != null
                    ? `~${pr.per_minute}/min`
                    : `${(chanceOverride ?? pr.chance_pct).toFixed(0)}% on ${TRIGGER_LABEL[pr.trigger] ?? pr.trigger}`,
                )
                .join(' · ')}
        </div>
      </div>
      {editable && (
        <span className="flex items-center gap-1 shrink-0 text-[0.72rem] text-text-muted">
          <input
            type="number"
            min={0}
            max={100}
            value={chanceOverride ?? censusChance ?? 100}
            onChange={e => {
              const v = Number(e.target.value)
              onChanceChange(p.name, Number.isFinite(v) ? Math.min(100, Math.max(0, v)) : null)
            }}
            className="w-14 bg-surface border border-border rounded-sm px-1 py-0.5 text-[0.78rem] text-text text-right"
            title="Proc chance % — census text can be stale; correct it from your log"
          />
          %
          {chanceOverride != null && chanceOverride !== censusChance && (
            <button
              type="button"
              onClick={() => onChanceChange(p.name, null)}
              className="appearance-none border-0 bg-transparent text-gold cursor-pointer text-[0.78rem] px-0.5"
              title={`Reset to census value (${censusChance?.toFixed(0)}%)`}
            >
              ↺
            </button>
          )}
        </span>
      )}
      {dmg != null && dmg > 0 && (
        <span className="text-[0.78rem] text-text-muted shrink-0">{fmtNum(Math.round(dmg))} dmg</span>
      )}
    </div>
  )
}

export default function PassivesPanel({ passives, disabled, result, chanceOverrides, onToggle, onChanceChange }: {
  passives: RotationAbility[]
  disabled: string[]
  result: SimResult | null
  chanceOverrides: Record<string, number>
  onToggle: (baseName: string, enabled: boolean) => void
  onChanceChange: (name: string, pct: number | null) => void
}) {
  if (passives.length === 0) return null
  const damageFor = (baseName: string) =>
    result?.perAbility.find(e => e.isProc && e.ability === baseName)?.damage ?? null

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Maintained &amp; passives</SectionLabel>
      <p className="text-[0.75rem] text-text-muted mt-1 mb-2">
        Always-on damage: maintained pulses you keep up (Exorcise) and proc innates from your AAs —
        modeled as continuous streams per fight.
      </p>
      <div className="flex flex-col gap-1.5">
        {passives.map(p => (
          <PassiveRow
            key={p.base_name}
            p={p}
            enabled={!disabled.includes(p.base_name)}
            dmg={!disabled.includes(p.base_name) ? damageFor(p.base_name) : null}
            chanceOverride={chanceOverrides[p.name]}
            onToggle={onToggle}
            onChanceChange={onChanceChange}
          />
        ))}
      </div>
    </Card>
  )
}
