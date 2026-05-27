import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
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
interface ZoneListResponse {
  expansion: string | null
  type: string | null
  zones: Zone[]
}

// ── Expansion catalogue ───────────────────────────────────────────────────────
// Ordered newest-first so the most relevant raid content surfaces at the top of
// the page. Only expansions with curated raid data are queried — the index
// silently omits any expansion that returns zero zones.

interface ExpansionDef { short: string; name: string }

const EXPANSIONS: ExpansionDef[] = [
  // Newest first — extend as raid rosters are curated.
  { short: 'RoK', name: 'Rise of Kunark' },
  { short: 'EoF', name: 'Echoes of Faydwer' },
]

// ── Page ──────────────────────────────────────────────────────────────────────

export default function RaidZonesPage() {
  // One fetch per expansion, in parallel. Keyed by expansion short for cheap
  // re-render-free lookups when we render the sections below.
  const [byExpansion, setByExpansion] = useState<Record<string, Zone[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const progress = useRaidProgress()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    Promise.all(
      EXPANSIONS.map(exp =>
        fetch(`/api/zones?expansion=${encodeURIComponent(exp.short)}&type=raid_x4`, { credentials: 'include' })
          .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
          .then((j: ZoneListResponse) => [exp.short, j.zones] as const)
      )
    )
      .then(pairs => {
        if (cancelled) return
        const next: Record<string, Zone[]> = {}
        for (const [short, zones] of pairs) next[short] = zones
        setByExpansion(next)
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
  }, [])

  // Drop empty expansions so the page doesn't show a hollow "RoR" header before
  // its roster has been curated.
  const visible = useMemo(
    () => EXPANSIONS.filter(e => (byExpansion[e.short]?.length ?? 0) > 0),
    [byExpansion]
  )

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6">
      <Breadcrumb items={[{ label: 'Raids' }]} />
      <h1 className="font-heading text-[1.7rem] text-gold mb-1">Raid Zones</h1>
      <p className="text-text-muted text-sm mb-5">
        Boss rosters for every curated x4 raid.
        {progress.guild_name && (
          <>
            {' '}Progression shown for <span className="text-gold-dim">‹{progress.guild_name}›</span>.
          </>
        )}
      </p>

      {loading && <p className="text-text-muted">Loading…</p>}
      {error && <p className="text-danger">Failed to load raid zones: {error}</p>}

      {!loading && !error && visible.length === 0 && (
        <p className="text-text-muted">No raid rosters have been curated yet.</p>
      )}

      <div className="flex flex-col gap-7">
        {visible.map(exp => (
          <section key={exp.short}>
            <SectionLabel>
              {exp.name} ({exp.short})
            </SectionLabel>
            <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
              {byExpansion[exp.short].map(zone => (
                <ZoneCard
                  key={zone.name}
                  zone={zone}
                  killed={progress.killed_encounters[zone.name] ?? []}
                  hasGuild={!!progress.guild_name}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  )
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

interface ZoneCardProps {
  zone: Zone
  killed: KilledEncounter[]
  hasGuild: boolean
}

function ZoneCard({ zone, killed, hasGuild }: ZoneCardProps) {
  const total = zone.bosses.length
  const killedCount = killed.length
  const pct = total > 0 ? Math.round((killedCount / total) * 100) : 0
  // Most recent kill across any encounter in this zone — drives the "last kill"
  // subtitle so a card communicates "active raid" at a glance.
  const lastKillAt = killed.length > 0 ? Math.max(...killed.map(k => k.last_kill_at)) : null

  return (
    <Link
      to={`/raids/${encodeURIComponent(zone.name)}`}
      className="block no-underline group"
    >
      <Card className="h-full transition-colors group-hover:border-gold/60 flex flex-col gap-2">
        <div>
          <div className="font-heading text-gold-bright text-[1.05rem] leading-snug mb-1">
            {zone.name}
          </div>
          <div className="text-text-muted text-[0.78rem]">
            {total} encounter{total === 1 ? '' : 's'}
            {zone.is_contested && <span className="ml-2 text-gold-dim">· Contested</span>}
          </div>
        </div>

        {/* Progress — only render when the user has a resolved guild. Otherwise
            we'd be showing 0/N for every zone, which reads as "your guild has
            zero kills" rather than "we don't know your guild yet". */}
        {hasGuild && (
          <ProgressBar killed={killedCount} total={total} pct={pct} lastKillAt={lastKillAt} />
        )}
      </Card>
    </Link>
  )
}

interface ProgressBarProps {
  killed: number
  total: number
  pct: number
  lastKillAt: number | null
}

function ProgressBar({ killed, total, pct, lastKillAt }: ProgressBarProps) {
  const isComplete = killed === total && total > 0
  return (
    <div className="mt-auto pt-1">
      <div className="flex items-center justify-between text-[0.7rem] mb-1">
        <span className="uppercase tracking-[0.06em] text-text-muted">Progress</span>
        <span className={isComplete ? 'text-success font-semibold tabular-nums' : 'text-text tabular-nums'}>
          {killed} / {total}
        </span>
      </div>
      <div
        className="h-1.5 rounded-full bg-bg/60 border border-border overflow-hidden"
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
      {lastKillAt !== null && (
        <div className="text-[0.68rem] text-text-muted mt-1 text-right">
          Last kill {fmtRelative(lastKillAt)}
        </div>
      )}
    </div>
  )
}
