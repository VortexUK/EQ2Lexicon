import { describe, it, expect, vi, afterEach } from 'vitest'

import { mergeParams, safeSetParams } from './searchParams'

describe('mergeParams', () => {
  it('merges a key while preserving existing params', () => {
    const prev = new URLSearchParams('tab=aas&x=1')
    const next = mergeParams({ profile: 'Templar_Heal' })(prev)
    expect(next.get('tab')).toBe('aas')
    expect(next.get('x')).toBe('1')
    expect(next.get('profile')).toBe('Templar_Heal')
  })

  it('deletes a key on null/empty and does not mutate prev', () => {
    const prev = new URLSearchParams('tab=aas&profile=X')
    const next = mergeParams({ profile: null, tree: '' })(prev)
    expect(next.has('profile')).toBe(false)
    expect(next.has('tree')).toBe(false)
    expect(prev.get('profile')).toBe('X') // input untouched
  })
})

describe('safeSetParams', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('calls setParams with the given args', () => {
    const set = vi.fn()
    safeSetParams(set, ['next', { replace: true }])
    expect(set).toHaveBeenCalledWith('next', { replace: true })
  })

  it('swallows a DOMException SecurityError (History-API throttle) without throwing', () => {
    vi.useFakeTimers()
    const set = vi.fn(() => {
      throw new DOMException('quota depleted', 'SecurityError')
    })
    expect(() => safeSetParams(set, ['next'])).not.toThrow()
    // The retry is scheduled but also swallowed — advancing timers must not throw.
    expect(() => vi.runOnlyPendingTimers()).not.toThrow()
    expect(set).toHaveBeenCalledTimes(2) // initial + one retry
  })

  it('rethrows non-DOMException errors', () => {
    const set = vi.fn(() => {
      throw new TypeError('boom')
    })
    expect(() => safeSetParams(set, ['next'])).toThrow('boom')
  })
})
