/**
 * Verify the historyTrace monkey-patch installs without throwing and that
 * patched APIs still function. Regression test for the v5 bug where
 * `window.location.assign = function(){}` threw TypeError ("assign is
 * read-only") at module load and broke the entire app.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

describe('historyTrace', () => {
  beforeEach(() => {
    // Wipe the install marker so the patch re-runs each test.
    delete (window as unknown as { __historyTracedV6?: boolean }).__historyTracedV6
    vi.resetModules()
  })

  it('installs without throwing', async () => {
    // Importing the module is the install step. If anything throws — including
    // the "assign is read-only" trap that broke v5 — this test fails.
    await expect(import('./historyTrace')).resolves.toBeDefined()
    expect(
      (window as unknown as { __historyTracedV6?: boolean }).__historyTracedV6,
    ).toBe(true)
  })

  it('logs pushState calls via console.warn', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await import('./historyTrace')
    warn.mockClear() // ignore the install banner

    window.history.pushState(null, '', '/test-path')

    const tracedCall = warn.mock.calls.find(c =>
      String(c[0] ?? '').includes('History.pushState'),
    )
    expect(tracedCall).toBeDefined()
    warn.mockRestore()
  })

  it('logs replaceState calls', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await import('./historyTrace')
    warn.mockClear()

    window.history.replaceState(null, '', '/test-replace')

    const tracedCall = warn.mock.calls.find(c =>
      String(c[0] ?? '').includes('History.replaceState'),
    )
    expect(tracedCall).toBeDefined()
    warn.mockRestore()
  })

  it('still allows pushState/replaceState to actually update the URL', async () => {
    await import('./historyTrace')
    // jsdom updates window.location on pushState
    window.history.pushState(null, '', '/post-patch-test')
    expect(window.location.pathname).toBe('/post-patch-test')
  })

  it('does not throw on Location method patch (the v5 regression)', async () => {
    // The whole point of v6 — Location.prototype.assign override using
    // Object.defineProperty must not throw at install time the way
    // `window.location.assign = function(){}` did.
    await expect(import('./historyTrace')).resolves.toBeDefined()
    // Bonus check — the methods still exist and are callable as functions
    // (jsdom won't actually navigate but the method shouldn't throw).
    expect(typeof window.location.assign).toBe('function')
    expect(typeof window.location.replace).toBe('function')
  })
})
