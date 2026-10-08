import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NEGATIVE_TTL_MS, _resetItemCacheForTest, getCachedItem, loadItem, prefetchItem } from './ItemTooltip'

function okResponse(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function failResponse(status: number): Response {
  return { ok: false, status, json: () => Promise.reject(new Error('no body')) } as unknown as Response
}

describe('item tooltip cache', () => {
  beforeEach(() => {
    _resetItemCacheForTest()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('shares one request between concurrent loads and caches the result', async () => {
    let resolve!: (r: Response) => void
    const fetchMock = vi.fn(() => new Promise<Response>(r => { resolve = r }))
    vi.stubGlobal('fetch', fetchMock)

    const a = loadItem('42')
    const b = loadItem('42')
    const c = prefetchItem('42')
    expect(fetchMock).toHaveBeenCalledTimes(1)

    resolve(okResponse({ id: 42, name: 'Sword' }))
    const [ra, rb] = await Promise.all([a, b, c])
    expect(ra).toEqual({ id: 42, name: 'Sword' })
    expect(rb).toBe(ra)
    expect(getCachedItem('42')).toBe(ra)

    await loadItem('42')
    expect(fetchMock).toHaveBeenCalledTimes(1) // served from cache
  })

  it('remembers a failed load for NEGATIVE_TTL_MS and then retries', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(failResponse(404)))
    vi.stubGlobal('fetch', fetchMock)

    expect(await loadItem('7')).toBeNull()
    expect(await loadItem('7')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1) // negative-cached, no second request

    vi.advanceTimersByTime(NEGATIVE_TTL_MS + 1)
    fetchMock.mockResolvedValueOnce(okResponse({ id: 7, name: 'Late' }))
    expect(await loadItem('7')).toEqual({ id: 7, name: 'Late' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('treats a network error like a failed load', async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error('offline')))
    vi.stubGlobal('fetch', fetchMock)
    expect(await loadItem('9')).toBeNull()
    expect(await loadItem('9')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
