/**
 * ResultsCharts — import smoke (pulls recharts into jsdom) + the rolling
 * DPS smoothing math.
 */
import { describe, expect, it } from 'vitest'

// Allowance for the first import of the module, which loads all of recharts
// (see the test). Per test only: the global vitest timeout stays at its default.
const SLOW_IMPORT_MS = 30_000

describe('ResultsCharts', () => {
  // Explicit timeout: this is the first thing in the file to import recharts,
  // so it pays for transforming the whole library. It has overrun vitest's
  // default 5 s twice in the pre-push gate, when the suite shares the CPU with
  // the Python checks, and passes alone. The import is the point of the test.
  it('imports without throwing', async () => {
    await expect(import('./ResultsCharts')).resolves.toBeDefined()
  }, SLOW_IMPORT_MS)

  it('rollingDps is a centered mean that preserves edges', async () => {
    const { rollingDps } = await import('./ResultsCharts')
    const out = rollingDps([0, 0, 10, 0, 0], 5)
    expect(out).toHaveLength(5)
    expect(out[2].dps).toBeCloseTo(2) // 10 / 5
    expect(out[0].dps).toBeCloseTo(10 / 3) // window clipped at the edge
    expect(out.map(p => p.t)).toEqual([0, 1, 2, 3, 4])
  })
})
