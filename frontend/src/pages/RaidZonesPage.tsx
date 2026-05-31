/**
 * RaidZonesPage — /raids landing page.
 *
 * Data is fully admin-curated:
 *   - GET /api/raids/expansions          → which expansion sections to render
 *   - GET /api/raids/zones?expansion=X   → raid zones in each section
 *
 * The page itself just owns the expansion list, the open/closed state per
 * section, and the page-level "Add expansion" admin affordance.  Per-section
 * rendering + per-zone fetches live in <ExpansionSection> so each one calls
 * useFetch exactly once per render (Rules of Hooks).
 *
 * Migrated 2026-05-31 from a hardcoded EXPANSIONS const + raid_x4 type
 * filter to admin-curated tables (see census/zones_db.featured_raid_*).
 */
import { useEffect, useMemo, useState } from 'react'

import Breadcrumb from '../components/Breadcrumb'
import { Button } from '../components/ui'
import { useFetch } from '../hooks/useFetch'
import { type AuthState, isAdmin, useAuth } from '../hooks/useAuth'
import { useRaidProgress } from '../hooks/useRaidProgress'
import { useServer } from '../hooks/useServer'
import { ExpansionSection } from './raids/ExpansionSection'
import { ExpansionPickerModal } from './raids/ZonePickerModal'

interface FeaturedExpansion {
  short: string
  name: string | null
  year: number | null
}

interface Props {
  /** Optional auth-state override for tests. */
  authOverride?: AuthState
}

export default function RaidZonesPage({ authOverride }: Props = {}) {
  const liveAuth = useAuth()
  const auth = authOverride ?? liveAuth
  const admin = isAdmin(auth)

  const expansionsFetch = useFetch<FeaturedExpansion[]>('/api/raids/expansions')
  const expansions = useMemo<FeaturedExpansion[]>(
    () => expansionsFetch.data ?? [],
    [expansionsFetch.data],
  )

  const progress = useRaidProgress()
  const server = useServer()

  // Per-section open/closed. Initialised once data + server settings are in:
  // the server's current_xpac is opened by default, everything else closed.
  // After that the user toggles freely — we don't re-collapse on re-render.
  const [openExpansions, setOpenExpansions] = useState<Set<string> | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [mutationError, setMutationError] = useState<string | null>(null)

  useEffect(() => {
    if (openExpansions !== null) return
    if (expansionsFetch.loading || expansions.length === 0) return
    const current = server?.currentXpac
    const seed = current && expansions.some(e => e.short === current)
      ? current
      : expansions[0].short
    setOpenExpansions(new Set([seed]))
  }, [openExpansions, expansionsFetch.loading, expansions, server])

  function toggleExpansion(short: string) {
    setOpenExpansions(prev => {
      const next = new Set(prev ?? [])
      if (next.has(short)) next.delete(short)
      else next.add(short)
      return next
    })
  }

  async function handleAddExpansion(short: string) {
    setMutationError(null)
    try {
      const r = await fetch(`/api/raids/expansions/${encodeURIComponent(short)}`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        setMutationError(body?.detail ?? `Add failed: ${r.status}`)
        return
      }
      setPickerOpen(false)
      // Open the new section by default so admin sees an immediate place to
      // add raid zones into.
      setOpenExpansions(prev => {
        const next = new Set(prev ?? [])
        next.add(short)
        return next
      })
      expansionsFetch.refetch()
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6">
      <Breadcrumb items={[{ label: 'Raids' }]} />
      <div className="flex items-baseline gap-3 mb-1">
        <h1 className="font-heading text-[1.7rem] text-gold">Raid Zones</h1>
        {admin && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { setMutationError(null); setPickerOpen(true) }}
          >
            + Add expansion
          </Button>
        )}
      </div>
      <p className="text-text-muted text-sm mb-5">
        Boss rosters for every curated x4 raid.
        {progress.guild_name && (
          <>
            {' '}Progression shown for <span className="text-gold-dim">‹{progress.guild_name}›</span>.
          </>
        )}
      </p>

      {mutationError && <p className="text-danger text-sm mb-3">{mutationError}</p>}
      {expansionsFetch.loading && <p className="text-text-muted">Loading…</p>}
      {expansionsFetch.error && (
        <p className="text-danger">Failed to load raid expansions: {expansionsFetch.error}</p>
      )}

      {!expansionsFetch.loading && !expansionsFetch.error && expansions.length === 0 && (
        <p className="text-text-muted">
          No expansions have been added to /raids yet
          {admin ? ' — click "Add expansion" above to start curating.' : '.'}
        </p>
      )}

      <div className="flex flex-col gap-7">
        {expansions.map(exp => {
          const isOpen = openExpansions?.has(exp.short) ?? false
          const isCurrent = server?.currentXpac === exp.short
          return (
            <ExpansionSection
              key={exp.short}
              expansion={{ short: exp.short, name: exp.name ?? exp.short }}
              isOpen={isOpen}
              onToggle={() => toggleExpansion(exp.short)}
              isCurrent={isCurrent}
              killedByZone={progress.killed_encounters}
              hasGuild={!!progress.guild_name}
              onExpansionRemoved={expansionsFetch.refetch}
              authOverride={authOverride}
            />
          )
        })}
      </div>

      {pickerOpen && (
        <ExpansionPickerModal
          onPick={handleAddExpansion}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </main>
  )
}
