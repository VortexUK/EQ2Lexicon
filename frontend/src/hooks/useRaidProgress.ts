import { useEffect, useState } from 'react'

/**
 * Per-zone kill progress for the signed-in user's primary-character guild.
 *
 * Shape mirrors ``RaidProgressResponse`` in [web/routes/zones.py]:
 *   - ``killed_encounters`` is keyed by zone name (the curator's canonical
 *     name) → list of {encounter_name, kill_count, last_kill_id, last_kill_at}.
 *   - Frontend joins on zone name; missing entries render as 0/N (untouched).
 *
 * 401 (not signed in) and any other failure both return an "empty" payload —
 * the caller renders without progress info rather than blowing up.
 */
export interface KilledEncounter {
  encounter_name: string
  kill_count: number
  last_kill_id: number
  last_kill_at: number          // unix seconds, UTC
}

export interface RaidProgress {
  guild_name: string | null
  character_name: string | null
  killed_encounters: Record<string, KilledEncounter[]>
}

const EMPTY: RaidProgress = { guild_name: null, character_name: null, killed_encounters: {} }

export function useRaidProgress(): RaidProgress {
  const [progress, setProgress] = useState<RaidProgress>(EMPTY)

  useEffect(() => {
    let cancelled = false
    fetch('/api/zones/progress', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : EMPTY))
      .then((p: RaidProgress) => {
        if (!cancelled) setProgress(p)
      })
      .catch(() => {
        if (!cancelled) setProgress(EMPTY)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return progress
}
