/**
 * Server context — fetches /api/server once on mount and exposes the active
 * server info (world, display name, max level, current xpac, launch datetime,
 * and the list of all available servers for the subdomain switcher).
 *
 * Returns null while loading or if the fetch fails — consumers must handle
 * the null case gracefully.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

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
  servers:      ServerEntry[]
}

// Raw shape returned by the backend
interface ApiServerResponse {
  world:         string
  display_name:  string
  max_level:     number
  current_xpac:  string | null
  launch_dt:     string | null
  servers:       { world: string; subdomain: string; display_name: string }[]
}

function mapResponse(data: ApiServerResponse): ActiveServer {
  return {
    world:       data.world,
    displayName: data.display_name,
    maxLevel:    data.max_level,
    currentXpac: data.current_xpac ?? null,
    launchDt:    data.launch_dt ?? null,
    servers:     data.servers.map(s => ({
      world:       s.world,
      subdomain:   s.subdomain,
      displayName: s.display_name,
    })),
  }
}

const ServerCtx = createContext<ActiveServer | null>(null)

export function useServer(): ActiveServer | null {
  return useContext(ServerCtx)
}

export function ServerProvider({ children }: { children: ReactNode }) {
  const [server, setServer] = useState<ActiveServer | null>(null)

  useEffect(() => {
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

  return <ServerCtx.Provider value={server}>{children}</ServerCtx.Provider>
}
