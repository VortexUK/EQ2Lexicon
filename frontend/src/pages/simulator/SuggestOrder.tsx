import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import {
  createOptimizer,
  OPTIMIZER_BATCH,
  OPTIMIZER_BUDGET_MS,
  type OptimizeResult,
} from './optimizer'
import type { RotationAbility, SimConfig } from './types'

// "Suggest order": random-restart hill climb over the CURRENT priority
// list (add everything you'd consider casting first). Runs in small
// batches with macrotask yields so the page stays responsive; ends on
// the time budget or the sim cap, then offers Apply / Dismiss.

type Phase =
  | { kind: 'idle' }
  | { kind: 'running'; sims: number; bestDps: number }
  | { kind: 'done'; result: OptimizeResult }

export default function SuggestOrder({ simConfig, abilities, onApply }: {
  simConfig: SimConfig | null
  abilities: Record<string, RotationAbility>
  onApply: (order: string[]) => void
}) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const runIdRef = useRef(0)

  // Any config change invalidates a finished suggestion (it was computed
  // against different stats/buffs/rotation) — and cancels a running one.
  useEffect(() => {
    runIdRef.current++
    setPhase({ kind: 'idle' })
  }, [simConfig])

  const canRun = simConfig != null && simConfig.rotation.length >= 2

  const run = async () => {
    if (!simConfig) return
    const runId = ++runIdRef.current
    const optimizer = createOptimizer(simConfig, { seed: (Date.now() % 100000) + 1 })
    const start = performance.now()
    setPhase({ kind: 'running', sims: 0, bestDps: 0 })
    while (!optimizer.done() && performance.now() - start < OPTIMIZER_BUDGET_MS) {
      optimizer.step(OPTIMIZER_BATCH)
      if (runIdRef.current !== runId) return // cancelled by a config change
      const r = optimizer.result()
      setPhase({ kind: 'running', sims: r.simsRun, bestDps: r.dps })
      await new Promise(resolve => setTimeout(resolve, 0))
      if (runIdRef.current !== runId) return
    }
    setPhase({ kind: 'done', result: optimizer.result() })
  }

  return (
    <Card className="rounded-sm px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <SectionLabel>Suggest order</SectionLabel>
        <Button variant="secondary" size="sm" disabled={!canRun || phase.kind === 'running'} onClick={run}>
          {phase.kind === 'running' ? 'Searching…' : 'Suggest order'}
        </Button>
        {phase.kind === 'running' && (
          <span className="text-[0.78rem] text-text-muted">
            {fmtNum(phase.sims)} orders tried · best {fmtNum(Math.round(phase.bestDps))} DPS
          </span>
        )}
        {!canRun && phase.kind === 'idle' && (
          <span className="text-[0.75rem] text-text-muted">
            Add at least two abilities — include everything you'd consider casting.
          </span>
        )}
      </div>

      {phase.kind === 'done' && (
        <div className="mt-2">
          {phase.result.improved ? (
            <>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[0.88rem]">
                <span className="text-text-muted">{fmtNum(Math.round(phase.result.baselineDps))} DPS</span>
                <span className="text-text-muted">→</span>
                <span className="text-gold font-semibold">{fmtNum(Math.round(phase.result.dps))} DPS</span>
                <Badge variant="success">
                  +{(100 * (phase.result.dps / Math.max(phase.result.baselineDps, 1e-9) - 1)).toFixed(1)}%
                </Badge>
                <span className="text-[0.72rem] text-text-muted">
                  {fmtNum(phase.result.simsRun)} orders tried
                </span>
              </div>
              <ol className="list-none m-0 mt-1.5 p-0 flex flex-wrap gap-x-3 gap-y-1 text-[0.8rem]">
                {phase.result.order.map((n, i) => (
                  <li key={n}>
                    <span className="text-gold font-semibold">{i + 1}.</span>{' '}
                    {abilities[n]?.name ?? n}
                  </li>
                ))}
              </ol>
              <div className="flex gap-2 mt-2">
                <Button variant="primary" size="sm" onClick={() => onApply(phase.result.order)}>
                  Apply
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setPhase({ kind: 'idle' })}>
                  Dismiss
                </Button>
              </div>
            </>
          ) : (
            <p className="text-[0.82rem] text-text-muted m-0">
              Your current order held up — nothing better found in {fmtNum(phase.result.simsRun)} tried
              orders ({fmtNum(Math.round(phase.result.baselineDps))} DPS).
            </p>
          )}
        </div>
      )}
    </Card>
  )
}
