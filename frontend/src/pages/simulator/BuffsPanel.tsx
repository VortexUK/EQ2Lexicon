import { useState } from 'react'
import { Badge, Button, Card, SectionLabel } from '../../components/ui'
import type { RotationBuffSheet } from '../../data/rotationBuffs'
import CharacterSearchInput from './CharacterSearchInput'
import type { ExternalBuffConfig, SuppliedBuffInfo } from './types'

// External raid buffs: per buff, how many providers rotate it (staggered
// windows in the engine — 2 troubs cycling Jester's Cap ⇒ ~100% uptime),
// with user-editable duration/recast (census timings are era-drifted for
// some of these) and the resulting sim uptime. Buffs with a censusBase
// can name their SUPPLIER character (the out-of-group bard rotating
// PotM/CoB onto the raid): their owned rank then drives timing, mods
// and proc damage at THEIR stats.

const NUM_CLASS =
  'py-1 px-2 rounded-sm2 border border-border bg-surface-raised text-text text-[0.82rem] w-16 [color-scheme:dark]'

export default function BuffsPanel({ sheet, exact, configs, permanentEnabled, uptimes, selfUptimes, supplied, tierOptions, tierSelected, onChange, onSetSupplier, onTogglePermanent, onSetTier }: {
  sheet: RotationBuffSheet
  exact: boolean
  configs: ExternalBuffConfig[]
  /** Enabled always-on group buffs (permanent kind, by buff id). */
  permanentEnabled: string[]
  /** buffId → uptime % from the last sim run. */
  uptimes: Record<string, number>
  /** Own temp buffs cast in the rotation: base_name → uptime %. */
  selfUptimes: Record<string, number>
  /** buffId → supplier resolution (their rank of the spell, status). */
  supplied: Record<string, SuppliedBuffInfo | undefined>
  /** buffId → available spell tiers (Apprentice → Master) for the
   * raid-wide permanents; the selected tier drives the mod values. */
  tierOptions: Record<string, string[]>
  /** buffId → assumed tier (absent = Expert). */
  tierSelected: Record<string, string>
  onChange: (configs: ExternalBuffConfig[]) => void
  onSetSupplier: (buffId: string, name: string | null) => void
  onTogglePermanent: (buffId: string, enabled: boolean) => void
  onSetTier: (buffId: string, tier: string) => void
}) {
  // Per-buff expand state — rows collapse to one line (most people only
  // configure a couple of these, and expanded they dominate the panel).
  const [expanded, setExpanded] = useState<string[]>([])
  const cfgFor = (id: string) => configs.find(c => c.buffId === id)

  const update = (id: string, patch: Partial<ExternalBuffConfig>) => {
    const existing = cfgFor(id)
    if (existing) {
      onChange(configs.map(c => (c.buffId === id ? { ...c, ...patch } : c)))
    } else {
      onChange([...configs, { buffId: id, providers: 0, ...patch }])
    }
  }

  const permanents = sheet.buffs.filter(b => b.kind === 'permanent')
  const temporaries = sheet.buffs.filter(b => b.kind === 'temporary')

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Raid buffs</SectionLabel>
      {!exact && (
        <p className="text-[0.75rem] text-warning mt-1 mb-0">
          No buff sheet for this expansion yet — showing {sheet.label}.
        </p>
      )}

      {permanents.length > 0 && (
        <div className="mt-2">
          <div className="text-[0.72rem] text-text-muted uppercase tracking-wide mb-1.5">
            In your group (always on)
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {permanents.map(buff => {
              const tiers = tierOptions[buff.id] ?? []
              return (
                <div key={buff.id} className="flex items-center gap-1.5 text-[0.8rem]">
                  <label className="flex items-center gap-1.5 cursor-pointer" title={buff.note}>
                    <input
                      type="checkbox"
                      checked={permanentEnabled.includes(buff.id)}
                      onChange={e => onTogglePermanent(buff.id, e.target.checked)}
                      className="accent-[var(--color-gold)]"
                    />
                    {buff.name}
                    <span className="text-[0.68rem] text-text-muted">{buff.sourceClass}</span>
                    {(buff.priestOnlyMods?.length ?? 0) > 0 && (
                      <Badge variant="muted" className="cursor-help" title={buff.note}>
                        Fervor: priests only
                      </Badge>
                    )}
                  </label>
                  {tiers.length > 0 ? (
                    <select
                      value={tierSelected[buff.id] ?? (tiers.includes('Expert') ? 'Expert' : tiers[tiers.length - 1])}
                      onChange={e => onSetTier(buff.id, e.target.value)}
                      title="Assumed spell tier — sets the buff's values"
                      className="py-0.5 px-1 rounded-sm2 border border-border bg-surface-raised text-text text-[0.72rem] [color-scheme:dark]"
                    >
                      {tiers.map(t => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                  ) : (
                    buff.todoValues && <Badge variant="warning" className="cursor-help" title={buff.note}>?</Badge>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="text-[0.72rem] text-text-muted uppercase tracking-wide mt-3 mb-1.5">
        Rotated temporaries
      </div>
      <div className="flex flex-col gap-2">
        {temporaries.map(buff => {
          const cfg = cfgFor(buff.id)
          const sup = supplied[buff.id]
          const entry = sup?.status === 'ok' ? sup.entry : undefined
          const providers = cfg?.providers ?? 0
          const duration = cfg?.duration_s ?? entry?.duration_s ?? buff.duration_s
          const recast = cfg?.recast_s ?? (entry?.recast_s || buff.recast_s)
          const uptime = uptimes[buff.id]
          const open = expanded.includes(buff.id)
          // "unverified" marks curated ESTIMATES. A resolved supplier
          // replaces them with the real book entry (parsed mods and/or
          // procs), so the badge only shows while estimates are in use.
          const usingEstimates =
            buff.todoValues === true &&
            !(entry && (Object.keys(entry.mods).length > 0 || entry.procs.length > 0))
          return (
            <div key={buff.id} className="px-2 py-2 rounded-sm bg-surface-raised border border-border">
              <button
                type="button"
                onClick={() => setExpanded(prev => (open ? prev.filter(x => x !== buff.id) : [...prev, buff.id]))}
                className="appearance-none border-0 bg-transparent w-full p-0 text-left cursor-pointer flex flex-wrap items-center gap-x-3 gap-y-1 text-text"
                aria-expanded={open}
                title={open ? 'Collapse' : 'Expand to configure'}
              >
                <span className="text-[0.7rem] text-text-muted w-3">{open ? '▾' : '▸'}</span>
                <span className="text-[0.85rem] font-medium">{buff.name}</span>
                <span className="text-[0.72rem] text-text-muted">{buff.sourceClass}</span>
                {usingEstimates && (
                  <Badge
                    variant="warning"
                    className="cursor-help"
                    title={`${buff.note ?? ''}\nEstimated values — attach a supplier character (real spell data), or report the in-game numbers so the sheet can be corrected.`.trim()}
                  >
                    unverified
                  </Badge>
                )}
                {providers > 0 && uptime != null && (
                  <Badge variant={uptime >= 99.5 ? 'success' : 'info'}>{uptime.toFixed(0)}% uptime</Badge>
                )}
                {!open && (
                  <span className="text-[0.72rem] text-text-muted ml-auto">
                    {providers > 0 ? `${providers}× · ${duration}s/${recast}s` : 'off'}
                    {sup ? ` · ${sup.supplier}` : ''}
                  </span>
                )}
              </button>
              {open && (
              <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-1.5">
                <span className="flex items-center gap-1.5 text-[0.78rem]">
                  Providers
                  <Button variant="ghost" size="icon" disabled={providers <= 0} onClick={() => update(buff.id, { providers: providers - 1 })}>−</Button>
                  <span className="w-4 text-center font-semibold text-gold">{providers}</span>
                  <Button variant="ghost" size="icon" disabled={providers >= 4} onClick={() => update(buff.id, { providers: providers + 1 })}>+</Button>
                </span>
                <label className="flex items-center gap-1.5 text-[0.78rem] text-text-muted">
                  Duration
                  <input
                    type="number" min={1} max={600} value={duration} className={NUM_CLASS}
                    onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v > 0) update(buff.id, { duration_s: v }) }}
                    aria-label={`${buff.name} duration seconds`}
                  />
                  s
                </label>
                <label className="flex items-center gap-1.5 text-[0.78rem] text-text-muted">
                  Recast
                  <input
                    type="number" min={1} max={1800} value={recast} className={NUM_CLASS}
                    onChange={e => { const v = Number(e.target.value); if (Number.isFinite(v) && v > 0) update(buff.id, { recast_s: v }) }}
                    aria-label={`${buff.name} recast seconds`}
                  />
                  s
                </label>
              </div>
              {buff.censusBase && (
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-1.5 text-[0.78rem]">
                  <span className="text-text-muted">Supplied by</span>
                  {sup ? (
                    <>
                      <span className="text-gold font-medium">{sup.supplier}</span>
                      {entry && (
                        <span className="text-success">
                          {entry.name} [{entry.tier_name}]
                          {entry.procs.length > 0 && ` · proc: ${entry.procs.map(p => p.name).join(', ')}`}
                          {(entry.aa_adjustments?.length ?? 0) > 0 && ' · AA-adjusted'}
                        </span>
                      )}
                      {sup.status === 'loading' && <span className="text-text-muted">loading…</span>}
                      {sup.status === 'failed' && <span className="text-danger">lookup failed</span>}
                      {sup.status === 'no-spell' && (
                        <span className="text-warning">doesn't own {buff.censusBase}</span>
                      )}
                      <button
                        type="button"
                        onClick={() => onSetSupplier(buff.id, null)}
                        title="Clear supplier"
                        className="appearance-none border-0 bg-transparent text-text-muted hover:text-text cursor-pointer text-[0.8rem] leading-none p-0.5"
                      >
                        ✕
                      </button>
                    </>
                  ) : (
                    <CharacterSearchInput
                      compact
                      placeholder="character…"
                      exclude={[]}
                      onPick={r => onSetSupplier(buff.id, r.name)}
                    />
                  )}
                </div>
              )}
              </>
              )}
            </div>
          )
        })}
      </div>
      {Object.keys(selfUptimes).length > 0 && (
        <div className="mt-3">
          <div className="text-[0.72rem] text-text-muted uppercase tracking-wide mb-1">Own temp buffs (from rotation)</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(selfUptimes).map(([name, pct]) => (
              <span key={name} className="text-[0.78rem]">
                {name} <span className="text-text-muted">{pct.toFixed(0)}%</span>
              </span>
            ))}
          </div>
        </div>
      )}
      <p className="text-[0.72rem] text-text-muted mt-3 mb-0 leading-relaxed">
        Providers rotate the buff staggered — 2 troubadors cycling Jester's Cap sustains ~100%
        uptime. Unverified values are curated estimates; correct duration/recast here if the
        in-game numbers differ.
      </p>
    </Card>
  )
}
