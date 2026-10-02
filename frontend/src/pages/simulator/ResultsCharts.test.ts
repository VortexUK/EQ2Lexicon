/**
 * ResultsCharts — import smoke (pulls recharts into jsdom) + the rolling
 * DPS smoothing math.
 */
import { describe, expect, it } from 'vitest'

describe('ResultsCharts', () => {
  it('imports without throwing', async () => {
    await expect(import('./ResultsCharts')).resolves.toBeDefined()
  })

  it('rollingDps is a centered mean that preserves edges', async () => {
    const { rollingDps } = await import('./ResultsCharts')
    const out = rollingDps([0, 0, 10, 0, 0], 5)
    expect(out).toHaveLength(5)
    expect(out[2].dps).toBeCloseTo(2) // 10 / 5
    expect(out[0].dps).toBeCloseTo(10 / 3) // window clipped at the edge
    expect(out.map(p => p.t)).toEqual([0, 1, 2, 3, 4])
  })
})
