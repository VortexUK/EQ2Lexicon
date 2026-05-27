// Shared display formatters.
//
// Until this file existed each page (ParsesPage, ParsePage, TokensPage, etc.)
// defined its own `fmtNum`, `fmtDuration`, `fmtLocalDate` etc. — multiplying
// the maintenance cost of design tweaks (e.g. locale changes). One source
// of truth now.

/** Locale-grouped integer string (rounds floats first). */
export function fmtNum(n: number): string {
  return Math.round(n).toLocaleString()
}

/** "5m23s" / "0m07s". */
export function fmtDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m${String(s).padStart(2, '0')}s`
}

/** "YYYY-MM-DD" in the browser's local timezone. */
export function fmtLocalDate(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  const y = d.getFullYear()
  const mo = String(d.getMonth() + 1).padStart(2, '0')
  const da = String(d.getDate()).padStart(2, '0')
  return `${y}-${mo}-${da}`
}

/** "HH:MM" in the browser's local timezone (24-hour clock). */
export function fmtLocalTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

/** "Mar 5, 2026, 3:42 PM" — uses the browser locale's medium date + short time. */
export function fmtLocalDateTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * Relative time, terse — "just now" / "5m ago" / "2h ago" / "3d ago" / "2w ago".
 * For anything older than ~8 weeks falls back to fmtLocalDate, so a very old
 * kill reads as a date rather than a noisy "127w ago".
 */
export function fmtRelative(unixSeconds: number, now: number = Date.now() / 1000): string {
  const diff = Math.max(0, now - unixSeconds)
  if (diff < 60) return 'just now'
  if (diff < 60 * 60) return `${Math.floor(diff / 60)}m ago`
  if (diff < 60 * 60 * 24) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 60 * 60 * 24 * 7) return `${Math.floor(diff / 86400)}d ago`
  if (diff < 60 * 60 * 24 * 56) return `${Math.floor(diff / (86400 * 7))}w ago`
  return fmtLocalDate(unixSeconds)
}
