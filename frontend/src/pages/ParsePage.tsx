import { Fragment, useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
import Caret from '../components/Caret'
import { useClasses } from '../useClasses'
import { fmtDuration, fmtLocalDateTime, fmtNum } from '../formatters'
import { Card } from '../components/ui'

// ── Types ─────────────────────────────────────────────────────────────────────

interface AttackSummary {
  attack_name: string
  damage: number
  hits: number
  swings: number
  crit_perc: number
  max_hit: number
}

interface DamageTypeBreakdown {
  damage_type: string
  damage: number
  dps: number
  hits: number
  swings: number
  max_hit: number
  crit_perc: number
}

interface HealSummary {
  heal_name: string
  healed: number
  hits: number
  swings: number
  crit_perc: number
  max_hit: number
  heal_type: string | null  // 'Hitpoints' (regular) or 'Absorption' (ward)
}

interface CureSummary {
  cure_name: string
  effects_removed: number
  times_cast: number
  max_at_once: number
}

interface ThreatSummary {
  ability_name: string
  value: number
  procs: number
  max_proc: number
  kind: string | null  // ACT's `resist` column — usually 'Increase'
}

interface CombatantSummary {
  id: number
  name: string
  ally: boolean
  // Identity frozen at ingest time. null for pets/NPCs, unresolved players,
  // and parses ingested before snapshots existed — we fall back to the live
  // /api/characters/lookup for those.
  level: number | null
  guild_name: string | null
  cls: string | null
  duration_s: number
  damage: number
  damage_perc: number
  dps: number
  encdps: number
  healed: number
  enchps: number
  heals: number
  crit_heals: number
  cure_dispels: number
  power_drain: number
  power_replenish: number
  heals_taken: number
  damage_taken: number
  threat_delta: number
  deaths: number
  kills: number
  crit_hits: number
  crit_dam_perc: number
  top_attacks: AttackSummary[]
  top_heals: HealSummary[]
  top_cures: CureSummary[]
  top_threats: ThreatSummary[]
  damage_types: DamageTypeBreakdown[]
}

interface ParseDetail {
  id: number
  act_encid: string
  title: string
  zone: string | null
  started_at: number
  ended_at: number
  duration_s: number
  total_damage: number
  encdps: number
  kills: number
  deaths: number
  success_level: number    // ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
  hidden?: boolean
  combatants: CombatantSummary[]
}

interface BulkLookupEntry {
  found: boolean
  guild_name: string | null
  cls: string | null
  level: number | null
}

interface BulkLookupResponse {
  results: Record<string, BulkLookupEntry>
}

// ── Helpers ───────────────────────────────────────────────────────────────────

// Single-word ally names are probably players (mirrors backend's player_count
// heuristic). Used to decide whether to render the name as a link.
function isLikelyPlayer(c: CombatantSummary): boolean {
  return c.ally && !c.name.includes(' ') && c.name !== 'Unknown' && c.name !== ''
}

// Subtle row tint derived from the class colour (alpha ~10%) — 8-digit hex.
// Takes the resolved hex colour (or null/undefined) and returns null when
// there's no colour, so the row stays untinted.
function rowTintFor(colour: string | null | undefined): string | null {
  return colour ? `${colour}1A` : null  // 0x1A = ~10% alpha
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ParsePage() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<ParseDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lookup, setLookup] = useState<Record<string, BulkLookupEntry>>({})

  // Fetch parse detail
  useEffect(() => {
    if (!id) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetch(`/api/parses/${encodeURIComponent(id)}`, { credentials: 'include' })
      .then(r => {
        if (r.status === 404) throw new Error('Parse not found.')
        if (!r.ok) throw new Error(`Server error ${r.status}`)
        return r.json()
      })
      .then((json: ParseDetail) => { if (!cancelled) setData(json) })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Unknown error') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id])

  // Once we have the parse, bulk-lookup likely-player names for guild info
  useEffect(() => {
    if (!data) return
    const names = data.combatants.filter(isLikelyPlayer).map(c => c.name)
    if (names.length === 0) return

    let cancelled = false
    const url = new URL('/api/characters/lookup', window.location.origin)
    url.searchParams.set('names', names.join(','))
    fetch(url.toString(), { credentials: 'include' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`Lookup failed ${r.status}`))))
      .then((json: BulkLookupResponse) => { if (!cancelled) setLookup(json.results) })
      .catch(() => { /* lookups are best-effort — failure just leaves names as plain links */ })
    return () => { cancelled = true }
  }, [data])

  const { allies, pets, enemies } = useMemo(() => {
    if (!data) return { allies: [], pets: [], enemies: [] }
    const byEncdps = (a: CombatantSummary, b: CombatantSummary) => b.encdps - a.encdps
    // Pet rule: ally + (multi-word/`Unknown` name OR Census lookup attempted
    // and not found). Single-word allies start out under Allies and only
    // migrate to Pets once we hear back from /api/characters/lookup, to
    // avoid showing real players as pets while the lookup is in-flight.
    const isPet = (c: CombatantSummary): boolean => {
      if (!c.ally) return false
      if (!isLikelyPlayer(c)) return true
      const entry = lookup[c.name]
      return entry !== undefined && entry.found === false
    }
    const allyCombatants = data.combatants.filter(c => c.ally)
    return {
      allies:  allyCombatants.filter(c => !isPet(c)).sort(byEncdps),
      pets:    allyCombatants.filter(isPet).sort(byEncdps),
      enemies: data.combatants.filter(c => !c.ally).sort(byEncdps),
    }
  }, [data, lookup])

  if (loading) {
    return (
      <main className={PAGE_CLS}>
        <Breadcrumb items={[{ label: 'Parses', to: '/parses' }, { label: '…' }]} />
        <p className="text-text-muted">Loading…</p>
      </main>
    )
  }

  if (error || !data) {
    return (
      <main className={PAGE_CLS}>
        <Breadcrumb items={[{ label: 'Parses', to: '/parses' }, { label: '…' }]} />
        <p className="text-danger">{error ?? 'Parse not found.'}</p>
      </main>
    )
  }

  return (
    <main className={PAGE_CLS}>
      <Breadcrumb items={[{ label: 'Parses', to: '/parses' }, { label: data.title }]} />
      <Header data={data} />
      {data.hidden && (
        <p className="text-text-muted text-[0.8rem] mb-3 border border-border rounded-md px-3 py-2">
          This parse has been removed from the parses list, but is preserved here because it holds a ranking.
        </p>
      )}
      {allies.length > 0 && (
        <CombatantSection title="Allies" combatants={allies} lookup={lookup} />
      )}
      {pets.length > 0 && (
        <CombatantSection title="Pets" combatants={pets} lookup={lookup} dimmed />
      )}
      {enemies.length > 0 && (
        <CombatantSection title="Enemies" combatants={enemies} lookup={lookup} dimmed />
      )}
    </main>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ data }: { data: ParseDetail }) {
  // Match the /parses list title-colour rule for visual consistency.
  // 1=win→green, 2=loss→red, 3=mixed→gold-warning, 0=unknown→default gold.
  const titleColor =
    data.success_level === 1 ? 'var(--success, #4caf50)'
    : data.success_level === 2 ? 'var(--danger, #e57373)'
    : data.success_level === 3 ? 'var(--warning, #d8a657)'
    : 'var(--gold)'
  return (
    <section className="mb-[1.4rem]">
      <h1
        className="font-heading text-[1.7rem] mb-1"
        style={{ color: titleColor }}
      >
        {data.title}
      </h1>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-text-muted text-[0.85rem]">
        {data.zone && <span><span className={HDR_KEY_CLS}>Zone:</span> {data.zone}</span>}
        <span><span className={HDR_KEY_CLS}>Started:</span> {fmtLocalDateTime(data.started_at)}</span>
        <span><span className={HDR_KEY_CLS}>Duration:</span> {fmtDuration(data.duration_s)}</span>
        <span><span className={HDR_KEY_CLS}>Damage:</span> {fmtNum(data.total_damage)}</span>
        <span><span className={HDR_KEY_CLS}>encDPS:</span> <span className="text-gold">{fmtNum(data.encdps)}</span></span>
        <span><span className={HDR_KEY_CLS}>K/D:</span> {data.kills} / {data.deaths}</span>
      </div>
    </section>
  )
}

const HDR_KEY_CLS = 'uppercase text-[0.7rem] tracking-[0.06em] text-text-muted opacity-70 mr-1'

// ── Combatant section ────────────────────────────────────────────────────────

function CombatantSection({
  title, combatants, lookup, dimmed = false,
}: {
  title: string
  combatants: CombatantSummary[]
  lookup: Record<string, BulkLookupEntry>
  dimmed?: boolean
}) {
  return (
    <section className="mb-[1.4rem]" style={{ opacity: dimmed ? 0.85 : 1 }}>
      <h2 className="font-heading text-[1.05rem] text-gold mb-2">
        {title} <span className="text-text-muted text-[0.75rem] font-normal">
          · {combatants.length}
        </span>
      </h2>
      <Card
        className="grid items-center text-[0.82rem] rounded-[6px] px-[0.6rem] py-[0.4rem] gap-x-2 gap-y-0"
        style={{
          gridTemplateColumns:
            'minmax(160px,1.6fr) 90px 80px 50px 90px 70px 90px 60px 40px',
        }}
      >
        <div className={HDR_CELL_CLS}>Name</div>
        <div className={`${HDR_CELL_CLS} text-right`}>DMG</div>
        <div className={`${HDR_CELL_CLS} text-right`}>encDPS</div>
        <div className={`${HDR_CELL_CLS} text-right`}>%</div>
        <div className={`${HDR_CELL_CLS} text-right`}>Healed</div>
        <div className={`${HDR_CELL_CLS} text-right`}>HPS</div>
        <div className={`${HDR_CELL_CLS} text-right`}>Taken</div>
        <div className={`${HDR_CELL_CLS} text-right`}>Crit%</div>
        <div className={`${HDR_CELL_CLS} text-right`}>D</div>

        {combatants.map(c => (
          <CombatantRow key={c.id} combatant={c} lookupEntry={lookup[c.name]} />
        ))}
      </Card>
    </section>
  )
}

function CombatantRow({
  combatant: c,
  lookupEntry,
}: {
  combatant: CombatantSummary
  lookupEntry: BulkLookupEntry | undefined
}) {
  const [open, setOpen] = useState(false)
  const player = isLikelyPlayer(c)
  // Prefer the parse-time snapshot stored on the row; fall back to the live
  // lookup for parses ingested before snapshots existed.
  const guildName = c.guild_name ?? lookupEntry?.guild_name ?? null
  const { byName } = useClasses()
  const cls = c.cls ?? lookupEntry?.cls ?? null
  const level = c.level ?? lookupEntry?.level ?? null
  const tint = rowTintFor(cls ? byName.get(cls)?.colour : null)

  return (
    <>
      <div
        onClick={() => setOpen(v => !v)}
        className="col-[1/-1] grid grid-cols-subgrid items-center px-2 py-[0.35rem] -mx-2 border-t border-border cursor-pointer"
        style={{ background: tint ?? 'transparent' }}
      >
        <div className="flex items-center gap-[0.35rem] min-w-0">
          <Caret open={open} />
          <NameCell combatant={c} player={player} level={level} guildName={guildName} cls={cls} />
        </div>
        <div className={CELL_RIGHT_CLS}>{fmtNum(c.damage)}</div>
        <div className={`${CELL_RIGHT_CLS} text-gold`}>{fmtNum(c.encdps)}</div>
        <div className={CELL_RIGHT_CLS}>{c.damage_perc > 0 ? `${Math.round(c.damage_perc)}%` : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.healed > 0 ? fmtNum(c.healed) : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.enchps > 0 ? fmtNum(c.enchps) : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.damage_taken > 0 ? fmtNum(c.damage_taken) : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.crit_dam_perc > 0 ? `${Math.round(c.crit_dam_perc)}%` : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.deaths > 0 ? c.deaths : ''}</div>
      </div>

      {open && (
        <div className="col-[1/-1] pt-[0.4rem] pb-[0.6rem] pl-6">
          <CombatantTabs combatant={c} />
        </div>
      )}
    </>
  )
}

function NameCell({
  combatant: c, player, level, guildName, cls,
}: {
  combatant: CombatantSummary
  player: boolean
  level: number | null
  guildName: string | null
  cls: string | null
}) {
  const { colourFor } = useClasses()
  const baseColor = c.ally ? 'var(--text)' : 'var(--text-muted)'

  if (!player) {
    return (
      <span
        className="overflow-hidden text-ellipsis whitespace-nowrap"
        style={{ color: baseColor }}
      >
        {c.name}
      </span>
    )
  }

  const classColor = cls ? colourFor(cls) : null

  return (
    <span
      onClick={e => e.stopPropagation()}
      className="flex items-center gap-[0.4rem] min-w-0"
    >
      <Link
        to={`/character/${encodeURIComponent(c.name)}`}
        className="text-text underline decoration-dotted decoration-text-muted underline-offset-[3px] overflow-hidden text-ellipsis whitespace-nowrap"
      >
        {c.name}
      </Link>
      {level != null && (
        <span className="text-[0.75rem] text-text-muted whitespace-nowrap tabular-nums">({level})</span>
      )}
      {guildName && (
        <Link
          to={`/guild/${encodeURIComponent(guildName)}`}
          className="text-[0.7rem] text-text-muted no-underline hover:underline hover:decoration-dotted decoration-text-muted underline-offset-2 whitespace-nowrap"
        >
          ‹{guildName}›
        </Link>
      )}
      {cls && (
        <span
          className="text-[0.7rem] whitespace-nowrap opacity-85"
          style={{ color: classColor ?? 'var(--text-muted)' }}
        >
          ‹{cls}›
        </span>
      )}
    </span>
  )
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
      <div className="flex flex-wrap gap-[0.3rem] mb-[0.4rem]">
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
      className="border border-solid rounded-sm px-[0.65rem] py-[0.2rem] text-[0.74rem]"
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
              style={{ color: isWard ? '#93d9ff' : 'var(--text-muted)' }}
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
      className="grid items-center gap-x-[0.6rem] gap-y-px text-[0.78rem]"
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
      className="grid items-center gap-x-[0.6rem] gap-y-px text-[0.78rem]"
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
          className="border border-border rounded-[5px] px-[0.7rem] py-[0.55rem]"
          style={{ background: 'rgba(0,0,0,0.18)' }}
        >
          <h4 className="mb-[0.35rem] text-[0.7rem] text-text-muted uppercase tracking-[0.06em]">{g.label}</h4>
          <div
            className="grid gap-x-2 gap-y-[2px] text-[0.8rem]"
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
    <div className="w-full h-[6px] bg-white/6 rounded-[3px] overflow-hidden">
      <div
        className="h-full bg-gold opacity-70"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

// ── Caret ────────────────────────────────────────────────────────────────────

// ── Style constants ──────────────────────────────────────────────────────────

const PAGE_CLS = 'max-w-[1100px] mx-auto px-4 py-6'

const CELL_RIGHT_CLS = 'text-right text-text'

const HDR_CELL_CLS = 'text-text-muted text-[0.7rem] uppercase tracking-[0.06em] py-[0.15rem]'

const HDR_SUB_CELL_CLS = 'text-text-muted text-[0.66rem] uppercase tracking-[0.05em] py-[0.1rem] border-b border-border mb-[0.15rem]'

const SUB_ROW_CLS = 'col-[1/-1] grid grid-cols-subgrid items-center py-[2px]'
