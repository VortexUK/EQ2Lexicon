/**
 * Server context — fetches /api/server once on mount and exposes the active
 * server info (world, display name, max level, current xpac, launch datetime,
 * and the list of all available servers for the subdomain switcher).
 *
 * Returns null while loading or if the fetch fails — consumers must handle
 * the null case gracefully.
 */
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

export interface ServerEntry {
  world:       string
  subdomain:   string
  displayName: string
}

export interface ActiveServer {
  world:        string
  displayName:  string
  maxLevel:     number
  currentXpac:  string | null
  launchDt:     string | null
  /** Upcoming expansion (admin-set) — drives the home-page countdown banner. */
  nextXpac:     string | null
  nextXpacDt:   string | null
  servers:      ServerEntry[]
}

// Raw shape returned by the backend
interface ApiServerResponse {
  world:         string
  display_name:  string
  max_level:     number
  current_xpac:  string | null
  launch_dt:     string | null
  next_xpac?:    string | null
  next_xpac_dt?: string | null
  servers:       { world: string; subdomain: string; display_name: string }[]
}

function mapResponse(data: ApiServerResponse): ActiveServer {
  return {
    world:       data.world,
    displayName: data.display_name,
    maxLevel:    data.max_level,
    currentXpac: data.current_xpac ?? null,
    launchDt:    data.launch_dt ?? null,
    nextXpac:    data.next_xpac ?? null,
    nextXpacDt:  data.next_xpac_dt ?? null,
    servers:     data.servers.map(s => ({
      world:       s.world,
      subdomain:   s.subdomain,
      displayName: s.display_name,
    })),
  }
}

const ServerCtx = createContext<ActiveServer | null>(null)

/** Re-fetch /api/server into the provider. Stable identity. Call after an
 *  admin edit to server settings so banners/limits update without a full
 *  page reload (the provider otherwise fetches exactly once at app load). */
const ServerRefreshCtx = createContext<() => void>(() => {})

export function useServer(): ActiveServer | null {
  return useContext(ServerCtx)
}

export function useRefreshServer(): () => void {
  return useContext(ServerRefreshCtx)
}

export function ServerProvider({ children }: { children: ReactNode }) {
  const [server, setServer] = useState<ActiveServer | null>(null)

  const load = useCallback(() => {
    fetch('/api/server', { credentials: 'include' })
      .then(res => {
        if (!res.ok) return null
        return res.json() as Promise<ApiServerResponse>
      })
      .then(data => {
        if (data) setServer(mapResponse(data))
      })
      .catch(() => { /* silently suppress — server stays null */ })
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <ServerRefreshCtx.Provider value={load}>
      <ServerCtx.Provider value={server}>{children}</ServerCtx.Provider>
    </ServerRefreshCtx.Provider>
  )
}
