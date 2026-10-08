import { useCallback, useState } from 'react'

import { usePolling } from './usePolling'

export interface NotificationData {
  pending_claims: number
  pending_users:  number
  officer_guild:  string | null
}

/**
 * Polls /api/notifications every `intervalMs` milliseconds (visibility-aware,
 * jittered, non-overlapping via usePolling). `enabled` lets a caller gate it on
 * a session; NotificationBell is only mounted inside the authenticated Layout.
 * Returns null until the first successful response.
 * Keeps the last known data on network errors (bell doesn't flicker away).
 */
export function useNotifications(intervalMs = 60_000, enabled = true): NotificationData | null {
  const [data, setData] = useState<NotificationData | null>(null)

  const poll = useCallback(async () => {
    try {
      const res = await fetch('/api/notifications', { credentials: 'include' })
      if (res.ok) setData(await res.json() as NotificationData)
    } catch {
      // network error — keep existing data so the bell doesn't disappear
    }
  }, [])

  usePolling(poll, intervalMs, { enabled })

  return data
}
