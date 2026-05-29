import { useEffect, useMemo, useState } from 'react'
import { useFetch } from '../hooks/useFetch'
import { useParams, Link } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
import Caret from '../components/Caret'
import { UploaderTag } from '../components/UploaderTag'
import { useClasses } from '../useClasses'
import { fmtDuration, fmtLocalDateTime, fmtNum } from '../formatters'
import { percentileColor } from '../percentileColors'
import { Card } from '../components/ui'
import { CombatantDetailPanel } from './parse/CombatantDetailPanel'
import type { AttackSummary, DamageTypeBreakdown, HealSummary, CureSummary, ThreatSummary } from './parse/CombatantDetailPanel'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface CombatantSummary {
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
  dps_percentile: number | null
  dps_best_overall: boolean
  hps_percentile: number | null
  hps_best_overall: boolean
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
  // Who uploaded this specific parse — character name + Discord identity.
  // Both Discord fields are null on pre-plugin / local uploads.
  uploaded_by: string
  uploader_discord_id: string | null
  uploader_display_name: string | null
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
  // A class snapshot resolved at ingest is proof this is a real character.
  if (c.cls) return c.ally
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
  const { data, loading, error } = useFetch<ParseDetail>(
    id ? `/api/parses/${encodeURIComponent(id)}` : null,
  )
  const [lookup, setLookup] = useState<Record<string, BulkLookupEntry>>({})
  // Canonical zone name from zones.db when the parse's `zone` field matches a
  // curated raid zone (incl. via alias). Null when it doesn't — header then
  // renders the zone text as plain (no cross-link).
  const [raidZoneCanonical, setRaidZoneCanonical] = useState<string | null>(null)

  // Resolve the parse's zone against the curated raid roster. 200 + a `name`
  // field means it's a known raid zone (find_by_name handles aliases too); the
  // canonical name is what the /raids/:name route expects. Any non-200 just
  // leaves the link off — graceful for non-raid parses (group, solo, weirdly
  // named zones).
  useEffect(() => {
    setRaidZoneCanonical(null)
    if (!data?.zone) return
    let cancelled = false
    fetch(`/api/zones/${encodeURIComponent(data.zone)}`, { credentials: 'include' })
      .then(r => (r.ok ? r.json() : null))
      .then(j => {
        if (!cancelled && j && typeof j.name === 'string') setRaidZoneCanonical(j.name)
      })
      .catch(() => { /* best-effort — silent on failure */ })
    return () => { cancelled = true }
  }, [data?.zone])

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
      if (c.cls) return false // resolved a class at ingest → a real player, never a pet
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
      <Header data={data} raidZoneCanonical={raidZoneCanonical} />
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

function Header({ data, raidZoneCanonical }: { data: ParseDetail; raidZoneCanonical: string | null }) {
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
        {data.zone && (
          <span>
            <span className={HDR_KEY_CLS}>Zone:</span>{' '}
            {raidZoneCanonical ? (
              <Link
                to={`/raids/${encodeURIComponent(raidZoneCanonical)}`}
                className="text-gold underline decoration-dotted underline-offset-2 hover:text-gold-bright"
                title="View raid zone roster"
              >
                {data.zone}
              </Link>
            ) : (
              data.zone
            )}
          </span>
        )}
        <span><span className={HDR_KEY_CLS}>Started:</span> {fmtLocalDateTime(data.started_at)}</span>
        <span>
          <span className={HDR_KEY_CLS}>Uploaded by:</span>{' '}
          <span className="text-text">
            <UploaderTag
              characterName={data.uploaded_by}
              discordId={data.uploader_discord_id}
              displayName={data.uploader_display_name}
            />
          </span>
        </span>
        <span><span className={HDR_KEY_CLS}>Duration:</span> {fmtDuration(data.duration_s)}</span>
        <span><span className={HDR_KEY_CLS}>Damage:</span> {fmtNum(data.total_damage)}</span>
        <span><span className={HDR_KEY_CLS}>encDPS:</span> <span className="text-gold">{fmtNum(data.encdps)}</span></span>
        <span><span className={HDR_KEY_CLS}>K/D:</span> {data.kills} / {data.deaths}</span>
      </div>
    </section>
  )
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
    <section className="mb-[1.4rem]" style={{ opacity: dimmed ? 0.85 : 1 }}>
      <h2 className="font-heading text-[1.05rem] text-gold mb-2">
        {title} <span className="text-text-muted text-[0.75rem] font-normal">
          · {combatants.length}
        </span>
      </h2>
      <div className="overflow-x-auto -mx-4 sm:mx-0">
        <Card
          className="grid items-center text-[0.82rem] rounded-sm2 px-2.5 py-1.5 gap-x-2 gap-y-0 min-w-[640px]"
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
        className="col-[1/-1] grid grid-cols-subgrid items-center px-2 py-1.5 -mx-2 border-t border-border cursor-pointer"
        style={{ background: tint ?? 'transparent' }}
      >
        <div className="flex items-center gap-1.5 min-w-0">
          <Caret open={open} />
          <NameCell combatant={c} player={player} level={level} guildName={guildName} cls={cls} />
        </div>
        <div className={CELL_RIGHT_CLS}>{fmtNum(c.damage)}</div>
        <div
          className={`${CELL_RIGHT_CLS} font-semibold`}
          style={c.dps_percentile != null ? { color: percentileColor(c.dps_percentile) } : undefined}
          title={c.dps_percentile != null ? `${c.dps_percentile}% of the best ${cls ?? ''} on this boss`.trim() : undefined}
        >
          {fmtNum(c.encdps)}
          {c.dps_best_overall && <span title="Best DPS of all classes on this boss"> *</span>}
        </div>
        <div className={CELL_RIGHT_CLS}>{c.damage_perc > 0 ? `${Math.round(c.damage_perc)}%` : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.healed > 0 ? fmtNum(c.healed) : '—'}</div>
        <div
          className={`${CELL_RIGHT_CLS} font-semibold`}
          style={c.enchps > 0 && c.hps_percentile != null ? { color: percentileColor(c.hps_percentile) } : undefined}
          title={c.enchps > 0 && c.hps_percentile != null ? `${c.hps_percentile}% of the best ${cls ?? ''} on this boss`.trim() : undefined}
        >
          {c.enchps > 0 ? fmtNum(c.enchps) : '—'}
          {c.enchps > 0 && c.hps_best_overall && <span title="Best HPS of all classes on this boss"> *</span>}
        </div>
        <div className={CELL_RIGHT_CLS}>{c.damage_taken > 0 ? fmtNum(c.damage_taken) : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.crit_dam_perc > 0 ? `${Math.round(c.crit_dam_perc)}%` : '—'}</div>
        <div className={CELL_RIGHT_CLS}>{c.deaths > 0 ? c.deaths : ''}</div>
      </div>

      {open && (
        <div className="col-[1/-1] pt-1.5 pb-2.5 pl-6">
          <CombatantDetailPanel combatant={c} />
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
      className="flex items-center gap-1.5 min-w-0"
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

// ── Style constants ──────────────────────────────────────────────────────────

const PAGE_CLS = 'max-w-[1100px] mx-auto px-4 py-6'

export const CELL_RIGHT_CLS = 'text-right text-text'

const HDR_BASE_CLS  = 'text-text-muted text-[0.7rem] uppercase tracking-[0.06em]'
const HDR_CELL_CLS  = `${HDR_BASE_CLS} py-[0.15rem]`
const HDR_KEY_CLS   = `${HDR_BASE_CLS} opacity-70 mr-1`
