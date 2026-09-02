/**
 * CharacterProgressionTab — RoK progression on the character sheet.
 *
 * Epic weapon (fabled → mythical) with chain-step progress, the T1–T4
 * raid-tier flag ladder (kill achievements), and Taking on Trakanon
 * access state with per-boss objectives. Data from
 * GET /api/character/{name}/progression (server-side census reduce,
 * SWR-cached — see backend/server/api/progression.py).
 */
import { useFetch } from '../hooks/useFetch'
import { Card, SectionLabel, Badge } from '../components/ui'
import { fmtLocalDate } from '../formatters'

// ── API types (mirrors backend/server/progression.py output) ────────────────

interface ChainProgress {
  done: boolean
  date: string | null
  steps_done: number
  steps_total: number
  current_step: number | null
  current_name: string | null
  current_stage: string | null
  started: boolean
}

interface EpicProgress {
  weapon: string
  state: 'none' | 'fabled_progress' | 'fabled' | 'mythical_progress' | 'mythical'
  fabled: ChainProgress
  mythical: ChainProgress
}

interface TierBoss {
  boss: string
  zone: string
  earned: boolean
  earned_at: number | null
}

interface TierProgress {
  earned: number
  total: number
  complete: boolean
  bosses: TierBoss[]
}

interface TrakanonProgress {
  state: 'none' | 'in_progress' | 'ready_to_turn_in' | 'completed'
  date: string | null
  killed_trakanon: boolean
  killed_trakanon_at: number | null
  bosses: { boss: string; killed: boolean }[] | null
  killed?: number
  total?: number
  stage?: string | null
}

export interface ProgressionData {
  epic: EpicProgress | null
  tiers: Record<string, TierProgress>
  trakanon: TrakanonProgress
}

interface ProgressionResponse {
  character: string
  world: string
  cls: string | null
  progression: ProgressionData
}

// ── Shared bits ──────────────────────────────────────────────────────────────

const TIER_ORDER = ['T1', 'T2', 'T3', 'T4'] as const

export const EPIC_STATE_LABEL: Record<EpicProgress['state'], string> = {
  none: 'Not started',
  fabled_progress: 'Fabled in progress',
  fabled: 'Fabled',
  mythical_progress: 'Mythical in progress',
  mythical: 'Mythical',
}

/** "Step 2/9 — Quest Name" for a mid-chain state. */
export function chainStepLabel(c: ChainProgress): string | null {
  if (c.done || !c.started || c.current_step == null) return null
  return `Step ${c.current_step}/${c.steps_total}${c.current_name ? ` — ${c.current_name}` : ''}`
}

function fmtEpoch(ts: number | null): string {
  return ts ? fmtLocalDate(ts) : ''
}

// ── Sections ─────────────────────────────────────────────────────────────────

function ChainRow({ label, chain, accent }: { label: string; chain: ChainProgress; accent: string }) {
  const step = chainStepLabel(chain)
  return (
    <div className="flex items-baseline gap-3 flex-wrap">
      <span className="text-text-muted text-[0.8rem] uppercase tracking-[0.06em] font-semibold w-20">{label}</span>
      {chain.done ? (
        <span style={{ color: accent }} className="font-semibold">
          ✓ Completed{chain.date ? ` · ${chain.date}` : ''}
        </span>
      ) : step ? (
        <span className="text-text">
          {step}
          {chain.current_stage && (
            <span className="block text-text-muted text-[0.8rem] italic mt-0.5">{chain.current_stage}</span>
          )}
        </span>
      ) : (
        <span className="text-text-muted">Not started</span>
      )}
    </div>
  )
}

function EpicCard({ epic }: { epic: EpicProgress }) {
  const badge =
    epic.state === 'mythical' ? <Badge variant="gold">Mythical</Badge>
    : epic.state === 'fabled' || epic.state === 'mythical_progress' ? <Badge variant="success">Fabled</Badge>
    : epic.state === 'fabled_progress' ? <Badge variant="warning">In progress</Badge>
    : <Badge variant="muted">Not started</Badge>
  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <SectionLabel style={{ marginBottom: 0 }}>Epic Weapon</SectionLabel>
        {badge}
      </div>
      <div className="font-heading text-[1.15rem]" style={{ color: 'var(--color-rarity-fabled, var(--gold))' }}>
        {epic.weapon}
      </div>
      <div className="space-y-2">
        <ChainRow label="Fabled" chain={epic.fabled} accent="var(--color-success)" />
        <ChainRow label="Mythical" chain={epic.mythical} accent="var(--gold)" />
      </div>
    </Card>
  )
}

function TierCard({ tiers }: { tiers: Record<string, TierProgress> }) {
  return (
    <Card className="space-y-3">
      <SectionLabel>Raid Tiers</SectionLabel>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
        {TIER_ORDER.map(t => {
          const tier = tiers[t]
          if (!tier) return null
          return (
            <div key={t} className="border border-border rounded-md p-3 bg-surface/50">
              <div className="flex items-baseline justify-between mb-2">
                <span className="font-heading text-gold text-[1rem]">{t}</span>
                <span className={tier.complete ? 'text-success font-semibold' : 'text-text-muted'}>
                  {tier.complete ? '✓' : `${tier.earned}/${tier.total}`}
                </span>
              </div>
              <ul className="space-y-1">
                {tier.bosses.map(b => (
                  <li key={b.boss} className="text-[0.8rem] leading-snug flex gap-1.5" title={b.zone + (b.earned_at ? ` · ${fmtEpoch(b.earned_at)}` : '')}>
                    <span className={b.earned ? 'text-success' : 'text-text-muted'}>{b.earned ? '✓' : '·'}</span>
                    <span className={b.earned ? 'text-text' : 'text-text-muted'}>{b.boss}</span>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function TrakanonCard({ t }: { t: TrakanonProgress }) {
  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <SectionLabel style={{ marginBottom: 0 }}>Trakanon Access</SectionLabel>
        {t.state === 'completed' ? <Badge variant="success">Access granted</Badge>
          : t.state === 'ready_to_turn_in' ? <Badge variant="warning">Ready to turn in</Badge>
          : t.state === 'in_progress' ? <Badge variant="info">{`${t.killed ?? 0}/${t.total ?? 12} bosses`}</Badge>
          : <Badge variant="muted">Not started</Badge>}
        {t.killed_trakanon && (
          <Badge variant="gold">Trakanon slain{t.killed_trakanon_at ? ` · ${fmtEpoch(t.killed_trakanon_at)}` : ''}</Badge>
        )}
      </div>
      {t.state === 'completed' && t.date && (
        <p className="text-text-muted text-[0.85rem]">Taking on Trakanon completed {t.date}.</p>
      )}
      {t.bosses && t.state !== 'completed' && (
        <ul className="grid gap-x-6 gap-y-1" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
          {t.bosses.map(b => (
            <li key={b.boss} className="text-[0.82rem] flex gap-1.5">
              <span className={b.killed ? 'text-success' : 'text-text-muted'}>{b.killed ? '✓' : '·'}</span>
              <span className={b.killed ? 'text-text' : 'text-text-muted'}>{b.boss}</span>
            </li>
          ))}
        </ul>
      )}
      {t.state === 'none' && !t.killed_trakanon && (
        <p className="text-text-muted text-[0.85rem]">
          The Veeshan's Peak Trakanon fight is gated by the <em>Taking on Trakanon</em> quest.
        </p>
      )}
    </Card>
  )
}

// ── Tab ──────────────────────────────────────────────────────────────────────

export function ProgressionTab({ charName }: { charName: string }) {
  const { data, loading, error } = useFetch<ProgressionResponse>(
    `/api/character/${encodeURIComponent(charName)}/progression`,
  )

  if (loading) return <p className="text-text-muted py-6">Loading progression…</p>
  if (error || !data) return <p className="text-text-muted py-6">Progression unavailable (Census may be down) — try again shortly.</p>

  const p = data.progression
  return (
    <div className="space-y-4 max-w-[900px]">
      {p.epic && <EpicCard epic={p.epic} />}
      <TierCard tiers={p.tiers} />
      <TrakanonCard t={p.trakanon} />
    </div>
  )
}
