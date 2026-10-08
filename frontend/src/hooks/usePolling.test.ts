import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

import { usePolling } from './usePolling'

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  vi.useFakeTimers()
  setVisibility('visible')
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('usePolling', () => {
  it('runs immediately then on each interval', async () => {
    const fn = vi.fn()
    renderHook(() => usePolling(fn, 1000, { jitter: 0 }))
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(fn).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(2000)
    expect(fn).toHaveBeenCalledTimes(4)
  })

  it('does not run immediately when immediate is false', async () => {
    const fn = vi.fn()
    renderHook(() => usePolling(fn, 1000, { jitter: 0, immediate: false }))
    await vi.advanceTimersByTimeAsync(999)
    expect(fn).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('does nothing when disabled', async () => {
    const fn = vi.fn()
    renderHook(() => usePolling(fn, 1000, { enabled: false }))
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fn).not.toHaveBeenCalled()
  })

  it('pauses while hidden and runs once immediately on becoming visible', async () => {
    const fn = vi.fn()
    renderHook(() => usePolling(fn, 1000, { jitter: 0 }))
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fn).toHaveBeenCalledTimes(1)

    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1000)
    expect(fn).toHaveBeenCalledTimes(3)
  })

  it('does not run on becoming visible if no tick was missed', async () => {
    const fn = vi.fn()
    renderHook(() => usePolling(fn, 1000, { jitter: 0 }))
    await vi.advanceTimersByTimeAsync(0)
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(300)
    setVisibility('visible')
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(700)
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('applies jitter within ±fraction of the interval', async () => {
    const fn = vi.fn()
    const rand = vi.spyOn(Math, 'random')

    rand.mockReturnValue(0) // lowest: 1000 * 0.8
    const a = renderHook(() => usePolling(fn, 1000, { jitter: 0.2, immediate: false }))
    await vi.advanceTimersByTimeAsync(799)
    expect(fn).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(fn).toHaveBeenCalledTimes(1)
    a.unmount()

    fn.mockClear()
    rand.mockReturnValue(0.99) // near-highest: 1000 * 1.196
    renderHook(() => usePolling(fn, 1000, { jitter: 0.2, immediate: false }))
    await vi.advanceTimersByTimeAsync(1195)
    expect(fn).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('skips ticks while the previous run is still pending', async () => {
    let resolve!: () => void
    const fn = vi.fn(() => new Promise<void>(r => { resolve = r }))
    renderHook(() => usePolling(fn, 1000, { jitter: 0 }))
    await vi.advanceTimersByTimeAsync(0)
    expect(fn).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(fn).toHaveBeenCalledTimes(1)
    resolve()
    await vi.advanceTimersByTimeAsync(1000)
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('stops on unmount', async () => {
    const fn = vi.fn()
    const { unmount } = renderHook(() => usePolling(fn, 1000, { jitter: 0 }))
    await vi.advanceTimersByTimeAsync(0)
    unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(fn).toHaveBeenCalledTimes(1)
  })
})
