import { useEffect, useState } from 'react'

export interface ClassInfo {
  name: string
  archetype: string
  subclass: string | null
  role: string
  colour: string
  display_order: number
  icon_url: string
}

// Module-level cache + in-flight promise so /api/classes is fetched once per
// app load (the data never changes within a session).
let _cache: ClassInfo[] | null = null
let _inflight: Promise<ClassInfo[]> | null = null

function loadClasses(): Promise<ClassInfo[]> {
  if (_cache) return Promise.resolve(_cache)
  if (!_inflight) {
    _inflight = fetch('/api/classes', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`/api/classes ${r.status}`))))
      .then((data: ClassInfo[]) => {
        // Defensive: a misbehaving proxy/mock returning a non-array must not
        // crash every consumer with `classes.map is not a function`.
        const safe = Array.isArray(data) ? data : []
        _cache = safe
        return safe
      })
      .catch(() => {
        _inflight = null // allow a retry on the next mount
        return [] as ClassInfo[]
      })
  }
  return _inflight
}

const FALLBACK_COLOUR = 'var(--text-muted)'

export function useClasses() {
  const [classes, setClasses] = useState<ClassInfo[]>(_cache ?? [])
  useEffect(() => {
    let cancelled = false
    loadClasses().then(data => {
      if (!cancelled) setClasses(data)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const byName = new Map(classes.map(c => [c.name, c]))
  const colourFor = (name: string | null | undefined, fallback: string = FALLBACK_COLOUR): string =>
    (name ? byName.get(name)?.colour : undefined) ?? fallback
  const iconUrlFor = (name: string | null | undefined): string | null =>
    (name ? byName.get(name)?.icon_url : undefined) ?? null

  return { classes, byName, colourFor, iconUrlFor }
}
