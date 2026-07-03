/**
 * Tests for the two exported utilities of RankingsPage.
 *
 * `normaliseBossName` is mirrored on the backend as `_normalise_boss_key`
 * in web/routes/rankings.py — the backend has 11 tests of identical shape.
 * Keep this file's apostrophe/space coverage parallel.
 *
 * `safeSetParams` is the safety net for Firefox's History-API throttle
 * being depleted by browser extensions (ClearURLs, Privacy Badger) or
 * Firefox tracking-protection internals. See the [[url-state-needs-
 * defensive-react-mirror]] memory for context.
 */
import { describe, it, expect, vi } from 'vitest'
import { normaliseBossName } from './RankingsPage'
import { safeSetParams } from '../lib/searchParams'

// ── normaliseBossName ────────────────────────────────────────────────────────

describe('normaliseBossName', () => {
  it('lowercases ASCII apostrophe names unchanged', () => {
    expect(normaliseBossName("D'Lizta Cheroon")).toBe("d'lizta cheroon")
  })

  it('folds U+2019 right single quote to ASCII apostrophe', () => {
    expect(normaliseBossName('D’Lizta Cheroon')).toBe("d'lizta cheroon")
  })

  it('folds U+2018 left single quote', () => {
    expect(normaliseBossName('D‘Lizta')).toBe("d'lizta")
  })

  it('folds U+02BC modifier letter apostrophe', () => {
    expect(normaliseBossName('VʼTekla KʼZalk')).toBe("v'tekla k'zalk")
  })

  it('folds U+02BB modifier letter turned comma', () => {
    expect(normaliseBossName('VʻTekla')).toBe("v'tekla")
  })

  it('folds U+2032 prime', () => {
    expect(normaliseBossName('Foo′s Bar')).toBe("foo's bar")
  })

  it('folds U+FF07 fullwidth apostrophe', () => {
    expect(normaliseBossName('D＇Lizta')).toBe("d'lizta")
  })

  it('folds U+00A0 NBSP to ASCII space', () => {
    expect(normaliseBossName("V'Tekla K'Zalk")).toBe("v'tekla k'zalk")
  })

  it('folds U+2009 thin space to ASCII space', () => {
    expect(normaliseBossName("Foo Bar")).toBe('foo bar')
  })

  it('folds U+3000 ideographic space to ASCII space', () => {
    expect(normaliseBossName("Foo　Bar")).toBe('foo bar')
  })

  it('strips leading/trailing whitespace', () => {
    expect(normaliseBossName("  D'Lizta Cheroon  ")).toBe("d'lizta cheroon")
  })

  it('NFC-normalises composed forms (decomposed é → composed é)', () => {
    // decomposed: e (U+0065) + combining acute (U+0301)
    const decomposed = 'Vé́mm'
    // NFC normalises this to the canonical composed form before folding.
    expect(normaliseBossName(decomposed)).toBe(normaliseBossName(decomposed.normalize('NFC')))
  })

  it('is idempotent — folding twice gives the same result', () => {
    const once = normaliseBossName('V’Tekla K’Zalk')
    expect(normaliseBossName(once)).toBe(once)
  })

  it('returns lowercase for already-folded inputs', () => {
    expect(normaliseBossName('VYEMM')).toBe('vyemm')
  })

  it('produces matching keys for ASCII and U+2019 variants of the same name', () => {
    expect(normaliseBossName("D'Lizta")).toBe(normaliseBossName('D’Lizta'))
  })
})

// ── safeSetParams ────────────────────────────────────────────────────────────

describe('safeSetParams', () => {
  it('forwards args to setParams when the call succeeds', () => {
    const setParams = vi.fn()
    safeSetParams(setParams, [new URLSearchParams('boss=Enynti'), { replace: true }])
    expect(setParams).toHaveBeenCalledOnce()
    expect(setParams).toHaveBeenCalledWith(
      expect.any(URLSearchParams),
      { replace: true },
    )
  })

  it('catches DOMException SecurityError and does not re-throw to the caller', () => {
    // This is the throttle-storm case — Firefox's per-Document History API
    // quota has been depleted by an extension (e.g. ClearURLs) and react-
    // router's setSearchParams throws synchronously. The page MUST not crash;
    // worst case the URL stays stale, React state is the source of truth.
    const setParams = vi.fn(() => {
      throw new DOMException('history quota exhausted', 'SecurityError')
    })
    expect(() => safeSetParams(setParams, [new URLSearchParams()])).not.toThrow()
    expect(setParams).toHaveBeenCalledOnce()
  })

  it('catches DOMException InvalidStateError too', () => {
    const setParams = vi.fn(() => {
      throw new DOMException('invalid state', 'InvalidStateError')
    })
    expect(() => safeSetParams(setParams, [new URLSearchParams()])).not.toThrow()
  })

  it('re-throws non-DOMException errors so real bugs are not swallowed', () => {
    const setParams = vi.fn(() => {
      throw new TypeError('coding bug in caller')
    })
    expect(() => safeSetParams(setParams, [new URLSearchParams()])).toThrow(TypeError)
  })

  it('re-throws DOMException with other names (only quota-related names are swallowed)', () => {
    // e.g. NotAllowedError, AbortError — not throttle, should propagate.
    const setParams = vi.fn(() => {
      throw new DOMException('not allowed', 'NotAllowedError')
    })
    expect(() => safeSetParams(setParams, [new URLSearchParams()])).toThrow(DOMException)
  })

  it('schedules a retry 1.2s after a throttle throw', () => {
    vi.useFakeTimers()
    try {
      // First call throws, second call (retry) succeeds.
      const setParams = vi
        .fn()
        .mockImplementationOnce(() => {
          throw new DOMException('throttled', 'SecurityError')
        })
        .mockImplementationOnce(() => {
          /* retry succeeds */
        })

      safeSetParams(setParams, [new URLSearchParams('boss=Enynti')])
      expect(setParams).toHaveBeenCalledOnce() // only the first throw so far

      // Advance time by 1.2s — the retry should fire.
      vi.advanceTimersByTime(1200)
      expect(setParams).toHaveBeenCalledTimes(2)
      expect(setParams).toHaveBeenLastCalledWith(expect.any(URLSearchParams))
    } finally {
      vi.useRealTimers()
    }
  })

  it('silently swallows the retry throw — does not surface as unhandled rejection', () => {
    vi.useFakeTimers()
    try {
      // Both calls throw. The retry's throw must NOT propagate.
      const setParams = vi.fn(() => {
        throw new DOMException('still throttled', 'SecurityError')
      })

      expect(() => safeSetParams(setParams, [new URLSearchParams()])).not.toThrow()
      // Advance time, retry fires, also throws — must not crash.
      expect(() => vi.advanceTimersByTime(1200)).not.toThrow()
      expect(setParams).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })
})
