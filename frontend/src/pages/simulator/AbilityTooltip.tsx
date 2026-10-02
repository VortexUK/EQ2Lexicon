import { createPortal } from 'react-dom'
import { Badge } from '../../components/ui'
import { fmtNum } from '../../formatters'
import { useTooltipPosition } from '../../hooks/useTooltipPosition'
import {
  abilityDmgMod,
  abilityDotDmgMod,
  abilityModShare,
  componentFlatFraction,
  flatDamageMod,
  isPerHp,
  perHpDamage,
  damageCoefficient,
  dotTicks,
  effCastTimeFor,
  effRecastFor,
  expectedCastDamage,
  primaryComponent,
  PROC_AM_SHARE,
} from './formulas'
import type { DamageComponent, RotationAbility, SimStats } from './types'

// Hover tooltip for a spell icon: the ability AT THE CHARACTER'S STATS —
// the same adjusted numbers the in-game tooltip would show (coefficient
// chain + ability-mod share, crit/fervor excluded), plus effective
// timings and the expected dealt damage per cast.

const WIDTH = 300

const fmtSecs = (v: number) => `${Math.round(v * 100) / 100}s`
const fmtRange = (lo: number, hi: number) =>
  Math.round(lo) === Math.round(hi) ? fmtNum(Math.round(lo)) : `${fmtNum(Math.round(lo))} – ${fmtNum(Math.round(hi))}`

const SCOPE_LABEL: Record<string, string> = { encounter: 'encounter', aoe: 'AE' }

export default function AbilityTooltip({ ability, stats, x, y }: {
  ability: RotationAbility
  stats: SimStats
  x: number
  y: number
}) {
  const { ref, position } = useTooltipPosition({ x, y, width: WIDTH })
  const coeff = damageCoefficient(stats, ability.level)
  const half = componentFlatFraction(ability.level)
  const primary = primaryComponent(ability)

  // Own Enhance AAs multiply the BASE-CHAIN part only; the flat mod
  // (ability mod + school-matched gear flat) adds outside it.
  const dmgMod = abilityDmgMod(ability)
  const adjusted = (c: DamageComponent): { lo: number; hi: number } => {
    if (isPerHp(c)) {
      const v = perHpDamage(c, stats)
      return { lo: v, hi: v }
    }
    // ½ flat = constant ½×B̄ on BOTH ends (widths scale by bare chain).
    // Auto-scaled class ranks (Wrath): bare chain — no ½, no mod.
    const flatPart = c.no_flat_mod ? 0 : (half * (c.min_dmg + c.max_dmg)) / 2
    const dotMod = c.kind === 'dot' ? abilityDotDmgMod(ability) : 1
    const amPart = c === primary && !c.no_flat_mod ? flatDamageMod(stats, c.school) * abilityModShare(c) : 0
    return {
      lo: (c.min_dmg * coeff + flatPart) * dmgMod * dotMod + amPart,
      hi: (c.max_dmg * coeff + flatPart) * dmgMod * dotMod + amPart,
    }
  }

  const expected = expectedCastDamage(ability, stats)

  // Portal to <body> (the ItemTooltip pattern): rendered in place, the
  // page-reveal transform + the palette's overflow container break
  // position:fixed. Portaled, viewport coords are viewport coords.
  return createPortal(
    <div
      ref={ref}
      className="fixed z-tooltip pointer-events-none rounded-sm border border-border bg-surface shadow-lg px-3 py-2"
      style={{ left: position.left, top: position.top, width: WIDTH }}
    >
      <div className="text-[0.9rem] font-semibold flex items-center gap-1.5 flex-wrap">
        {ability.name}
        <Badge variant="muted">{ability.tier_name}</Badge>
        <span className="text-[0.72rem] text-text-muted font-normal">Lv {ability.level}</span>
      </div>
      <div className="text-[0.75rem] text-text-muted mt-0.5">
        {fmtSecs(effCastTimeFor(ability, stats))} cast
        {ability.recast_secs > 0 && ` · ${fmtSecs(effRecastFor(ability, stats))} recast`}
        {ability.duration_s != null && ` · ${fmtSecs(ability.duration_s)} duration`}
      </div>

      <div className="mt-1.5 flex flex-col gap-0.5">
        {ability.components.map((c, i) => {
          const { lo, hi } = adjusted(c)
          const scope = SCOPE_LABEL[c.target_scope]
          return (
            <div key={i} className="text-[0.8rem] leading-snug">
              {c.condition && <span className="text-gold">{c.condition}: </span>}
              <span className="text-success">{fmtRange(lo, hi)}</span>{' '}
              <span className="text-text-muted">
                {c.school}
                {scope ? ` (${scope})` : ''}
                {c.kind === 'dot' &&
                  ` every ${c.interval_s ?? 1}s × ${dotTicks(c)} ticks`}
                {c.suspect_low_value ? ' — bad data' : ''}
              </span>
            </div>
          )
        })}
        {(ability.procs ?? []).map((p, i) => {
          const amPart = p.components.every(c => !c.target_scope || c.target_scope === 'single')
            ? flatDamageMod(stats, p.components[0]?.school) * PROC_AM_SHARE
            : 0
          const lo = p.components.reduce((s, c) => s + c.min_dmg, 0) * (coeff + half) + amPart
          const hi = p.components.reduce((s, c) => s + c.max_dmg, 0) * (coeff + half) + amPart
          return (
            <div key={`p${i}`} className="text-[0.8rem] leading-snug">
              <span className="text-text-muted">{p.name}: </span>
              <span className="text-success">{fmtRange(lo, hi)}</span>
              <span className="text-text-muted">
                {p.trigger_count ? ` × ${p.trigger_count} per cast` : ` (${p.chance_pct.toFixed(0)}%)`}
              </span>
            </div>
          )
        })}
      </div>

      {expected > 0 && (
        <div className="text-[0.75rem] text-text-muted mt-1.5 border-t border-border pt-1">
          expected dealt / cast:{' '}
          <span className="text-gold font-medium">{fmtNum(Math.round(expected))}</span>
          <span className="text-[0.68rem]"> (crit · fervor · doublecast in)</span>
        </div>
      )}
    </div>,
    document.body,
  )
}
