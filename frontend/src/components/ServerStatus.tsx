import { useEffect, useState } from 'react'

import { useServer } from '../hooks/useServer'

/**
 * Footer indicator for the in-focus EQ2 server's Census-reported state,
 * shown next to the Census API indicator. Green for any "up" population
 * state (low/medium/high), orange for locked, red for down; grey until
 * the first report lands. The tooltip carries the raw reported status.
 */

interface ServerStatusData {
  world: string
  state: string
  reported_at: number
}

/** Daybreak state token → dot colour + label suffix + tooltip description.
 * The low/medium/high tokens are POPULATION levels on an up server, not
 * degraded states. Exported for tests. */
export function presentState(state: string): { colour: string; glow: string; suffix: string; describe: string } {
  const s = state.toLowerCase()
  if (['low', 'medium', 'high'].includes(s)) {
    return {
      colour: 'var(--color-success)',
      glow: '0 0 5px rgba(74,222,128,0.7)',
      suffix: 'online',
      describe: `online — current population: ${s}`,
    }
  }
  if (s === 'up') {
    return { colour: 'var(--color-success)', glow: '0 0 5px rgba(74,222,128,0.7)', suffix: 'online', describe: 'online' }
  }
  if (s === 'locked') {
    return {
      colour: 'var(--color-warning)',
      glow: '0 0 5px rgba(251,191,36,0.7)',
      suffix: 'locked',
      describe: 'locked — not accepting logins',
    }
  }
  if (['down', 'offline'].includes(s)) {
    return { colour: 'var(--color-danger)', glow: '0 0 5px rgba(248,113,113,0.7)', suffix: 'down', describe: 'down' }
  }
  return { colour: 'var(--color-text-muted)', glow: 'none', suffix: 'status unknown', describe: 'no status reported yet' }
}

const REFRESH_MS = 5 * 60 * 1000 // match the backend poll cadence

export default function ServerStatus() {
  const server = useServer()
  const displayName = server?.displayName
  const [data, setData] = useState<ServerStatusData | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      fetch('/api/census/server-status', { credentials: 'include' })
        .then(r => (r.ok ? r.json() : null))
        .then(d => {
          if (!cancelled && d) setData(d as ServerStatusData)
        })
        .catch(() => {})
    load()
    const t = setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [])

  const state = data?.state ?? 'unknown'
  const { colour, glow, suffix, describe } = presentState(state)
  const name = displayName || data?.world || 'Server'
  const reported = data?.reported_at
    ? ` (reported ${new Date(data.reported_at * 1000).toLocaleTimeString()})`
    : ''

  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={`${name} server: ${describe}${reported}`}
    >
      <span
        className="inline-block rounded-full shrink-0"
        style={{ width: '8px', height: '8px', background: colour, boxShadow: glow }}
        aria-hidden="true"
      />
      <span>{name} server {suffix}</span>
    </span>
  )
}
