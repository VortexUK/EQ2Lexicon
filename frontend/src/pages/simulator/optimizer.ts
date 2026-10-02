// Rotation optimizer — random-restart hill climb over priority
// orderings. Pure and deterministic under an injected PRNG; the UI runs
// it in small batches (createOptimizer().step) with macrotask yields so
// the main thread stays responsive. Neighbor moves: random pair swap +
// move-to-front (the two moves that matter for a priority list).

import { simulate } from './engine'
import type { SimConfig } from './types'

export const OPTIMIZER_BUDGET_MS = 2000
export const OPTIMIZER_MAX_SIMS = 4000
/** Consecutive non-improving neighbors before a random restart. */
export const OPTIMIZER_STALL_LIMIT = 40
/** Sims per UI batch (each is sub-ms; ~50 keeps frames comfortable). */
export const OPTIMIZER_BATCH = 50

/** Small fast seeded PRNG (mulberry32) — deterministic tests. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a += 0x6d2b79f5
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export interface OptimizeResult {
  /** The best priority order found (a permutation of the input). */
  order: string[]
  dps: number
  baselineDps: number
  simsRun: number
  /** True when `order` strictly beats the CURRENT order. */
  improved: boolean
}

export interface OptimizerOptions {
  seed?: number
  rng?: () => number
  maxSims?: number
}

export interface Optimizer {
  /** Run up to n more sims (hill-climb steps). */
  step(n: number): void
  /** True once the sim cap is reached (nothing left to try). */
  done(): boolean
  result(): OptimizeResult
}

export function createOptimizer(config: SimConfig, opts: OptimizerOptions = {}): Optimizer {
  const rng = opts.rng ?? mulberry32(opts.seed ?? 1)
  const maxSims = opts.maxSims ?? OPTIMIZER_MAX_SIMS
  const order0 = config.rotation.filter(n => config.abilities[n])

  let sims = 0
  const dpsOf = (order: string[]): number => {
    sims++
    return simulate({ ...config, rotation: order }).dps
  }

  const shuffled = (): string[] => {
    const a = order0.slice()
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1))
      ;[a[i], a[j]] = [a[j], a[i]]
    }
    return a
  }

  const baseline = order0.length > 0 ? dpsOf(order0) : 0
  let bestOrder = order0
  let bestDps = baseline
  let cur = order0.slice()
  let curDps = baseline
  let stall = 0

  const stepOnce = (): void => {
    const n = cur.length
    if (n < 2) return
    const cand = cur.slice()
    const i = Math.floor(rng() * n)
    if (rng() < 0.5) {
      let j = Math.floor(rng() * n)
      if (j === i) j = (j + 1) % n
      ;[cand[i], cand[j]] = [cand[j], cand[i]]
    } else {
      const [moved] = cand.splice(i, 1)
      cand.unshift(moved)
    }
    const d = dpsOf(cand)
    if (d > curDps + 1e-9) {
      cur = cand
      curDps = d
      stall = 0
      if (d > bestDps) {
        bestOrder = cand
        bestDps = d
      }
    } else if (++stall >= OPTIMIZER_STALL_LIMIT && sims < maxSims) {
      cur = shuffled()
      curDps = dpsOf(cur)
      stall = 0
      if (curDps > bestDps) {
        bestOrder = cur
        bestDps = curDps
      }
    }
  }

  return {
    step(n: number): void {
      for (let k = 0; k < n && sims < maxSims && order0.length >= 2; k++) stepOnce()
    },
    done(): boolean {
      return sims >= maxSims || order0.length < 2
    },
    result(): OptimizeResult {
      return {
        order: bestOrder.slice(),
        dps: bestDps,
        baselineDps: baseline,
        simsRun: sims,
        improved: bestDps > baseline + 1e-6,
      }
    },
  }
}

/** Synchronous budget-bounded run (tests / non-UI callers). */
export function optimize(
  config: SimConfig,
  opts: OptimizerOptions & { budgetMs?: number; now?: () => number } = {},
): OptimizeResult {
  const now = opts.now ?? (() => Date.now())
  const budget = opts.budgetMs ?? OPTIMIZER_BUDGET_MS
  const start = now()
  const o = createOptimizer(config, opts)
  while (!o.done() && now() - start < budget) o.step(OPTIMIZER_BATCH)
  return o.result()
}
