import type { ReactNode } from 'react'
import { Badge, Button, Card, SectionLabel } from '../../components/ui'
import { effCastTime, effRecast } from './formulas'
import type { RotationAbility, SimStats } from './types'

// Palette of the character's abilities → an ordered priority list.
// Priority 1 always wins when ready; the engine fills gaps with lower rows.

export function SpellIcon({ ability, size = 20 }: {
  ability: { icon_id: number | null; icon_backdrop: number | null }
  size?: number
}) {
  if (ability.icon_id == null && ability.icon_backdrop == null) return null
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {ability.icon_backdrop != null && ability.icon_backdrop > 0 && (
        <img
          src={`/spell-icons/${ability.icon_backdrop}.png`}
          alt=""
          className="absolute inset-0 w-full h-full"
          onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
        />
      )}
      {ability.icon_id != null && ability.icon_id > 0 && (
        <img
          src={`/spell-icons/${ability.icon_id}.png`}
          alt=""
          className="absolute inset-0 w-full h-full"
          onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
        />
      )}
    </div>
  )
}

const fmtSecs = (v: number) => `${Math.round(v * 100) / 100}s`

/** Subtitle shows EFFECTIVE timings under the corrected stats — the same
 * numbers the in-game tooltip shows (cast 2.0 → 1.13s at 77% speed). */
function abilitySubtitle(a: RotationAbility, stats: SimStats): string {
  const cast = effCastTime(a.cast_secs, stats)
  const recast = effRecast(a.recast_secs, stats)
  const bits = [
    `Lv ${a.level}`,
    `${fmtSecs(cast)} cast`,
    a.recast_secs > 0 ? `${fmtSecs(recast)} recast` : null,
  ]
  return bits.filter(Boolean).join(' · ')
}

/** Scope + condition badges shared by the palette and the priority list. */
function AbilityBadges({ a }: { a: RotationAbility }) {
  const conditions = [...new Set(a.components.filter(c => c.condition).map(c => c.condition as string))]
  return (
    <>
      {a.components.some(c => c.target_scope === 'encounter') && <Badge variant="success">green AE</Badge>}
      {a.components.some(c => c.target_scope === 'aoe') && <Badge variant="info">AE</Badge>}
      {conditions.length > 0 && (
        <Badge variant="gold" className="cursor-help" title={conditions.join('\n')}>conditional</Badge>
      )}
      {a.components.some(c => c.suspect_low_value) && (
        <Badge
          variant="danger"
          className="cursor-help"
          title="The game data for this spell reports implausibly low damage (an unscaled tooltip) — the simulated damage is far below reality. Calibration will let you enter the real in-game value."
        >
          bad data
        </Badge>
      )}
      {a.has_unparsed_damage && <Badge variant="warning">partial</Badge>}
      {a.components.some(c => c.duration_estimated) && <Badge variant="muted">est.</Badge>}
    </>
  )
}

export default function RotationBuilder({ abilities, rotation, dotHold, stats, autoAttackSlot, suggestSlot, onChange, onToggleDotHold }: {
  abilities: Record<string, RotationAbility>
  /** Priority-ordered base_name keys. */
  rotation: string[]
  /** base_names whose DoT is held until its last tick before re-casting. */
  dotHold: string[]
  /** Corrected stats — timings display at their EFFECTIVE values. */
  stats: SimStats
  /** Small auto-attack card rendered above the Abilities palette. */
  autoAttackSlot?: ReactNode
  /** The Suggest-order card rendered above the Priority list. */
  suggestSlot?: ReactNode
  onChange: (rotation: string[]) => void
  onToggleDotHold: (name: string, hold: boolean) => void
}) {
  const inRotation = new Set(rotation)
  // Palette shows damage abilities only — utility/buff rows with nothing
  // the engine can model just add noise.
  const palette = Object.values(abilities)
    .filter(a => !inRotation.has(a.base_name) && a.components.length > 0)
    .sort((x, y) => y.level - x.level || x.base_name.localeCompare(y.base_name))

  const move = (i: number, delta: number) => {
    const j = i + delta
    if (j < 0 || j >= rotation.length) return
    const next = [...rotation]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }

  return (
    // One grid, two rows: the two slot cards share row 1, so the grid
    // forces them to IDENTICAL heights (row height = the taller of the
    // two; grid items stretch). Palette/priority share row 2.
    <div className="grid gap-4 md:grid-cols-2">
      {autoAttackSlot ?? <div />}
      {suggestSlot ?? <div />}
      <Card className="rounded-sm px-4 py-3">
        <SectionLabel>Abilities</SectionLabel>
        {palette.length === 0 && (
          <p className="text-[0.82rem] text-text-muted mt-2 mb-0">
            {Object.values(abilities).some(a => a.components.length > 0)
              ? 'All damage abilities are in the rotation.'
              : 'No damage abilities found for this character.'}
          </p>
        )}
        <div className="mt-1 max-h-[420px] overflow-y-auto flex flex-col gap-px">
          {palette.map(a => (
            <button
              key={a.base_name}
              type="button"
              onClick={() => onChange([...rotation, a.base_name])}
              className="appearance-none border-0 bg-transparent w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-left cursor-pointer hover:bg-gold/10"
            >
              <SpellIcon ability={a} />
              <div className="min-w-0 flex-1">
                <div className="text-[0.85rem] font-medium truncate flex items-center gap-1.5">
                  {a.name}
                  <AbilityBadges a={a} />
                </div>
                <div className="text-[0.72rem] text-text-muted truncate">{abilitySubtitle(a, stats)}</div>
              </div>
              <span className="text-[0.8rem] text-gold shrink-0">+ add</span>
            </button>
          ))}
        </div>
      </Card>

      <Card className="rounded-sm px-4 py-3">
        <SectionLabel>Priority list</SectionLabel>
        {rotation.length === 0 && (
          <p className="text-[0.82rem] text-text-muted mt-2 mb-0">
            Add abilities from the palette — the top entry always casts first when ready.
          </p>
        )}
        <ol className="list-none m-0 mt-1 p-0 flex flex-col gap-1">
          {rotation.map((name, i) => {
            const a = abilities[name]
            if (!a) return null
            const hasDot = a.components.some(c => c.kind === 'dot')
            return (
              <li key={name} className="flex items-center gap-2 px-2 py-1.5 rounded-sm bg-surface-raised border border-border">
                <span className="text-[0.75rem] text-gold font-semibold w-5 text-right shrink-0">{i + 1}.</span>
                <SpellIcon ability={a} />
                <div className="min-w-0 flex-1">
                  <div className="text-[0.85rem] font-medium truncate flex items-center gap-1.5">
                    {a.name}
                    <AbilityBadges a={a} />
                  </div>
                  <div className="text-[0.72rem] text-text-muted truncate">{abilitySubtitle(a, stats)}</div>
                </div>
                {hasDot && (
                  <label
                    className="flex items-center gap-1 text-[0.7rem] text-text-muted cursor-pointer shrink-0"
                    title="Wait until the DoT's last tick has landed before re-casting, so refreshing never clips ticks. Off = re-cast the moment it's up (by priority)."
                  >
                    <input
                      type="checkbox"
                      checked={dotHold.includes(name)}
                      onChange={e => onToggleDotHold(name, e.target.checked)}
                      className="accent-[var(--color-gold)]"
                    />
                    hold DoT
                  </label>
                )}
                <div className="flex items-center gap-1 shrink-0">
                  <Button variant="ghost" size="icon" onClick={() => move(i, -1)} disabled={i === 0} title="Higher priority">↑</Button>
                  <Button variant="ghost" size="icon" onClick={() => move(i, 1)} disabled={i === rotation.length - 1} title="Lower priority">↓</Button>
                  <Button variant="ghost" size="icon" onClick={() => onChange(rotation.filter(n => n !== name))} title="Remove">✕</Button>
                </div>
              </li>
            )
          })}
        </ol>
      </Card>
    </div>
  )
}