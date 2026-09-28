/**
 * Helpers shared by the chart components — kept out of the component files
 * so React Fast Refresh sees component-only modules.
 */
import { useEffect, useState } from 'react'

/** Read a CSS custom property off :root once mounted; `fallback` until then
 * (and forever in jsdom, which has no computed custom properties). */
export function useThemeColour(varName: string, fallback: string): string {
  const [colour, setColour] = useState(fallback)
  useEffect(() => {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
      if (v) setColour(v)
    } catch {
      /* no document — keep the fallback */
    }
  }, [varName])
  return colour
}

/** Midday UTC of a `YYYY-MM-DD` day, in unix seconds — a stable x value that
 * formats to the same calendar date in every timezone from UTC−11 to +11. */
export function dayToUnix(day: string): number {
  const [y, m, d] = day.split('-').map(Number)
  return Date.UTC(y, m - 1, d, 12) / 1000
}
