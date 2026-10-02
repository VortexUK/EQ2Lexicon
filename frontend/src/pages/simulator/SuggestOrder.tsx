import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import {
  createOptimizer,
  OPTIMIZER_BATCH,
  OPTIMIZER_BUDGET_MS,
  type OptimizeResult,
} from './optimizer'
import type { SimConfig } from './types'

// "Suggest order": random-restart hill climb over the CURRENT priority
// list (add everything you'd consider casting first). Runs in small
// batches with macrotask yields so the page stays responsive; ends on
// the time budget or the sim cap, then offers Apply / Dismiss.

type Phase =
  | { kind: 'idle' }
  | { kind: 'running'; sims: number; bestDps: number }
  | { kind: 'done'; result: OptimizeResult }

export default function SuggestOrder({ simConfig, onApply }: {
  simConfig: SimConfig | null
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

  // Everything lives on ONE content row so the card never changes size —
  // it mirrors the Auto-attack card opposite it. Apply updates the
  // priority list in place; no need to preview the order here.
  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Suggest order</SectionLabel>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 mt-2 min-h-[2.1rem]">
        {phase.kind === 'done' && phase.result.improved ? (
          <>
            <span className="text-[0.85rem] text-text-muted">
              {fmtNum(Math.round(phase.result.baselineDps))} →{' '}
              <span className="text-gold font-semibold">{fmtNum(Math.round(phase.result.dps))}</span> DPS
            </span>
            <Badge variant="success">
              +{(100 * (phase.result.dps / Math.max(phase.result.baselineDps, 1e-9) - 1)).toFixed(1)}%
            </Badge>
            <Button variant="primary" size="sm" onClick={() => onApply(phase.result.order)}>
              Apply
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setPhase({ kind: 'idle' })}>
              Dismiss
            </Button>
          </>
        ) : (
          <>
            <Button variant="secondary" size="sm" disabled={!canRun || phase.kind === 'running'} onClick={run}>
              {phase.kind === 'running' ? 'Searching…' : 'Suggest order'}
            </Button>
            {phase.kind === 'running' && (
              <span className="text-[0.78rem] text-text-muted">
                {fmtNum(phase.sims)} tried · best {fmtNum(Math.round(phase.bestDps))} DPS
              </span>
            )}
            {phase.kind === 'done' && !phase.result.improved && (
              <span className="text-[0.78rem] text-text-muted">
                current order held up ({fmtNum(phase.result.simsRun)} tried)
              </span>
            )}
            {!canRun && phase.kind === 'idle' && (
              <span className="text-[0.75rem] text-text-muted">Add at least two abilities first.</span>
            )}
          </>
        )}
      </div>
    </Card>
  )
}
