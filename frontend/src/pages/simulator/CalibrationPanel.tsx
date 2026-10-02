import { Card, SectionLabel } from '../../components/ui'
import { fmtNum } from '../../formatters'
import { predictedTooltipMin } from './formulas'
import type { CalibrationResult, ObservedHits } from './calibration'
import type { RotationAbility, SimStats } from './types'

// Calibration: the predicted value is the MINIMUM of the ability's
// in-game tooltip range (the model is tooltip-validated; the max derives
// from the min via the base ratio). The user enters their tooltip's
// minimum; factor = observed / predicted absorbs residual model error.
// Unobserved abilities inherit the mean factor.

const NUM_CLASS =
  'py-1 px-2 rounded-sm2 border border-border bg-surface-raised text-text text-[0.82rem] w-24 [color-scheme:dark]'

export default function CalibrationPanel({ rotation, abilities, stats, observed, calibration, extraAbilities, onChange }: {
  rotation: string[]
  abilities: Record<string, RotationAbility>
  stats: SimStats
  observed: ObservedHits
  calibration: CalibrationResult
  /** Non-rotation damage sources that still calibrate against their
   * in-game tooltip (maintained toggles like Exorcise). */
  extraAbilities?: RotationAbility[]
  onChange: (observed: ObservedHits) => void
}) {
  const rows = [
    ...rotation.map(name => abilities[name]).filter((a): a is RotationAbility => a != null),
    ...(extraAbilities ?? []),
  ]
    .map(a => ({ a, predicted: predictedTooltipMin(a, stats) }))
    .filter(r => r.predicted != null && r.predicted > 0)

  if (rows.length === 0) {
    return (
      <Card className="rounded-sm px-4 py-3">
        <SectionLabel>Calibration</SectionLabel>
        <p className="text-[0.82rem] text-text-muted mt-2 mb-0">
          Add damage abilities to the rotation, then enter the minimum from each in-game tooltip
          to tune the predictions.
        </p>
      </Card>
    )
  }

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Calibration</SectionLabel>
      <p className="text-[0.75rem] text-text-muted mt-1 mb-2">
        Predicted = the <em>minimum</em> of the ability's in-game tooltip range. Enter the minimum
        your tooltip shows and the sim scales that ability to match. Abilities without a value use
        the mean factor
        {calibration.globalFactor !== 1 && <> (currently ×{calibration.globalFactor.toFixed(2)})</>}.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[0.82rem]">
          <thead>
            <tr className="border-b border-border text-left text-text-muted text-[0.72rem] uppercase tracking-wide">
              <th className="py-1 pr-2 font-semibold">Ability</th>
              <th className="py-1 px-2 font-semibold text-right">Predicted min</th>
              <th className="py-1 px-2 font-semibold text-right">Tooltip min</th>
              <th className="py-1 pl-2 font-semibold text-right">Factor</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ a, predicted }) => {
              const factor = calibration.observedFactors[a.base_name]
              return (
                <tr key={a.base_name} className="border-b border-border">
                  <td className="py-1.5 pr-2">{a.name}</td>
                  <td className="py-1.5 px-2 text-right text-text-muted">{fmtNum(Math.round(predicted as number))}</td>
                  <td className="py-1.5 px-2 text-right">
                    <input
                      type="number"
                      min={0}
                      value={observed[a.base_name] ?? ''}
                      placeholder="—"
                      className={NUM_CLASS}
                      onChange={e => {
                        const raw = e.target.value
                        const next = { ...observed }
                        const v = Number(raw)
                        if (raw === '' || !Number.isFinite(v) || v <= 0) delete next[a.base_name]
                        else next[a.base_name] = v
                        onChange(next)
                      }}
                      aria-label={`In-game tooltip minimum for ${a.name}`}
                    />
                  </td>
                  <td className="py-1.5 pl-2 text-right">
                    {factor != null ? (
                      <span className={factor > 1.05 || factor < 0.95 ? 'text-gold' : 'text-text-muted'}>
                        ×{factor.toFixed(2)}
                      </span>
                    ) : (
                      <span className="text-text-muted">×{calibration.globalFactor.toFixed(2)}</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
