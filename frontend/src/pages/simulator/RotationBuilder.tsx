import { useState } from 'react'
import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { Badge, Button, Card, SectionLabel } from '../../components/ui'
import AbilityTooltip from './AbilityTooltip'
import { effCastTimeFor, effRecastFor } from './formulas'
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
  const cast = effCastTimeFor(a, stats)
  const recast = effRecastFor(a, stats)
  const bits = [
    `Lv ${a.level}`,
    `${fmtSecs(cast)} cast`,
    a.recast_secs > 0 ? `${fmtSecs(recast)} recast` : null,
  ]
  return bits.filter(Boolean).join(' · ')
}

/** Beyond this many levels below the character the game replaces a rank's
 * damage with a level curve census doesn't carry — the sim's numbers there
 * are knowingly wrong and badged. Within the gap the normal model holds. */
export const GREY_RANK_LEVEL_GAP = 35

/** Scope + condition badges shared by the palette and the priority list. */
function AbilityBadges({ a, charLevel }: { a: RotationAbility; charLevel?: number }) {
  const conditions = [...new Set(a.components.filter(c => c.condition).map(c => c.condition as string))]
  const grey =
    charLevel != null &&
    charLevel - a.level > GREY_RANK_LEVEL_GAP &&
    a.components.length > 0 &&
    !a.components.some(c => c.no_flat_mod) // curated auto-scaled bases are modeled
  return (
    <>
      {grey && (
        <Badge
          variant="muted"
          className="cursor-help"
          title="Low-rank spell: in game its damage is level-upscaled by a formula census doesn't carry — the sim shows the era base, which will read far lower than your in-game tooltip."
        >
          grey rank
        </Badge>
      )}
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

export default function RotationBuilder({ abilities, rotation, dotHold, stats, charLevel, autoAttackSlot, suggestSlot, onChange, onToggleDotHold }: {
  abilities: Record<string, RotationAbility>
  /** Priority-ordered base_name keys. */
  rotation: string[]
  /** base_names whose DoT is held until its last tick before re-casting. */
  dotHold: string[]
  /** Corrected stats — timings display at their EFFECTIVE values. */
  stats: SimStats
  /** Character level — grey-rank badging (level-upscaled in game). */
  charLevel?: number
  /** Small auto-attack card rendered above the Abilities palette. */
  autoAttackSlot?: ReactNode
  /** The Suggest-order card rendered above the Priority list. */
  suggestSlot?: ReactNode
  onChange: (rotation: string[]) => void
  onToggleDotHold: (name: string, hold: boolean) => void
}) {
  // Icon-hover tooltip: the ability at the character's ADJUSTED values.
  const [tip, setTip] = useState<{ ability: RotationAbility; x: number; y: number } | null>(null)
  const tipHandlers = (a: RotationAbility) => ({
    onMouseEnter: (e: ReactMouseEvent) => setTip({ ability: a, x: e.clientX, y: e.clientY }),
    onMouseMove: (e: ReactMouseEvent) => setTip(t => (t ? { ...t, x: e.clientX, y: e.clientY } : t)),
    onMouseLeave: () => setTip(null),
  })

  const inRotation = new Set(rotation)
  // Palette shows abilities the engine can model: direct damage,
  // trigger-budget procs (Slothful Spirit), or a castable temp window
  // that carries a proc (Consumption) or stat mods. Pure utility rows
  // (heals, fears) just add noise and stay hidden.
  const modelable = (a: RotationAbility) =>
    a.components.length > 0 ||
    (a.procs ?? []).some(p => (p.trigger_count ?? 0) > 0) ||
    (!!a.beneficial &&
      (a.duration_s ?? 0) > 0 &&
      ((a.procs ?? []).length > 0 || Object.keys(a.mods ?? {}).length > 0))
  const palette = Object.values(abilities)
    .filter(a => !inRotation.has(a.base_name) && modelable(a))
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
            {Object.values(abilities).some(modelable)
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
              <span {...tipHandlers(a)}><SpellIcon ability={a} /></span>
              <div className="min-w-0 flex-1">
                <div className="text-[0.85rem] font-medium truncate flex items-center gap-1.5">
                  {a.name}
                  <AbilityBadges a={a} charLevel={charLevel} />
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
              <li key={name} className="px-2 py-1.5 rounded-sm bg-surface-raised border border-border">
                <div className="flex items-center gap-2">
                  <span className="text-[0.75rem] text-gold font-semibold w-5 text-right shrink-0">{i + 1}.</span>
                  <span {...tipHandlers(a)}><SpellIcon ability={a} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[0.85rem] font-medium truncate flex items-center gap-1.5">
                      {a.name}
                      <AbilityBadges a={a} charLevel={charLevel} />
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
                </div>
              </li>
            )
          })}
        </ol>
      </Card>
      {tip && <AbilityTooltip ability={tip.ability} stats={stats} x={tip.x} y={tip.y} />}
    </div>
  )
}