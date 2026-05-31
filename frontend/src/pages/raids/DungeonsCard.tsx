/**
 * DungeonsCard — per-expansion dungeon-curation panel on /raids.
 *
 * Visible only to contributors / admins. Non-contributors see no trace of
 * this card (the component returns null on its first line for them) so the
 * public /raids page is unchanged for regular users.
 *
 * Behaviour:
 *   - Collapsible header showing the dungeon count for this expansion.
 *   - Add-a-dungeon dropdown — non-dungeon zones in the same expansion.
 *   - List of currently-tagged dungeons; each row is itself collapsible and
 *     mounts the same `<BossRosterEditor>` used by the raid editor.
 *
 * Auto-categorisation note: dungeons tagged here automatically appear on the
 * rankings page and as the "Dungeon" parses bucket via the backend's
 * `_classify_zone` predicate. There's no frontend coupling — the moment the
 * type tag is added, both pickups happen on the next /api/rankings/filters
 * and /api/parses request. (`invalidate_zones_cache` fires on the mutation
 * so neither cache returns a stale view.)
 */
import { useMemo, useState } from 'react'

import { BossRosterEditor } from '../../components/BossRosterEditor'
import { Button, Card } from '../../components/ui'
import { useAuth, isContributor, type AuthState } from '../../hooks/useAuth'
import { useFetch } from '../../hooks/useFetch'
import type { Zone, ZoneListResponse } from './types'

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  /** Expansion short code (e.g. 'RoK', 'EoF'). Drives both the dungeon
   *  list fetch and the "addable" pool fetch. */
  expansion: string
  /** Optional auth-state override for tests. Production code uses useAuth();
   *  tests inject a known auth state without needing to mock fetch('/api/auth/me'). */
  authOverride?: AuthState
}

// ── Component ─────────────────────────────────────────────────────────────────

export function DungeonsCard({ expansion, authOverride }: Props) {
  // ──────────────────────────────────────────────────────────────────────────
  // Permission gate. MUST be the very first thing — we don't want any of the
  // useFetch calls below to fire for non-contributors. Using the early return
  // means React calls the same number of hooks each render for a given user
  // (rules-of-hooks is satisfied because the user's auth identity is stable
  // across renders).
  // ──────────────────────────────────────────────────────────────────────────
  const liveAuth = useAuth()
  const auth = authOverride ?? liveAuth
  if (!isContributor(auth)) return null

  return <DungeonsCardInner expansion={expansion} />
}

// The interior component holds all the data-fetching + UI state. Splitting it
// out from the gating wrapper keeps the hook call count stable per identity.

function DungeonsCardInner({ expansion }: { expansion: string }) {
  const [open, setOpen] = useState(false)
  const [expandedZoneNames, setExpandedZoneNames] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [adderValue, setAdderValue] = useState<string>('')

  // Dungeons currently tagged in this expansion.
  const dungeonsFetch = useFetch<ZoneListResponse>(
    `/api/zones?expansion=${encodeURIComponent(expansion)}&type=dungeon`,
  )
  // All zones in this expansion (so we can derive the "not yet dungeon" list).
  // We pass `type=` (empty) — the read endpoint treats an empty type param as
  // "no type filter" so the response covers every zone in the expansion.
  const allFetch = useFetch<ZoneListResponse>(
    `/api/zones?expansion=${encodeURIComponent(expansion)}&type=`,
  )

  const dungeons = useMemo<Zone[]>(() => dungeonsFetch.data?.zones ?? [], [dungeonsFetch.data])
  const addable = useMemo<Zone[]>(() => {
    const allZones = allFetch.data?.zones ?? []
    const dungeonNames = new Set(dungeons.map(z => z.name))
    return allZones.filter(z => !dungeonNames.has(z.name))
  }, [allFetch.data, dungeons])

  function reloadAll() {
    dungeonsFetch.refetch()
    allFetch.refetch()
  }

  async function handleAdd(zoneName: string) {
    if (!zoneName) return
    setError(null)
    try {
      const r = await fetch(`/api/zones/${encodeURIComponent(zoneName)}/types`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'dungeon' }),
      })
      if (!r.ok) {
        setError(`Add failed: ${r.status} ${r.statusText}`)
        return
      }
      setAdderValue('')
      reloadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleRemove(zoneName: string) {
    setError(null)
    try {
      const r = await fetch(
        `/api/zones/${encodeURIComponent(zoneName)}/types/dungeon`,
        { method: 'DELETE', credentials: 'include' },
      )
      if (!r.ok) {
        setError(`Remove failed: ${r.status} ${r.statusText}`)
        return
      }
      // Collapse if it was expanded — the row is about to disappear.
      setExpandedZoneNames(prev => {
        if (!prev.has(zoneName)) return prev
        const next = new Set(prev)
        next.delete(zoneName)
        return next
      })
      reloadAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function toggleZone(zoneName: string) {
    setExpandedZoneNames(prev => {
      const next = new Set(prev)
      if (next.has(zoneName)) next.delete(zoneName)
      else next.add(zoneName)
      return next
    })
  }

  return (
    <Card className="mt-3">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className="
          w-full flex items-baseline gap-2 text-left cursor-pointer
          appearance-none border-0 bg-transparent p-0
          text-[0.78rem] uppercase tracking-[0.08em] text-gold font-semibold
          hover:text-gold-bright transition-colors
        "
      >
        <span aria-hidden className="inline-block w-[0.6rem] text-text-muted">
          {open ? '▾' : '▸'}
        </span>
        <span>Dungeons</span>
        <span className="ml-auto normal-case tracking-normal text-[0.78rem] text-text-muted font-normal tabular-nums">
          {dungeons.length}
        </span>
      </button>

      {open && (
        <div className="mt-3 flex flex-col gap-3">
          {(dungeonsFetch.loading || allFetch.loading) && (
            <p className="text-text-muted text-sm">Loading…</p>
          )}
          {(dungeonsFetch.error || allFetch.error) && (
            <p className="text-danger text-sm">
              Failed to load dungeons: {dungeonsFetch.error ?? allFetch.error}
            </p>
          )}
          {error && <p className="text-danger text-sm">{error}</p>}

          {/* Add-a-dungeon picker. Dropdown of currently-untagged zones in
              this expansion. Selecting one immediately POSTs the tag. */}
          {!dungeonsFetch.loading && !allFetch.loading && addable.length > 0 && (
            <div className="flex items-center gap-2">
              <label htmlFor={`add-dungeon-${expansion}`} className="text-text-muted text-sm">
                Add dungeon:
              </label>
              <select
                id={`add-dungeon-${expansion}`}
                value={adderValue}
                onChange={e => {
                  const next = e.target.value
                  setAdderValue(next)
                  if (next) void handleAdd(next)
                }}
                className="
                  bg-surface border border-border rounded-md px-2 py-1
                  text-text outline-none focus:border-gold/60 text-sm
                  appearance-none
                "
              >
                <option value="">— pick a zone —</option>
                {addable.map(z => (
                  <option key={z.name} value={z.name}>
                    {z.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Dungeon list. Each is its own collapsible row with the boss
              roster editor inside (lazy-mounted on expand so collapsed rows
              don't pay the render cost). */}
          {dungeons.length === 0 && !dungeonsFetch.loading && (
            <p className="text-text-muted text-sm">
              No dungeons curated for this expansion yet.
            </p>
          )}
          {dungeons.map(zone => {
            const isExpanded = expandedZoneNames.has(zone.name)
            return (
              <div key={zone.name} className="border border-border rounded-md">
                <div className="flex items-center gap-2 px-2 py-1">
                  <button
                    type="button"
                    onClick={() => toggleZone(zone.name)}
                    aria-expanded={isExpanded}
                    className="
                      flex-1 flex items-baseline gap-2 text-left cursor-pointer
                      appearance-none border-0 bg-transparent p-0
                      text-text hover:text-gold-bright transition-colors
                    "
                  >
                    <span aria-hidden className="inline-block w-[0.6rem] text-text-muted">
                      {isExpanded ? '▾' : '▸'}
                    </span>
                    <span className="font-heading text-gold-bright">{zone.name}</span>
                    <span className="ml-2 text-text-muted text-xs tabular-nums">
                      {zone.bosses.length} encounter{zone.bosses.length === 1 ? '' : 's'}
                    </span>
                  </button>
                  <Button
                    variant="danger"
                    size="icon"
                    aria-label={`Remove ${zone.name} from dungeons`}
                    title="Remove from dungeons"
                    onClick={() => handleRemove(zone.name)}
                  >
                    🗑
                  </Button>
                </div>

                {isExpanded && (
                  <div className="border-t border-border/60 px-3 py-2">
                    <BossRosterEditor
                      zoneName={zone.name}
                      encounters={zone.bosses}
                      onReload={async () => {
                        // Refetch the dungeon list so the editor reflects the
                        // server's authoritative state after a mob/encounter
                        // mutation.
                        dungeonsFetch.refetch()
                      }}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

export default DungeonsCard
