import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
import { EncounterStrategy } from '../components/EncounterStrategy'
import { ZoneOverview } from '../components/ZoneOverview'
import { Card, SectionLabel } from '../components/ui'
import { fmtRelative } from '../formatters'
import { useRaidProgress, type KilledEncounter } from '../hooks/useRaidProgress'

// ── Types ─────────────────────────────────────────────────────────────────────

interface EncounterMob { mob_name: string; position: number }
interface Encounter {
  encounter_name: string
  position: number
  stage: string | null
  wiki_url: string | null
  mobs: EncounterMob[]
}
interface Zone {
  name: string
  expansion_short: string
  expansion_name: string
  expansion_year: number | null
  types: string[]
  aliases: string[]
  wiki_url: string | null
  is_contested: boolean
  is_instance: boolean
  is_openworld: boolean
  bosses: Encounter[]
}

// ── Page ──────────────────────────────────────────────────────────────────────
//
// URL: /raids/:name           — defaults to the first encounter
//      /raids/:name/:position — opens the encounter with that curator position
//
// Layout: sidebar (encounter list, grouped by stage) + main pane (selected
// encounter detail). Clicking a sidebar item updates the URL via navigate(),
// which only changes useParams — the component stays mounted, so there's no
// reload / data refetch.

export default function RaidZonePage() {
  const { name = '', position } = useParams<{ name: string; position?: string }>()
  const navigate = useNavigate()

  const [zone, setZone] = useState<Zone | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const progress = useRaidProgress()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setZone(null)

    fetch(`/api/zones/${encodeURIComponent(name)}`, { credentials: 'include' })
      .then(r => {
        if (r.status === 404) return Promise.reject(new Error('not found'))
        return r.ok ? r.json() : Promise.reject(new Error(String(r.status)))
      })
      .then((z: Zone) => {
        if (!cancelled) setZone(z)
      })
      .catch(err => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [name])

  // Group encounters by stage in curator order. Memoise both for cheap
  // re-renders when only the selected position changes.
  const stages = useMemo(() => groupByStage(zone?.bosses ?? []), [zone])

  // Resolve the selected encounter from the URL. Falls back to the first
  // encounter when no position or an unknown position is given so a bare
  // /raids/:name URL still shows something useful.
  const selected: Encounter | null = useMemo(() => {
    if (!zone || zone.bosses.length === 0) return null
    if (position) {
      const want = Number.parseInt(position, 10)
      const hit = zone.bosses.find(b => b.position === want)
      if (hit) return hit
    }
    return zone.bosses[0]
  }, [zone, position])

  // Map encounter_name → KilledEncounter so the sidebar + main pane can pull
  // ‹last_kill_id, last_kill_at, kill_count› without scanning the list each render.
  const killsByName = useMemo<Map<string, KilledEncounter>>(() => {
    const m = new Map<string, KilledEncounter>()
    for (const k of progress.killed_encounters[zone?.name ?? ''] ?? []) {
      m.set(k.encounter_name, k)
    }
    return m
  }, [progress, zone])
  const killedCount = killsByName.size
  const totalCount = zone?.bosses.length ?? 0

  function selectEncounter(enc: Encounter) {
    // replace:true keeps the back button useful — bouncing between encounters
    // shouldn't bloat history with one entry per click.
    navigate(`/raids/${encodeURIComponent(name)}/${enc.position}`, { replace: true })
  }

  return (
    <main className="page-enter mx-auto max-w-6xl px-4 py-6">
      <Breadcrumb items={[{ label: 'Raids', to: '/raids' }, { label: name }]} />

      {loading && <p className="text-text-muted">Loading…</p>}
      {error === 'not found' && (
        <p className="text-text-muted">
          No raid zone known as <span className="text-text">{name}</span>.
        </p>
      )}
      {error && error !== 'not found' && <p className="text-danger">Failed to load zone: {error}</p>}

      {!loading && zone && (
        <>
          <ZoneHeader
            zone={zone}
            killed={killedCount}
            total={totalCount}
            hasGuild={!!progress.guild_name}
            guildName={progress.guild_name}
          />

          {/* Zone-level overview lives between the header and the encounter
              grid so it's read before drilling into a specific boss. Hidden
              entirely when there's no content and the viewer can't create
              any (component handles that internally). */}
          <div className="mt-5">
            <ZoneOverview zoneName={zone.name} />
          </div>

          <div className="mt-5 grid gap-4 grid-cols-1 md:grid-cols-[16rem_1fr]">
            <aside className="min-w-0">
              <EncounterSidebar
                stages={stages}
                selected={selected}
                killsByName={killsByName}
                onSelect={selectEncounter}
              />
            </aside>
            <section className="min-w-0">
              {selected ? (
                <EncounterDetail
                  zoneName={zone.name}
                  encounter={selected}
                  kill={killsByName.get(selected.encounter_name)}
                />
              ) : (
                <Card><p className="text-text-muted">No encounters curated for this zone yet.</p></Card>
              )}
            </section>
          </div>
        </>
      )}
    </main>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

interface ZoneHeaderProps {
  zone: Zone
  killed: number
  total: number
  hasGuild: boolean
  guildName: string | null
}

function ZoneHeader({ zone, killed, total, hasGuild, guildName }: ZoneHeaderProps) {
  const bits: string[] = [`${zone.expansion_name} (${zone.expansion_short})`]
  if (zone.expansion_year) bits.push(String(zone.expansion_year))
  if (zone.is_contested) bits.push('Contested')
  else if (zone.is_instance) bits.push('Instanced')

  const pct = total > 0 ? Math.round((killed / total) * 100) : 0
  const isComplete = killed === total && total > 0

  return (
    <>
      <h1 className="font-heading text-[1.7rem] text-gold mb-1">{zone.name}</h1>
      <div className="flex items-center gap-3 flex-wrap text-text-muted text-sm">
        <span>{bits.join(' · ')}</span>
        {zone.wiki_url && (
          <a
            href={zone.wiki_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-gold-dim underline decoration-dotted underline-offset-2 hover:text-gold"
          >
            EQ2i wiki ↗
          </a>
        )}
      </div>

      {hasGuild && (
        <div className="mt-3 max-w-md">
          <div className="flex items-center justify-between text-[0.72rem] mb-1">
            <span className="uppercase tracking-[0.06em] text-text-muted">
              ‹{guildName}› progress
            </span>
            <span className={isComplete ? 'text-success font-semibold tabular-nums' : 'text-text tabular-nums'}>
              {killed} / {total}
            </span>
          </div>
          <div
            className="h-2 rounded-full bg-bg/60 border border-border overflow-hidden"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={killed}
          >
            <div
              className={`h-full transition-[width] duration-300 ${isComplete ? 'bg-success/70' : 'bg-gold/70'}`}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
    </>
  )
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

interface SidebarProps {
  stages: { stage: string | null; encounters: Encounter[] }[]
  selected: Encounter | null
  killsByName: Map<string, KilledEncounter>
  onSelect: (enc: Encounter) => void
}

function EncounterSidebar({ stages, selected, killsByName, onSelect }: SidebarProps) {
  return (
    <Card className="p-0 overflow-hidden sticky top-[4.5rem]">
      <nav aria-label="Encounter list">
        {stages.map((s, i) => (
          <div key={s.stage ?? '__flat'} className={i > 0 ? 'border-t border-border/60' : ''}>
            {s.stage && (
              <div className="px-3 pt-3 pb-1">
                <SectionLabel>{s.stage}</SectionLabel>
              </div>
            )}
            <ul>
              {s.encounters.map(enc => {
                const isSelected = selected?.position === enc.position
                const kill = killsByName.get(enc.encounter_name)
                return (
                  <li key={enc.position}>
                    <button
                      type="button"
                      onClick={() => onSelect(enc)}
                      className={
                        'w-full text-left flex items-start gap-2 px-3 py-2 text-[0.88rem] transition-colors ' +
                        (isSelected
                          ? 'bg-surface-raised text-gold-bright'
                          : 'text-text hover:bg-surface-raised/50 hover:text-gold-bright')
                      }
                    >
                      <KillIndicator killed={!!kill} />
                      <span className="flex-1 min-w-0 leading-snug">
                        <span className="block">{enc.encounter_name}</span>
                        {kill && (
                          <span className="block text-[0.7rem] text-text-muted">
                            {fmtRelative(kill.last_kill_at)}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>
    </Card>
  )
}

function KillIndicator({ killed }: { killed: boolean }) {
  // Kept as a tiny dedicated component so we can swap the visual treatment in
  // one place — currently a green check for kills, a hollow ring for unkilled.
  return killed ? (
    <span
      className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-success/20 text-success text-[0.7rem] shrink-0 mt-[2px]"
      aria-label="Killed"
      title="Killed"
    >
      ✓
    </span>
  ) : (
    <span
      className="inline-block w-4 h-4 rounded-full border border-border shrink-0 mt-[2px]"
      aria-label="Not killed"
      title="Not killed"
    />
  )
}

// ── Main pane ─────────────────────────────────────────────────────────────────

interface EncounterDetailProps {
  zoneName: string
  encounter: Encounter
  kill: KilledEncounter | undefined
}

function EncounterDetail({ zoneName, encounter, kill }: EncounterDetailProps) {
  const isGroup = encounter.mobs.length > 1
  return (
    <Card className="flex flex-col gap-4">
      <header className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <SectionLabel>
            {encounter.stage ? `${encounter.stage} · #${encounter.position}` : `Encounter #${encounter.position}`}
          </SectionLabel>
          <h2 className="font-heading text-[1.45rem] text-gold-bright leading-snug">
            {encounter.encounter_name}
          </h2>
        </div>
        {kill && (
          <span className="inline-flex items-center gap-1 text-success text-xs uppercase tracking-[0.08em] font-semibold pt-1">
            ✓ Cleared
          </span>
        )}
      </header>

      {kill && <LastKillRow kill={kill} />}

      {isGroup && (
        <section>
          <SectionLabel>Encounter mobs</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {encounter.mobs.map(m => (
              <span
                key={m.mob_name}
                className="text-[0.78rem] text-text bg-surface-raised/70 border border-border rounded-sm px-2 py-[2px]"
              >
                {m.mob_name}
              </span>
            ))}
          </div>
        </section>
      )}

      {encounter.wiki_url && (
        <section>
          <SectionLabel>Reference</SectionLabel>
          <a
            href={encounter.wiki_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-gold underline decoration-dotted underline-offset-2 hover:text-gold-bright"
          >
            EQ2i page ↗
          </a>
        </section>
      )}

      <EncounterStrategy
        zoneName={zoneName}
        position={encounter.position}
        wikiUrl={encounter.wiki_url}
      />
    </Card>
  )
}

function LastKillRow({ kill }: { kill: KilledEncounter }) {
  return (
    <section>
      <SectionLabel>Last killed</SectionLabel>
      <div className="flex items-baseline gap-3 flex-wrap text-sm">
        <Link
          to={`/parse/${kill.last_kill_id}`}
          className="text-gold underline decoration-dotted underline-offset-2 hover:text-gold-bright"
        >
          {fmtRelative(kill.last_kill_at)}
        </Link>
        <span className="text-text-muted text-[0.78rem]">
          {kill.kill_count === 1 ? '1 kill total' : `${kill.kill_count} kills total`}
        </span>
      </div>
    </section>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function groupByStage(encounters: Encounter[]): { stage: string | null; encounters: Encounter[] }[] {
  // Preserve first-seen order — relying on Map insertion order keeps the
  // curator's intended sequence (matching the kill order in the zone).
  const buckets = new Map<string, Encounter[]>()
  for (const enc of encounters) {
    const key = enc.stage ?? '__flat'
    const arr = buckets.get(key)
    if (arr) arr.push(enc)
    else buckets.set(key, [enc])
  }
  return Array.from(buckets.entries()).map(([key, list]) => ({
    stage: key === '__flat' ? null : list[0].stage,
    encounters: list,
  }))
}
