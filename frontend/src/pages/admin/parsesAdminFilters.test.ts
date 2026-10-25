import { describe, it, expect } from 'vitest'
import { EMPTY_FILTERS, filtersToParams, hasActiveFilters, localDayStart } from './parsesAdminFilters'

describe('filtersToParams', () => {
  it('sends nothing for empty filters', () => {
    expect(filtersToParams(EMPTY_FILTERS)).toBe('')
  })

  it('trims text filters and drops blank ones', () => {
    const q = new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, zone: '  Thundering ', guild: '   ' }))
    expect(q.get('zone')).toBe('Thundering')
    expect(q.has('guild')).toBe(false)
  })

  it('accepts an id with the "#" the table shows, and ignores a half-typed one', () => {
    expect(new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, id: '#123' })).get('id')).toBe('123')
    expect(new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, id: '12a' })).has('id')).toBe(false)
  })

  it('sends min_players only for a positive whole number', () => {
    expect(new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, minPlayers: '100' })).get('min_players')).toBe('100')
    expect(new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, minPlayers: '0' })).has('min_players')).toBe(false)
    expect(new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, minPlayers: '1e3' })).has('min_players')).toBe(false)
  })

  it('makes the date range inclusive of both days', () => {
    const q = new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, from: '2026-10-09', to: '2026-10-09' }))
    expect(Number(q.get('until')) - Number(q.get('since'))).toBe(localDayStart('2026-10-10')! - localDayStart('2026-10-09')!)
    expect(q.get('since')).toBe(String(localDayStart('2026-10-09')))
  })

  it('leaves status out when it is "all" and adds paging last', () => {
    const q = new URLSearchParams(filtersToParams({ ...EMPTY_FILTERS, status: 'hidden', result: '0' }, { limit: 1000, before: 5, beforeId: 9 }))
    expect(q.get('status')).toBe('hidden')
    expect(q.get('result')).toBe('0')
    expect([q.get('limit'), q.get('before'), q.get('before_id')]).toEqual(['1000', '5', '9'])
    expect(filtersToParams({ ...EMPTY_FILTERS, status: 'all' })).toBe('')
  })
})

describe('hasActiveFilters', () => {
  it('is false for the empty set and true once any column is set', () => {
    expect(hasActiveFilters(EMPTY_FILTERS)).toBe(false)
    expect(hasActiveFilters({ ...EMPTY_FILTERS, uploader: 'x' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY_FILTERS, status: 'visible' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY_FILTERS, title: '   ' })).toBe(false)
  })
})

describe('localDayStart', () => {
  it('rejects anything but YYYY-MM-DD', () => {
    expect(localDayStart('')).toBeNull()
    expect(localDayStart('10/09/2026')).toBeNull()
  })
})
