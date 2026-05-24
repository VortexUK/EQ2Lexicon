import type { CSSProperties } from 'react'
import { Fragment, useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
import { CLASS_COLOURS } from '../classConstants'

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

function fmtNum(n: number): string {
  return Math.round(n).toLocaleString()
}

function fmtDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m${String(s).padStart(2, '0')}s`
}

function fmtLocalDateTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// Single-word ally names are probably players (mirrors backend's player_count
// heuristic). Used to decide whether to render the name as a link.
function isLikelyPlayer(c: CombatantSummary): boolean {
  return c.ally && !c.name.includes(' ') && c.name !== 'Unknown' && c.name !== ''
}

// Subtle row tint derived from the archetype colour (alpha ~10%) — 8-digit
// hex format. Returns null when the class is unknown (no cache hit, NPC, or
// a class we don't recognise) so the row stays untinted.
function rowTintFor(cls: string | null | undefined): string | null {
  if (!cls) return null
  const base = CLASS_COLOURS[cls]
  if (!base) return null
  return `${base}1A`  // 0x1A = ~10% alpha
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
      <main style={pageStyle}>
        <Breadcrumb items={[{ label: 'Parses', to: '/parses' }, { label: '…' }]} />
        <p style={{ color: 'var(--text-muted)' }}>Loading…</p>
      </main>
    )
  }

  if (error || !data) {
    return (
      <main style={pageStyle}>
        <Breadcrumb items={[{ label: 'Parses', to: '/parses' }, { label: '…' }]} />
        <p style={{ color: 'var(--danger)' }}>{error ?? 'Parse not found.'}</p>
      </main>
    )
  }

  return (
    <main style={pageStyle}>
      <Breadcrumb items={[{ label: 'Parses', to: '/parses' }, { label: data.title }]} />
      <Header data={data} />
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
  return (
    <section style={{ marginBottom: '1.4rem' }}>
      <h1 style={{
        fontFamily: 'var(--font-heading)',
        fontSize: '1.7rem',
        color: 'var(--gold)',
        margin: '0 0 0.25rem',
      }}>
        {data.title}
      </h1>
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: '0.5rem 1.5rem',
        color: 'var(--text-muted)', fontSize: '0.85rem',
      }}>
        {data.zone && <span><span style={hdrKey}>Zone:</span> {data.zone}</span>}
        <span><span style={hdrKey}>Started:</span> {fmtLocalDateTime(data.started_at)}</span>
        <span><span style={hdrKey}>Duration:</span> {fmtDuration(data.duration_s)}</span>
        <span><span style={hdrKey}>Damage:</span> {fmtNum(data.total_damage)}</span>
        <span><span style={hdrKey}>encDPS:</span> <span style={{ color: 'var(--gold)' }}>{fmtNum(data.encdps)}</span></span>
        <span><span style={hdrKey}>K/D:</span> {data.kills} / {data.deaths}</span>
      </div>
    </section>
  )
}

const hdrKey: CSSProperties = {
  textTransform: 'uppercase',
  fontSize: '0.7rem',
  letterSpacing: '0.06em',
  color: 'var(--text-muted)',
  opacity: 0.7,
  marginRight: '0.25rem',
}

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
    <section style={{ marginBottom: '1.4rem', opacity: dimmed ? 0.85 : 1 }}>
      <h2 style={{
        fontFamily: 'var(--font-heading)',
        fontSize: '1.05rem',
        color: 'var(--gold)',
        margin: '0 0 0.5rem',
      }}>
        {title} <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', fontWeight: 400 }}>
          · {combatants.length}
        </span>
      </h2>
      <div style={{
        display: 'grid',
        gridTemplateColumns:
          'minmax(160px,1.6fr) 90px 80px 50px 90px 70px 90px 60px 40px',
        columnGap: '0.5rem',
        rowGap: 0,
        alignItems: 'center',
        fontSize: '0.82rem',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: '0.4rem 0.6rem',
      }}>
        <div style={hdrCellStyle}>Name</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>DMG</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>encDPS</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>%</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>Healed</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>HPS</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>Taken</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>Crit%</div>
        <div style={{ ...hdrCellStyle, textAlign: 'right' }}>D</div>

        {combatants.map(c => (
          <CombatantRow key={c.id} combatant={c} lookupEntry={lookup[c.name]} />
        ))}
      </div>
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
  const guildName = lookupEntry?.guild_name ?? null
  const cls = lookupEntry?.cls ?? null
  const tint = rowTintFor(cls)

  return (
    <>
      <div
        onClick={() => setOpen(v => !v)}
        style={{
          gridColumn: '1 / -1',
          display: 'grid',
          gridTemplateColumns: 'subgrid',
          alignItems: 'center',
          padding: '0.35rem 0.5rem',
          marginLeft: '-0.5rem',
          marginRight: '-0.5rem',
          borderTop: '1px solid var(--border)',
          cursor: 'pointer',
          background: tint ?? 'transparent',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', minWidth: 0 }}>
          <Caret open={open} />
          <NameCell combatant={c} player={player} guildName={guildName} cls={cls} />
        </div>
        <div style={cellRight}>{fmtNum(c.damage)}</div>
        <div style={{ ...cellRight, color: 'var(--gold)' }}>{fmtNum(c.encdps)}</div>
        <div style={cellRight}>{c.damage_perc > 0 ? `${Math.round(c.damage_perc)}%` : '—'}</div>
        <div style={cellRight}>{c.healed > 0 ? fmtNum(c.healed) : '—'}</div>
        <div style={cellRight}>{c.enchps > 0 ? fmtNum(c.enchps) : '—'}</div>
        <div style={cellRight}>{c.damage_taken > 0 ? fmtNum(c.damage_taken) : '—'}</div>
        <div style={cellRight}>{c.crit_dam_perc > 0 ? `${Math.round(c.crit_dam_perc)}%` : '—'}</div>
        <div style={cellRight}>{c.deaths > 0 ? c.deaths : ''}</div>
      </div>

      {open && (
        <div style={{ gridColumn: '1 / -1', padding: '0.4rem 0 0.6rem 1.5rem' }}>
          <CombatantTabs combatant={c} />
        </div>
      )}
    </>
  )
}

function NameCell({
  combatant: c, player, guildName, cls,
}: {
  combatant: CombatantSummary
  player: boolean
  guildName: string | null
  cls: string | null
}) {
  const baseColor = c.ally ? 'var(--text)' : 'var(--text-muted)'

  if (!player) {
    return (
      <span style={{
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        color: baseColor,
      }}>
        {c.name}
      </span>
    )
  }

  const classColor = cls ? (CLASS_COLOURS[cls] ?? 'var(--text-muted)') : null

  return (
    <span
      onClick={e => e.stopPropagation()}
      style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', minWidth: 0 }}
    >
      <Link
        to={`/character/${encodeURIComponent(c.name)}`}
        style={{
          color: 'var(--text)',
          textDecoration: 'none',
          borderBottom: '1px dotted var(--text-muted)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {c.name}
      </Link>
      {guildName && (
        <Link
          to={`/guild/${encodeURIComponent(guildName)}`}
          style={{
            fontSize: '0.7rem',
            color: 'var(--text-muted)',
            textDecoration: 'none',
            borderBottom: '1px dotted transparent',
            whiteSpace: 'nowrap',
          }}
          onMouseEnter={e => (e.currentTarget.style.borderBottomColor = 'var(--text-muted)')}
          onMouseLeave={e => (e.currentTarget.style.borderBottomColor = 'transparent')}
        >
          ‹{guildName}›
        </Link>
      )}
      {cls && (
        <span style={{
          fontSize: '0.7rem',
          color: classColor ?? 'var(--text-muted)',
          whiteSpace: 'nowrap',
          opacity: 0.85,
        }}>
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
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', marginBottom: '0.4rem' }}>
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
      style={{
        background: active ? 'var(--surface)' : 'transparent',
        border: '1px solid',
        borderColor: active ? 'var(--gold)' : 'var(--border)',
        color: active ? 'var(--gold)' : disabled ? 'var(--text-muted)' : 'var(--text)',
        opacity: disabled ? 0.45 : 1,
        borderRadius: 4,
        padding: '0.2rem 0.65rem',
        fontSize: '0.74rem',
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
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(150px,1.4fr) 80px 50px 60px 60px 70px 1fr',
      columnGap: '0.5rem',
      rowGap: '1px',
      alignItems: 'center',
      fontSize: '0.78rem',
    }}>
      <div style={hdrSubCellStyle}>Attack</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>DMG</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Hits</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Swings</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Crit%</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Max</div>
      <div style={hdrSubCellStyle}>Share</div>

      {attacks.map((a, i) => {
        const share = combatantDamage > 0 ? a.damage / combatantDamage : 0
        return (
          <div key={i} style={subRow}>
            <div style={{ color: 'var(--text)' }}>{a.attack_name}</div>
            <div style={cellRight}>{fmtNum(a.damage)}</div>
            <div style={cellRight}>{a.hits}</div>
            <div style={cellRight}>{a.swings}</div>
            <div style={cellRight}>{Math.round(a.crit_perc)}%</div>
            <div style={cellRight}>{fmtNum(a.max_hit)}</div>
            <div style={{ paddingRight: '0.5rem' }}><DamageBar share={share} /></div>
          </div>
        )
      })}
    </div>
  )
}

function DamageTypesTable({ types, combatantDamage }: { types: DamageTypeBreakdown[]; combatantDamage: number }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(140px,1.2fr) 80px 70px 50px 60px 70px 1fr',
      columnGap: '0.5rem',
      rowGap: '1px',
      alignItems: 'center',
      fontSize: '0.78rem',
    }}>
      <div style={hdrSubCellStyle}>Damage Type</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>DMG</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>DPS</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Hits</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Crit%</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Max</div>
      <div style={hdrSubCellStyle}>Share</div>

      {types.map((t, i) => {
        const share = combatantDamage > 0 ? t.damage / combatantDamage : 0
        return (
          <div key={i} style={subRow}>
            <div style={{ color: 'var(--text)' }}>{t.damage_type || '—'}</div>
            <div style={cellRight}>{fmtNum(t.damage)}</div>
            <div style={cellRight}>{fmtNum(t.dps)}</div>
            <div style={cellRight}>{t.hits}</div>
            <div style={cellRight}>{Math.round(t.crit_perc)}%</div>
            <div style={cellRight}>{fmtNum(t.max_hit)}</div>
            <div style={{ paddingRight: '0.5rem' }}><DamageBar share={share} /></div>
          </div>
        )
      })}
    </div>
  )
}

function HealsTable({ heals, totalHealed }: { heals: HealSummary[]; totalHealed: number }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(150px,1.4fr) 80px 50px 60px 70px 90px 1fr',
      columnGap: '0.5rem',
      rowGap: '1px',
      alignItems: 'center',
      fontSize: '0.78rem',
    }}>
      <div style={hdrSubCellStyle}>Ability</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Healed</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Hits</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Crit%</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Max</div>
      <div style={hdrSubCellStyle}>Type</div>
      <div style={hdrSubCellStyle}>Share</div>

      {heals.map((h, i) => {
        const share = totalHealed > 0 ? h.healed / totalHealed : 0
        const isWard = h.heal_type === 'Absorption'
        return (
          <div key={i} style={subRow}>
            <div style={{ color: 'var(--text)' }}>{h.heal_name}</div>
            <div style={cellRight}>{fmtNum(h.healed)}</div>
            <div style={cellRight}>{h.hits}</div>
            <div style={cellRight}>{Math.round(h.crit_perc)}%</div>
            <div style={cellRight}>{fmtNum(h.max_hit)}</div>
            <div style={{
              fontSize: '0.7rem',
              color: isWard ? '#93d9ff' : 'var(--text-muted)',
            }}>
              {h.heal_type ?? '—'}
            </div>
            <div style={{ paddingRight: '0.5rem' }}><DamageBar share={share} /></div>
          </div>
        )
      })}
    </div>
  )
}

function CuresTable({ cures }: { cures: CureSummary[] }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(160px,1.4fr) 100px 100px 80px',
      columnGap: '0.6rem',
      rowGap: '1px',
      alignItems: 'center',
      fontSize: '0.78rem',
    }}>
      <div style={hdrSubCellStyle}>Cure ability</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Effects removed</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Times cast</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Max</div>
      {cures.map((cu, i) => (
        <div key={i} style={subRow}>
          <div style={{ color: 'var(--text)' }}>{cu.cure_name}</div>
          <div style={cellRight}>{fmtNum(cu.effects_removed)}</div>
          <div style={cellRight}>{cu.times_cast}</div>
          <div style={cellRight}>{cu.max_at_once}</div>
        </div>
      ))}
    </div>
  )
}

function ThreatTable({ threats }: { threats: ThreatSummary[] }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(160px,1.4fr) 100px 70px 80px 90px',
      columnGap: '0.6rem',
      rowGap: '1px',
      alignItems: 'center',
      fontSize: '0.78rem',
    }}>
      <div style={hdrSubCellStyle}>Ability</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Value</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Procs</div>
      <div style={{ ...hdrSubCellStyle, textAlign: 'right' }}>Max proc</div>
      <div style={hdrSubCellStyle}>Kind</div>
      {threats.map((t, i) => (
        <div key={i} style={subRow}>
          <div style={{ color: 'var(--text)' }}>{t.ability_name}</div>
          <div style={cellRight}>{fmtNum(t.value)}</div>
          <div style={cellRight}>{t.procs}</div>
          <div style={cellRight}>{fmtNum(t.max_proc)}</div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{t.kind ?? '—'}</div>
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
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
      {groups.map(g => (
        <div key={g.label} style={{
          background: 'rgba(0,0,0,0.18)',
          border: '1px solid var(--border)',
          borderRadius: 5,
          padding: '0.55rem 0.7rem',
        }}>
          <h4 style={{
            margin: '0 0 0.35rem',
            fontSize: '0.7rem',
            color: 'var(--text-muted)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}>{g.label}</h4>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', columnGap: '0.5rem', rowGap: '2px', fontSize: '0.8rem' }}>
            {g.stats.map(([k, v]) => (
              <Fragment key={`${g.label}-${k}`}>
                <span style={{ color: 'var(--text-muted)' }}>{k}</span>
                <span style={{ color: 'var(--text)', textAlign: 'right' }}>{v}</span>
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
    <div style={{
      width: '100%',
      height: 6,
      background: 'rgba(255,255,255,0.06)',
      borderRadius: 3,
      overflow: 'hidden',
    }}>
      <div style={{
        width: `${pct}%`,
        height: '100%',
        background: 'var(--gold)',
        opacity: 0.7,
      }} />
    </div>
  )
}

// ── Caret ────────────────────────────────────────────────────────────────────

function Caret({ open }: { open: boolean }) {
  return (
    <span style={{
      display: 'inline-block',
      width: '0.65rem',
      transform: `rotate(${open ? 90 : 0}deg)`,
      transition: 'transform 0.15s',
      fontSize: '0.7rem',
      color: 'var(--text-muted)',
    }}>
      ▶
    </span>
  )
}

// ── Style constants ──────────────────────────────────────────────────────────

const pageStyle: CSSProperties = { maxWidth: 1100, margin: '0 auto', padding: '1.5rem 1rem' }

const cellRight: CSSProperties = { textAlign: 'right', color: 'var(--text)' }

const hdrCellStyle: CSSProperties = {
  color: 'var(--text-muted)',
  fontSize: '0.7rem',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  padding: '0.15rem 0',
}

const hdrSubCellStyle: CSSProperties = {
  color: 'var(--text-muted)',
  fontSize: '0.66rem',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  padding: '0.1rem 0',
  borderBottom: '1px solid var(--border)',
  marginBottom: '0.15rem',
}

const subRow: CSSProperties = {
  gridColumn: '1 / -1',
  display: 'grid',
  gridTemplateColumns: 'subgrid',
  alignItems: 'center',
  padding: '2px 0',
}
