import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

export type CensusHealth = 'up' | 'down' | 'unknown'
/** `noChange` is true when the server finished a refresh with nothing newer
 *  (Census had no record) — `data` is undefined then; clear any "refreshing"
 *  state and keep what you have. */
type Listener<T = unknown> = (data: T, fetchedAt: number, noChange?: boolean) => void

interface CensusStream {
  health: CensusHealth
  /** Whether the SSE connection itself is open. A dropped stream (deploy
   *  restart, proxy idle timeout) is NOT a Census outage — it used to be
   *  reported as one. */
  connected: boolean
  /** Subscribe to refresh records for a given key (`<name_lower>:<world>` or `guild:<g>:<w>`).
   *  The type parameter `T` is a TS-only hint — the runtime still delivers JSON-parsed `unknown`. */
  subscribe: <T = unknown>(key: string, cb: Listener<T>) => () => void
}

type StreamMessage =
  | { type: 'health'; status: CensusHealth }
  | { type: 'character' | 'guild'; key: string; data?: unknown; fetched_at: number; nochange?: boolean }

const Ctx = createContext<CensusStream>({ health: 'unknown', connected: false, subscribe: () => () => {} })
export const useCensusStream = () => useContext(Ctx)

export function CensusStreamProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<CensusHealth>('unknown')
  const [connected, setConnected] = useState(false)
  const listeners = useRef<Map<string, Set<Listener>>>(new Map())

  useEffect(() => {
    const es = new EventSource('/api/census/stream', { withCredentials: true })
    es.onopen = () => setConnected(true)
    es.onmessage = e => {
      let msg: StreamMessage
      try {
        msg = JSON.parse(e.data as string) as StreamMessage
      } catch {
        return
      }
      if (msg.type === 'health') {
        setHealth(msg.status)
        return
      }
      if (msg.type === 'character' || msg.type === 'guild') {
        listeners.current.get(msg.key)?.forEach(cb => cb(msg.data, msg.fetched_at, msg.nochange === true))
      }
    }
    // EventSource reconnects on its own; health comes from the server's
    // 'health' messages, never from transport errors.
    es.onerror = () => setConnected(false)
    return () => es.close()
  }, [])

  const subscribe = useCallback(<T = unknown>(key: string, cb: Listener<T>) => {
    let set = listeners.current.get(key)
    if (!set) { set = new Set(); listeners.current.set(key, set) }
    set.add(cb as Listener)
    return () => set!.delete(cb as Listener)
  }, [])

  return <Ctx.Provider value={{ health, connected, subscribe }}>{children}</Ctx.Provider>
}
