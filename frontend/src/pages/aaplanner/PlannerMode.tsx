/**
 * PlannerMode — the interactive AA planner inside the character AA tab.
 *
 * Left-click a node to spend a rank, right-click to refund one; every edit
 * goes through the engine (engine.ts) so a plan can never go illegal —
 * including the no-stranding removal rule. Plans save privately per user
 * (20 per character) and every plan carries a read-only share link.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AATree, type AANode, type AATreeData } from '../../components/AATree'
import { Button, SectionLabel } from '../../components/ui'
import { TabButton } from '../../components/ui/TabButton'
import { handle } from '../../lib/api'
import { toErrorMessage } from '../../lib/errors'
import {
  type AAConfig,
  type CharAAsResponse,
  TREE_TYPE_LABEL,
  filterTreeForEra,
  getAAConfigFor,
  getTreeData,
} from '../CharacterAAsTab'
import { aaFileName, buildAAFileXml, downloadAAFile } from './aaFile'
import {
  type PlanAllocations,
  type PlannerCtx,
  TRADESKILL_TREE_TYPES,
  TREE_POINT_CAP,
  addRank,
  canAddRank,
  canRemoveRank,
  nodeUnlocked,
  planFromSpent,
  poolPoints,
  removeRank,
  treePoints,
  validatePlan,
} from './engine'

/** Eras the planner can target. Later expansions come once their row
 * curation lands in aa_limits.json (currently verified up to DoV). */
export const PLANNER_ERAS = [
  'Kingdom of Sky',
  'Echoes of Faydwer',
  'Rise of Kunark',
  'The Shadow Odyssey',
  "Sentinel's Fate",
  'Destiny of Velious',
] as const

export interface PlanSummary {
  id: number
  name: string
  xpac: string | null
  share_slug: string
  created_at: number
  updated_at: number
}

interface PlanDetail extends PlanSummary {
  character_name: string
  world: string
  allocations: PlanAllocations
  is_mine: boolean
}

interface PlannerModeProps {
  charName: string
  cls?: string | null
  charAAs: CharAAsResponse
  config: AAConfig
  treeData: Map<number, AATreeData>
}

interface PlanTreeEntry {
  tree_id: number
  tree_type: string
  tree_name: string
}

// Subclass → plannable trees (class/subclass/shadows/heroic), cached per
// class. Lets the planner offer trees the character's Census record doesn't
// carry yet — e.g. the shadows tree when era-planning TSO on an EoF server.
const _planTreesCache = new Map<string, Promise<PlanTreeEntry[]>>()

function getPlanTrees(cls: string): Promise<PlanTreeEntry[]> {
  let p = _planTreesCache.get(cls)
  if (!p) {
    p = fetch(`/api/aa/plan-trees?cls=${encodeURIComponent(cls)}`, { credentials: 'include' })
      .then(r => (r.ok ? (r.json() as Promise<PlanTreeEntry[]>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .catch(err => {
        _planTreesCache.delete(cls) // allow retry
        throw err
      })
    _planTreesCache.set(cls, p)
  }
  return p
}

export function PlannerMode({ charName, cls, charAAs, config, treeData }: PlannerModeProps) {
  // Era selection: '' = the server's era (the config prop); a PLANNER_ERAS
  // value plans under that expansion's cap/trees/rows instead.
  const [era, setEra] = useState('')
  const [eraConfig, setEraConfig] = useState<AAConfig | null>(null)
  const eraRef = useRef('')
  const activeConfig = era && eraConfig ? eraConfig : config

  const selectEra = (value: string) => {
    setEra(value)
    eraRef.current = value
    setError(null)
    if (!value) {
      setEraConfig(null)
      return
    }
    getAAConfigFor(value)
      .then(cfg => {
        if (eraRef.current === value) setEraConfig(cfg)
      })
      .catch(err => {
        if (eraRef.current === value) setError(toErrorMessage(err))
      })
  }

  // The full plannable tree pool: the class resolver's trees (class,
  // subclass, shadows, heroic — including ones the character's Census
  // record doesn't carry yet) plus any extras the character has (tradeskill).
  // Falls back to the character's own trees while loading / on resolver error.
  const [treePool, setTreePool] = useState<AATreeData[] | null>(null)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const wanted: { tree_id: number }[] = []
      const seen = new Set<number>()
      if (cls) {
        try {
          for (const entry of await getPlanTrees(cls)) {
            wanted.push(entry)
            seen.add(entry.tree_id)
          }
        } catch {
          /* resolver is an enhancement — the character's own trees still plan */
        }
      }
      for (const ct of charAAs.trees) {
        if (!seen.has(ct.tree_id)) {
          wanted.push(ct)
          seen.add(ct.tree_id)
        }
      }
      const datas = await Promise.all(wanted.map(w => treeData.get(w.tree_id) ?? getTreeData(w.tree_id)))
      if (!cancelled) setTreePool(datas.filter((d): d is AATreeData => d !== null))
    })()
    return () => {
      cancelled = true
    }
  }, [cls, charAAs, treeData])

  // Era-filtered plannable trees: only the era-unlocked tree types, each
  // with off-era rows removed.
  const trees = useMemo(() => {
    const source =
      treePool ??
      charAAs.trees.map(ct => treeData.get(ct.tree_id)).filter((td): td is AATreeData => td !== undefined)
    const unlocked = new Set(activeConfig.unlocked_tree_types)
    return source
      .filter(td => unlocked.size === 0 || unlocked.has(td.tree_type))
      .map(td => filterTreeForEra(td, td.tree_type, activeConfig))
  }, [treePool, charAAs, treeData, activeConfig])
  const ctx: PlannerCtx = useMemo(
    () => ({ trees, aaCap: activeConfig.aa_cap, tradeskillCap: activeConfig.tradeskill_aa_cap }),
    [trees, activeConfig],
  )

  const [plan, setPlan] = useState<PlanAllocations>(() => planFromSpent(charAAs.trees))
  const [planName, setPlanName] = useState('New plan')
  const [activePlanId, setActivePlanId] = useState<number | null>(null)
  const [shareSlug, setShareSlug] = useState<string | null>(null)
  const [savedPlans, setSavedPlans] = useState<PlanSummary[]>([])
  const [selectedTreeId, setSelectedTreeId] = useState<number | null>(trees[0]?.tree_id ?? null)
  const [dirty, setDirty] = useState(false)
  const [notice, setNotice] = useState<string | null>(null) // rule feedback / save status
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const listUrl = `/api/aa/plans?character=${encodeURIComponent(charName)}`

  const refreshSaved = useCallback(async () => {
    try {
      setSavedPlans(await handle<PlanSummary[]>(await fetch(listUrl, { credentials: 'include' })))
    } catch {
      /* list is a convenience — planner still works without it */
    }
  }, [listUrl])

  useEffect(() => {
    refreshSaved()
  }, [refreshSaved])

  // Transient rule feedback (why a click was refused).
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 3500)
    return () => clearTimeout(t)
  }, [notice])

  const activeTree = trees.find(t => t.tree_id === selectedTreeId) ?? trees[0]

  const onAdd = (node: AANode) => {
    if (!activeTree) return
    const verdict = canAddRank(ctx, plan, activeTree, node)
    if (!verdict.ok) {
      setNotice(verdict.reason ?? 'Locked')
      return
    }
    setPlan(p => addRank(p, activeTree, node))
    setDirty(true)
  }

  const onRemove = (node: AANode) => {
    if (!activeTree) return
    const verdict = canRemoveRank(ctx, plan, activeTree, node)
    if (!verdict.ok) {
      setNotice(verdict.reason ?? 'Cannot remove')
      return
    }
    setPlan(p => removeRank(p, activeTree, node))
    setDirty(true)
  }

  const loadPlan = async (planId: number) => {
    setBusy(true)
    setError(null)
    try {
      const detail = await handle<PlanDetail>(await fetch(`/api/aa/plans/${planId}`, { credentials: 'include' }))
      setPlan(detail.allocations)
      setPlanName(detail.name)
      setActivePlanId(detail.id)
      setShareSlug(detail.share_slug)
      setDirty(false)
      // Re-open the plan under the era it was saved for.
      if (detail.xpac && (PLANNER_ERAS as readonly string[]).includes(detail.xpac) && detail.xpac !== era) {
        selectEra(detail.xpac)
      }
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const startFrom = (source: 'current' | 'empty') => {
    setPlan(source === 'current' ? planFromSpent(charAAs.trees) : {})
    setPlanName('New plan')
    setActivePlanId(null)
    setShareSlug(null)
    setDirty(true)
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const body = JSON.stringify({
        character_name: charName,
        name: planName.trim() || 'New plan',
        xpac: era || config.xpac || null,
        allocations: plan,
      })
      const url = activePlanId != null ? `/api/aa/plans/${activePlanId}` : '/api/aa/plans'
      const detail = await handle<PlanDetail>(
        await fetch(url, {
          method: activePlanId != null ? 'PUT' : 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body,
        }),
      )
      setActivePlanId(detail.id)
      setShareSlug(detail.share_slug)
      setDirty(false)
      setNotice('Saved')
      await refreshSaved()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const deletePlan = async () => {
    if (activePlanId == null) return
    if (!confirm(`Delete plan "${planName}"?`)) return
    setBusy(true)
    setError(null)
    try {
      await handle(await fetch(`/api/aa/plans/${activePlanId}`, { method: 'DELETE', credentials: 'include' }))
      startFrom('current')
      setDirty(false)
      await refreshSaved()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const downloadSpec = () => {
    const { xml, unplacedCount } = buildAAFileXml(ctx, plan)
    downloadAAFile(aaFileName(charName, planName), xml)
    setNotice(
      unplacedCount > 0
        ? `Downloaded — ${unplacedCount} point${unplacedCount !== 1 ? 's' : ''} skipped (not legal in this era)`
        : 'Spec downloaded — load it from the in-game AA window',
    )
  }

  const copyShareLink = async () => {
    if (!shareSlug) return
    const link = `${window.location.origin}/aa-plan/${shareSlug}`
    try {
      await navigator.clipboard.writeText(link)
      setNotice('Share link copied')
    } catch {
      setNotice(link) // clipboard blocked — show it instead
    }
  }

  const adventureSpent = poolPoints(ctx, plan, 'adventure')
  const tradeskillSpent = poolPoints(ctx, plan, 'tradeskill')
  const hasTradeskill = trees.some(t => TRADESKILL_TREE_TYPES.has(t.tree_type))
  const violations = useMemo(() => validatePlan(ctx, plan), [ctx, plan])

  if (trees.length === 0) {
    return <p className="mt-4 text-text-muted">No plannable tree data for this character.</p>
  }

  return (
    <div className="mt-4 flex flex-col md:flex-row gap-6 items-start">
      {/* ── Left: plan management + budgets ── */}
      <div className="w-full md:w-[240px] md:shrink-0">
        <SectionLabel className="mb-1">Era</SectionLabel>
        <select
          value={era}
          onChange={e => selectEra(e.target.value)}
          aria-label="Era"
          className="w-full bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem] mb-3 cursor-pointer"
        >
          <option value="">Server era{config.xpac ? ` (${config.xpac})` : ''}</option>
          {PLANNER_ERAS.map(e => (
            <option key={e} value={e}>{e}</option>
          ))}
        </select>

        <SectionLabel className="mb-1">Plan</SectionLabel>
        <input
          type="text"
          value={planName}
          onChange={e => {
            setPlanName(e.target.value)
            setDirty(true)
          }}
          maxLength={60}
          aria-label="Plan name"
          className="w-full bg-surface border border-border rounded-sm px-2 py-1 text-[0.85rem] mb-1.5"
        />
        <div className="flex flex-wrap gap-1.5 mb-2">
          <Button variant="primary" size="sm" onClick={save} disabled={busy || !dirty}>
            {activePlanId != null ? 'Save' : 'Save as new'}
          </Button>
          {shareSlug && (
            <Button variant="secondary" size="sm" onClick={copyShareLink} disabled={busy}>
              Share link
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            onClick={downloadSpec}
            disabled={busy || adventureSpent + tradeskillSpent === 0}
            title="Download this plan as an in-game .aa spec file (loadable from the AA window)"
          >
            Download .aa
          </Button>
          {activePlanId != null && (
            <Button variant="danger" size="sm" onClick={deletePlan} disabled={busy}>
              Delete
            </Button>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 mb-3">
          <Button variant="ghost" size="sm" onClick={() => startFrom('current')} disabled={busy}>
            Reset to current
          </Button>
          <Button variant="ghost" size="sm" onClick={() => startFrom('empty')} disabled={busy}>
            Start empty
          </Button>
        </div>

        {savedPlans.length > 0 && (
          <div className="mb-3">
            <SectionLabel variant="muted" className="mb-1">My plans</SectionLabel>
            <div className="flex flex-col gap-[2px]">
              {savedPlans.map(p => (
                <button
                  key={p.id}
                  onClick={() => loadPlan(p.id)}
                  disabled={busy}
                  className="appearance-none text-left border rounded-sm cursor-pointer text-[0.78rem] py-1 px-2 overflow-hidden text-ellipsis whitespace-nowrap"
                  style={{
                    background: p.id === activePlanId ? 'var(--accent)' : 'var(--surface)',
                    borderColor: p.id === activePlanId ? 'var(--accent)' : 'var(--border)',
                    color: p.id === activePlanId ? '#000' : 'var(--text)',
                    fontWeight: p.id === activePlanId ? 600 : 400,
                  }}
                  title={p.name}
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>
        )}

        <SectionLabel variant="muted" className="mb-1">Budget</SectionLabel>
        <div className="text-[0.82rem] mb-1">
          <span className="text-text-muted">Adventure:</span>{' '}
          <span className="font-semibold tabular-nums">{adventureSpent}</span>
          {activeConfig.aa_cap > 0 && <span className="text-text-muted"> / {activeConfig.aa_cap}</span>}
        </div>
        {hasTradeskill && (
          <div className="text-[0.82rem] mb-1">
            <span className="text-text-muted">Tradeskill:</span>{' '}
            <span className="font-semibold tabular-nums">{tradeskillSpent}</span>
            {activeConfig.tradeskill_aa_cap > 0 && (
              <span className="text-text-muted"> / {activeConfig.tradeskill_aa_cap}</span>
            )}
          </div>
        )}
        <div className="text-[0.7rem] text-text-muted mt-2 leading-[1.5]">
          Click a skill to spend a rank; right-click to refund. Greyed skills are locked until their requirements
          are met.
        </div>
      </div>

      {/* ── Right: tree tabs + interactive tree ── */}
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap gap-[2px] border-b border-border mb-3">
          {trees.map(t => {
            const spent = treePoints(t, plan[String(t.tree_id)])
            return (
              <TabButton
                key={t.tree_id}
                active={t.tree_id === activeTree?.tree_id}
                onClick={() => setSelectedTreeId(t.tree_id)}
                title={`${TREE_TYPE_LABEL[t.tree_type] ?? t.tree_type} · ${spent}/${TREE_POINT_CAP} pts`}
                className="whitespace-nowrap"
              >
                {t.tree_name} <span className="text-text-muted text-[0.72rem]">({spent})</span>
              </TabButton>
            )
          })}
        </div>

        {(notice || error) && (
          <div className={`mb-2 text-[0.8rem] ${error ? 'text-danger' : 'text-gold'}`} role="status">
            {error ?? notice}
          </div>
        )}
        {violations.length > 0 && (
          <div className="mb-2 text-[0.78rem] text-warning">
            This plan has {violations.length} rule violation{violations.length !== 1 ? 's' : ''} (
            {violations[0].nodeName}: {violations[0].reason}
            {violations.length > 1 ? ', …' : ''}) — likely saved under different era rules.
          </div>
        )}

        {activeTree && (
          <>
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="text-[0.72rem] uppercase tracking-[0.06em] text-gold">
                {TREE_TYPE_LABEL[activeTree.tree_type] ?? activeTree.tree_type}
              </span>
              <span className="text-[0.78rem] text-text-muted tabular-nums">
                {treePoints(activeTree, plan[String(activeTree.tree_id)])} / {TREE_POINT_CAP} in tree
              </span>
            </div>
            <div className="overflow-x-auto md:overflow-visible">
              <div className="min-w-[420px] md:min-w-0 md:w-[60%]">
                <AATree
                  tree={activeTree}
                  spent={plan[String(activeTree.tree_id)] ?? {}}
                  onAddRank={onAdd}
                  onRemoveRank={onRemove}
                  locked={node => !nodeUnlocked(ctx, plan, activeTree, node)}
                  lockReason={node => {
                    const verdict = canAddRank(ctx, plan, activeTree, node)
                    return verdict.ok ? null : (verdict.reason ?? null)
                  }}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
