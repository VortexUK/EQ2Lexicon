import { Fragment, useState } from 'react'
import { fmtNum } from '../../formatters'
import { CELL_RIGHT_CLS } from '../ParsePage'
import type { CombatantSummary } from '../ParsePage'

// ── Sub-table types ──────────────────────────────────────────────────────────

export interface AttackSummary {
  attack_name: string
  damage: number
  hits: number
  swings: number
  crit_perc: number
  max_hit: number
}

export interface DamageTypeBreakdown {
  damage_type: string
  damage: number
  dps: number
  hits: number
  swings: number
  max_hit: number
  crit_perc: number
}

export interface HealSummary {
  heal_name: string
  healed: number
  hits: number
  swings: number
  crit_perc: number
  max_hit: number
  heal_type: string | null  // 'Hitpoints' (regular) or 'Absorption' (ward)
}

export interface CureSummary {
  cure_name: string
  effects_removed: number
  times_cast: number
  max_at_once: number
}

export interface ThreatSummary {
  ability_name: string
  value: number
  procs: number
  max_proc: number
  kind: string | null  // ACT's `resist` column — usually 'Increase'
}

// ── Style constants (local to the detail panel) ──────────────────────────────

const HDR_SUB_CELL_CLS = 'text-text-muted text-[0.66rem] uppercase tracking-[0.05em] py-0.5 border-b border-border mb-0.5'

const SUB_ROW_CLS = 'col-[1/-1] grid grid-cols-subgrid items-center py-[2px]'

// ── CombatantDetailPanel ─────────────────────────────────────────────────────

interface Props {
  combatant: CombatantSummary
}

export function CombatantDetailPanel({ combatant }: Props) {
  return <CombatantTabs combatant={combatant} />
}

// ── Per-combatant tabs ───────────────────────────────────────────────────────

type TabId = 'damage' | 'types' | 'heals' | 'cures' | 'threat' | 'summary'

function CombatantTabs({ combatant: c }: { combatant: CombatantSummary }) {
  const [tab, setTab] = useState<TabId>('damage')
  const hasDamage = c.top_attacks.length > 0
  const hasTypes  = c.damage_types.length > 0
  const hasHeals  = c.top_heals.length > 0
  const hasCures  = c.top_cures.length > 0
  const hasThreat = c.top_threats.length > 0

  const has: Record<TabId, boolean> = {
    damage: hasDamage, types: hasTypes, heals: hasHeals,
    cures: hasCures, threat: hasThreat, summary: true,
  }
  // If the active tab has no data (e.g. healer landed on Damage with 0 rows),
  // fall through to the next non-empty tab.
  const order: TabId[] = ['damage', 'types', 'heals', 'cures', 'threat', 'summary']
  const effectiveTab: TabId = has[tab] ? tab : order.find(t => has[t]) ?? 'summary'

  return (
    <div>
      <div className="flex flex-wrap gap-1 mb-1.5">
        <TabBtn id="damage"  current={effectiveTab} disabled={!hasDamage} onClick={setTab}>Damage</TabBtn>
        <TabBtn id="types"   current={effectiveTab} disabled={!hasTypes}  onClick={setTab}>By Type</TabBtn>
        <TabBtn id="heals"   current={effectiveTab} disabled={!hasHeals}  onClick={setTab}>Healed</TabBtn>
        <TabBtn id="cures"   current={effectiveTab} disabled={!hasCures}  onClick={setTab}>Cures</TabBtn>
        <TabBtn id="threat"  current={effectiveTab} disabled={!hasThreat} onClick={setTab}>Threat</TabBtn>
        <TabBtn id="summary" current={effectiveTab} disabled={false}      onClick={setTab}>Summary</TabBtn>
      </div>
      {effectiveTab === 'damage'  && <AttacksTable attacks={c.top_attacks} combatantDamage={c.damage} />}
      {effectiveTab === 'types'   && <DamageTypesTable types={c.damage_types} combatantDamage={c.damage} />}
      {effectiveTab === 'heals'   && <HealsTable heals={c.top_heals} totalHealed={c.healed} />}
      {effectiveTab === 'cures'   && <CuresTable cures={c.top_cures} />}
      {effectiveTab === 'threat'  && <ThreatTable threats={c.top_threats} />}
      {effectiveTab === 'summary' && <SummaryCard combatant={c} />}
    </div>
  )
}

function TabBtn({
  id, current, disabled, onClick, children,
}: {
  id: TabId
  current: TabId
  disabled: boolean
  onClick: (id: TabId) => void
  children: React.ReactNode
}) {
  const active = id === current
  return (
    <button
      disabled={disabled}
      onClick={() => onClick(id)}
      className="border border-solid rounded-sm px-2.5 py-1 text-[0.74rem]"
      style={{
        background: active ? 'var(--surface)' : 'transparent',
        borderColor: active ? 'var(--gold)' : 'var(--border)',
        color: active ? 'var(--gold)' : disabled ? 'var(--text-muted)' : 'var(--text)',
        opacity: disabled ? 0.45 : 1,
        fontWeight: active ? 700 : 500,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {children}
    </button>
  )
}

// ── Tab content ──────────────────────────────────────────────────────────────

function AttacksTable({ attacks, combatantDamage }: { attacks: AttackSummary[]; combatantDamage: number }) {
  return (
    <div
      className="grid items-center gap-x-2 gap-y-px text-[0.78rem]"
      style={{ gridTemplateColumns: 'minmax(150px,1.4fr) 80px 50px 60px 60px 70px 1fr' }}
    >
      <div className={HDR_SUB_CELL_CLS}>Attack</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>DMG</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Hits</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Swings</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Crit%</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Max</div>
      <div className={HDR_SUB_CELL_CLS}>Share</div>

      {attacks.map((a, i) => {
        const share = combatantDamage > 0 ? a.damage / combatantDamage : 0
        return (
          <div key={i} className={SUB_ROW_CLS}>
            <div className="text-text">{a.attack_name}</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(a.damage)}</div>
            <div className={CELL_RIGHT_CLS}>{a.hits}</div>
            <div className={CELL_RIGHT_CLS}>{a.swings}</div>
            <div className={CELL_RIGHT_CLS}>{Math.round(a.crit_perc)}%</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(a.max_hit)}</div>
            <div className="pr-2"><DamageBar share={share} /></div>
          </div>
        )
      })}
    </div>
  )
}

function DamageTypesTable({ types, combatantDamage }: { types: DamageTypeBreakdown[]; combatantDamage: number }) {
  return (
    <div
      className="grid items-center gap-x-2 gap-y-px text-[0.78rem]"
      style={{ gridTemplateColumns: 'minmax(140px,1.2fr) 80px 70px 50px 60px 70px 1fr' }}
    >
      <div className={HDR_SUB_CELL_CLS}>Damage Type</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>DMG</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>DPS</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Hits</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Crit%</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Max</div>
      <div className={HDR_SUB_CELL_CLS}>Share</div>

      {types.map((t, i) => {
        const share = combatantDamage > 0 ? t.damage / combatantDamage : 0
        return (
          <div key={i} className={SUB_ROW_CLS}>
            <div className="text-text">{t.damage_type || '—'}</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(t.damage)}</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(t.dps)}</div>
            <div className={CELL_RIGHT_CLS}>{t.hits}</div>
            <div className={CELL_RIGHT_CLS}>{Math.round(t.crit_perc)}%</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(t.max_hit)}</div>
            <div className="pr-2"><DamageBar share={share} /></div>
          </div>
        )
      })}
    </div>
  )
}

function HealsTable({ heals, totalHealed }: { heals: HealSummary[]; totalHealed: number }) {
  return (
    <div
      className="grid items-center gap-x-2 gap-y-px text-[0.78rem]"
      style={{ gridTemplateColumns: 'minmax(150px,1.4fr) 80px 50px 60px 70px 90px 1fr' }}
    >
      <div className={HDR_SUB_CELL_CLS}>Ability</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Healed</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Hits</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Crit%</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Max</div>
      <div className={HDR_SUB_CELL_CLS}>Type</div>
      <div className={HDR_SUB_CELL_CLS}>Share</div>

      {heals.map((h, i) => {
        const share = totalHealed > 0 ? h.healed / totalHealed : 0
        const isWard = h.heal_type === 'Absorption'
        return (
          <div key={i} className={SUB_ROW_CLS}>
            <div className="text-text">{h.heal_name}</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(h.healed)}</div>
            <div className={CELL_RIGHT_CLS}>{h.hits}</div>
            <div className={CELL_RIGHT_CLS}>{Math.round(h.crit_perc)}%</div>
            <div className={CELL_RIGHT_CLS}>{fmtNum(h.max_hit)}</div>
            <div
              className="text-[0.7rem]"
              style={{ color: isWard ? 'var(--rarity-treasured)' : 'var(--text-muted)' }}
            >
              {h.heal_type ?? '—'}
            </div>
            <div className="pr-2"><DamageBar share={share} /></div>
          </div>
        )
      })}
    </div>
  )
}

function CuresTable({ cures }: { cures: CureSummary[] }) {
  return (
    <div
      className="grid items-center gap-x-2.5 gap-y-px text-[0.78rem]"
      style={{ gridTemplateColumns: 'minmax(160px,1.4fr) 100px 100px 80px' }}
    >
      <div className={HDR_SUB_CELL_CLS}>Cure ability</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Effects removed</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Times cast</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Max</div>
      {cures.map((cu, i) => (
        <div key={i} className={SUB_ROW_CLS}>
          <div className="text-text">{cu.cure_name}</div>
          <div className={CELL_RIGHT_CLS}>{fmtNum(cu.effects_removed)}</div>
          <div className={CELL_RIGHT_CLS}>{cu.times_cast}</div>
          <div className={CELL_RIGHT_CLS}>{cu.max_at_once}</div>
        </div>
      ))}
    </div>
  )
}

function ThreatTable({ threats }: { threats: ThreatSummary[] }) {
  return (
    <div
      className="grid items-center gap-x-2.5 gap-y-px text-[0.78rem]"
      style={{ gridTemplateColumns: 'minmax(160px,1.4fr) 100px 70px 80px 90px' }}
    >
      <div className={HDR_SUB_CELL_CLS}>Ability</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Value</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Procs</div>
      <div className={`${HDR_SUB_CELL_CLS} text-right`}>Max proc</div>
      <div className={HDR_SUB_CELL_CLS}>Kind</div>
      {threats.map((t, i) => (
        <div key={i} className={SUB_ROW_CLS}>
          <div className="text-text">{t.ability_name}</div>
          <div className={CELL_RIGHT_CLS}>{fmtNum(t.value)}</div>
          <div className={CELL_RIGHT_CLS}>{t.procs}</div>
          <div className={CELL_RIGHT_CLS}>{fmtNum(t.max_proc)}</div>
          <div className="text-[0.7rem] text-text-muted">{t.kind ?? '—'}</div>
        </div>
      ))}
    </div>
  )
}

function SummaryCard({ combatant: c }: { combatant: CombatantSummary }) {
  const groups: { label: string; stats: [string, string | number][] }[] = [
    {
      label: 'Outgoing',
      stats: [
        ['Damage',       fmtNum(c.damage)],
        ['encDPS',       fmtNum(c.encdps)],
        ['Personal DPS', fmtNum(c.dps)],
        ['Crit hits',    c.crit_hits],
        ['Crit %',       c.crit_dam_perc > 0 ? `${Math.round(c.crit_dam_perc)}%` : '—'],
        ['Kills',        c.kills],
      ],
    },
    {
      label: 'Healing & Support (Out)',
      stats: [
        ['Healed',          fmtNum(c.healed)],
        ['Heals cast',      c.heals],
        ['Crit heals',      c.crit_heals],
        ['HPS',             c.enchps > 0 ? fmtNum(c.enchps) : '—'],
        ['Cures / Dispels', c.cure_dispels],
        ['Power drain',     fmtNum(c.power_drain)],
        ['Power replenish', fmtNum(c.power_replenish)],
      ],
    },
    {
      label: 'Incoming',
      stats: [
        ['Damage taken', fmtNum(c.damage_taken)],
        ['Heals taken',  fmtNum(c.heals_taken)],
        ['Deaths',       c.deaths],
      ],
    },
    {
      label: 'Threat',
      stats: [
        ['Threat Δ', c.threat_delta.toLocaleString()],
      ],
    },
  ]
  return (
    <div
      className="grid gap-3"
      style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}
    >
      {groups.map(g => (
        <div
          key={g.label}
          className="border border-border rounded-sm px-3 py-2"
          style={{ background: 'rgba(0,0,0,0.18)' }}
        >
          <h4 className="mb-1.5 text-[0.7rem] text-text-muted uppercase tracking-[0.06em]">{g.label}</h4>
          <div
            className="grid gap-x-2 gap-y-0.5 text-[0.8rem]"
            style={{ gridTemplateColumns: '1fr auto' }}
          >
            {g.stats.map(([k, v]) => (
              <Fragment key={`${g.label}-${k}`}>
                <span className="text-text-muted">{k}</span>
                <span className="text-text text-right">{v}</span>
              </Fragment>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function DamageBar({ share }: { share: number }) {
  const pct = Math.max(0, Math.min(1, share)) * 100
  return (
    <div className="w-full h-[6px] bg-white/6 rounded-full overflow-hidden">
      <div
        className="h-full bg-gold opacity-70"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
