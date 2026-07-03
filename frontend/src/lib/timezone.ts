/**
 * Timezone helpers for the raid schedule. A raid is a recurring weekly
 * wall-clock time in the team's IANA timezone; viewers see it converted to
 * their own. Uses only Intl (no dependency).
 */

export function getBrowserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** All IANA timezone names for a picker (falls back to a small list on old browsers). */
export function listTimeZones(): string[] {
  const supported = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf
  if (supported) {
    try {
      return supported('timeZone')
    } catch {
      /* fall through */
    }
  }
  return ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Australia/Sydney']
}

/** Minutes-since-midnight → "HH:MM" (24h). Wraps negatives/overflow into 0..1439. */
export function minutesToHHMM(min: number): string {
  const m = (((min % 1440) + 1440) % 1440)
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

/** "HH:MM" → minutes since midnight, or null if malformed. */
export function hhmmToMinutes(s: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(s.trim())
  if (!m) return null
  const h = +m[1]
  const mm = +m[2]
  if (h > 23 || mm > 59) return null
  return h * 60 + mm
}

/**
 * UTC offset (minutes, + = ahead of UTC) of `tz` at the given instant.
 * e.g. America/New_York in winter → -300.
 */
export function tzOffsetMinutes(instantMs: number, tz: string): number {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
  const p: Record<string, string> = {}
  for (const part of dtf.formatToParts(new Date(instantMs))) p[part.type] = part.value
  // "24" is emitted for midnight by some engines — normalise to 0.
  const hour = p.hour === '24' ? 0 : +p.hour
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, hour, +p.minute, +p.second)
  return Math.round((asUtc - instantMs) / 60000)
}

/**
 * Convert a team-tz wall-clock minute-of-day to the viewer's tz.
 * Returns the viewer minute-of-day + a dayShift (-1/0/+1) when it crosses
 * midnight. `refMs` (default now) resolves DST; injectable for tests.
 */
export function toViewerMinutes(
  teamMin: number,
  teamTz: string,
  viewerTz: string,
  refMs: number = Date.now(),
): { minutes: number; dayShift: number } {
  const delta = tzOffsetMinutes(refMs, viewerTz) - tzOffsetMinutes(refMs, teamTz)
  const raw = teamMin + delta
  return { minutes: (((raw % 1440) + 1440) % 1440), dayShift: Math.floor(raw / 1440) }
}

export const WEEKDAYS: { n: number; label: string }[] = [
  { n: 1, label: 'Mon' }, { n: 2, label: 'Tue' }, { n: 3, label: 'Wed' },
  { n: 4, label: 'Thu' }, { n: 5, label: 'Fri' }, { n: 6, label: 'Sat' }, { n: 7, label: 'Sun' },
]
