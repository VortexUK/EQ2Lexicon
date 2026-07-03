import { describe, it, expect } from 'vitest'

import { minutesToHHMM, hhmmToMinutes, tzOffsetMinutes, toViewerMinutes } from './timezone'

// A fixed winter instant so offsets are deterministic (no DST ambiguity, no Date.now()).
const REF = Date.UTC(2026, 0, 15, 12, 0, 0)

describe('minutesToHHMM / hhmmToMinutes', () => {
  it('formats + parses HH:MM', () => {
    expect(minutesToHHMM(1200)).toBe('20:00')
    expect(minutesToHHMM(60)).toBe('01:00')
    expect(minutesToHHMM(-60)).toBe('23:00') // wraps
    expect(hhmmToMinutes('20:00')).toBe(1200)
    expect(hhmmToMinutes('08:30')).toBe(510)
  })
  it('rejects malformed times', () => {
    expect(hhmmToMinutes('25:00')).toBeNull()
    expect(hhmmToMinutes('12:60')).toBeNull()
    expect(hhmmToMinutes('noon')).toBeNull()
  })
})

describe('tzOffsetMinutes', () => {
  it('gives the IANA offset at an instant', () => {
    expect(tzOffsetMinutes(REF, 'UTC')).toBe(0)
    expect(tzOffsetMinutes(REF, 'America/New_York')).toBe(-300) // EST
    expect(tzOffsetMinutes(REF, 'America/Los_Angeles')).toBe(-480) // PST
    expect(tzOffsetMinutes(REF, 'Europe/London')).toBe(0) // GMT in Jan
  })
})

describe('toViewerMinutes', () => {
  it('converts a team-tz wall clock into the viewer tz', () => {
    // 20:00 New York → 17:00 Los Angeles, same day.
    expect(toViewerMinutes(1200, 'America/New_York', 'America/Los_Angeles', REF)).toEqual({ minutes: 1020, dayShift: 0 })
    // 20:00 New York → 01:00 London, next day.
    expect(toViewerMinutes(1200, 'America/New_York', 'Europe/London', REF)).toEqual({ minutes: 60, dayShift: 1 })
    // Same tz → unchanged.
    expect(toViewerMinutes(600, 'UTC', 'UTC', REF)).toEqual({ minutes: 600, dayShift: 0 })
  })
})
