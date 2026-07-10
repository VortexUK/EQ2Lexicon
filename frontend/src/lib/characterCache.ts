/**
 * characterCache — module-level cache + fetch for full character records.
 *
 * Extracted from CharacterPage so the compare page shares the same cache:
 * navigating character page → compare (or comparing two already-viewed
 * characters) performs zero refetches. Survives re-renders and Vite HMR
 * remounts; keyed by lower-cased character name. In-flight promises are
 * deduped so two consumers asking for the same name concurrently share one
 * request.
 */
import type { Character } from '../pages/characterSheet'

export type CharFetchResult =
  | { status: 'ok'; char: Character }
  | { status: 'not_found' }
  | { status: 'census_unavailable' }
  | { status: 'error'; message: string }

const cache = new Map<string, Character>()
const inFlight = new Map<string, Promise<CharFetchResult>>()

export function getCachedCharacter(name: string): Character | undefined {
  return cache.get(name.toLowerCase())
}

/** Used by the SSE subscriber to live-swap a refreshed record. */
export function setCachedCharacter(char: Character): void {
  cache.set(char.name.toLowerCase(), char)
}

/** Fetch a character (cache-first, in-flight-deduped). Never throws — errors
 * come back as a result status. Only 'ok' results populate the cache. */
export function fetchCharacter(name: string): Promise<CharFetchResult> {
  const key = name.toLowerCase()
  const cached = cache.get(key)
  if (cached) return Promise.resolve({ status: 'ok', char: cached })
  const pending = inFlight.get(key)
  if (pending) return pending

  const promise: Promise<CharFetchResult> = fetch(
    `/api/character/${encodeURIComponent(name)}`,
    { credentials: 'include' },
  )
    .then(async (res): Promise<CharFetchResult> => {
      if (res.status === 404) return { status: 'not_found' }
      if (res.status === 503) return { status: 'census_unavailable' }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        return { status: 'error', message: body.detail ?? `HTTP ${res.status}` }
      }
      const char: Character = await res.json()
      cache.set(key, char)
      return { status: 'ok', char }
    })
    .catch((err): CharFetchResult => ({ status: 'error', message: String(err) }))
    .finally(() => { inFlight.delete(key) })

  inFlight.set(key, promise)
  return promise
}
