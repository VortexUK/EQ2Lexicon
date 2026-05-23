import { useEffect, useState } from 'react'

export interface NotificationData {
  pending_claims: number
  pending_users:  number
  officer_guild:  string | null
}

/**
 * Polls /api/notifications every `intervalMs` milliseconds.
 * Returns null until the first successful response.
 * Keeps the last known data on network errors (bell doesn't flicker away).
 */
export function useNotifications(intervalMs = 60_000): NotificationData | null {
  const [data, setData] = useState<NotificationData | null>(null)

  useEffect(() => {
    let alive = true

    async function poll() {
      try {
        const res = await fetch('/api/notifications', { credentials: 'include' })
        if (res.ok && alive) {
          setData(await res.json() as NotificationData)
        }
      } catch {
        // network error — keep existing data so the bell doesn't disappear
      }
    }

    poll()
    const id = setInterval(poll, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [intervalMs])

  return data
}
