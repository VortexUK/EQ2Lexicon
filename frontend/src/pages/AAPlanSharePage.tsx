/**
 * AAPlanSharePage — read-only view of a shared AA plan (/aa-plan/:slug).
 *
 * Renders the plan's trees with its planned ranks using the same AATree
 * replica as the character page. Owners get a hint that editing happens on
 * the character's AA tab.
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { AATree, type AATreeData } from '../components/AATree'
import Breadcrumb from '../components/Breadcrumb'
import { Button, Card, SectionLabel } from '../components/ui'
import { TabButton } from '../components/ui/TabButton'
import { aaFileName, buildAAFileXml, downloadAAFile } from './aaplanner/aaFile'
import { useFetch } from '../hooks/useFetch'
import { fmtLocalDate } from '../formatters'
import {
  type AAConfig,
  TREE_TYPE_LABEL,
  filterTreeForEra,
  getAAConfig,
  getAAConfigFor,
  getTreeData,
} from './CharacterAAsTab'
import type { PlanAllocations } from './aaplanner/engine'
import { TRADESKILL_TREE_TYPES, TREE_POINT_CAP, treePoints } from './aaplanner/engine'

interface SharedPlan {
  id: number
  name: string
  xpac: string | null
  character_name: string
  world: string
  allocations: PlanAllocations
  is_mine: boolean
  updated_at: number
  share_slug: string
}

export default function AAPlanSharePage() {
  const { slug } = useParams<{ slug: string }>()
  const { data: plan, loading, error, statusCode } = useFetch<SharedPlan>(
    slug ? `/api/aa/plan/${encodeURIComponent(slug)}` : null,
  )

  const [config, setConfig] = useState<AAConfig | null>(null)
  const [trees, setTrees] = useState<AATreeData[]>([])
  const [selectedTreeId, setSelectedTreeId] = useState<number | null>(null)

  useEffect(() => {
    if (!plan) return
    let cancelled = false
    const treeIds = Object.keys(plan.allocations).map(Number)
    // Render under the era the plan was saved for; unknown era → server config.
    const configPromise = plan.xpac ? getAAConfigFor(plan.xpac).catch(() => getAAConfig()) : getAAConfig()
    Promise.all([configPromise, Promise.all(treeIds.map(getTreeData))]).then(([cfg, tds]) => {
      if (cancelled) return
      const loaded = tds.filter((td): td is AATreeData => td !== null)
      const filtered = loaded.map(td => filterTreeForEra(td, td.tree_type, cfg))
      setConfig(cfg)
      setTrees(filtered)
      setSelectedTreeId(prev => prev ?? filtered[0]?.tree_id ?? null)
    })
    return () => {
      cancelled = true
    }
  }, [plan])

  const activeTree = trees.find(t => t.tree_id === selectedTreeId) ?? trees[0]
  // Adventure vs tradeskill are separate pools with separate caps — never
  // count tradeskill spend against the adventure xpac cap.
  const pointsIn = (predicate: (t: AATreeData) => boolean) =>
    trees.filter(predicate).reduce((sum, t) => sum + treePoints(t, plan?.allocations[String(t.tree_id)]), 0)
  const adventurePlanned = pointsIn(t => !TRADESKILL_TREE_TYPES.has(t.tree_type))
  const tradeskillPlanned = pointsIn(t => TRADESKILL_TREE_TYPES.has(t.tree_type))

  return (
    <main className="max-w-[1100px] my-8 mx-auto px-4">
      <Breadcrumb items={[{ label: 'Characters', to: '/characters' }, { label: 'Shared AA plan' }]} />

      {loading && <p className="mt-6 text-text-muted">Loading plan…</p>}
      {error && (
        <p className="mt-6 text-text-muted">
          {statusCode === 404 ? 'This plan no longer exists (or the link is wrong).' : `Error: ${error}`}
        </p>
      )}

      {plan && (
        <>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mt-2 mb-1">
            <h1 className="font-heading text-[1.5rem] font-bold text-gold m-0">{plan.name}</h1>
            <span className="text-[0.85rem] text-text-muted">
              AA plan for{' '}
              <Link to={`/character/${encodeURIComponent(plan.character_name)}`} className="text-gold-dim">
                {plan.character_name}
              </Link>{' '}
              · {plan.world}
              {plan.xpac ? ` · ${plan.xpac}` : ''} · updated {fmtLocalDate(plan.updated_at)}
            </span>
          </div>
          <p className="text-[0.78rem] text-text-muted mb-3">
            {adventurePlanned.toLocaleString()} points planned
            {config && config.aa_cap > 0 ? ` (cap ${config.aa_cap})` : ''}
            {tradeskillPlanned > 0 &&
              ` · ${tradeskillPlanned.toLocaleString()} tradeskill${
                config && config.tradeskill_aa_cap > 0 ? ` (cap ${config.tradeskill_aa_cap})` : ''
              }`}
            .{plan.is_mine ? ' This is your plan — edit it from the character page’s AA tab.' : ' Read-only.'}
          </p>
          {trees.length > 0 && config && (
            <div className="mb-4">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  const ctx = { trees, aaCap: config.aa_cap, tradeskillCap: config.tradeskill_aa_cap }
                  const { xml } = buildAAFileXml(ctx, plan.allocations)
                  downloadAAFile(aaFileName(plan.character_name, plan.name), xml)
                }}
                title="Download this plan as an in-game .aa spec file (loadable from the AA window)"
              >
                Download .aa
              </Button>
            </div>
          )}

          {trees.length === 0 && !loading && (
            <Card className="rounded-sm p-4 text-text-muted text-[0.85rem]">Tree data unavailable.</Card>
          )}

          {trees.length > 0 && (
            <>
              <div className="flex flex-wrap gap-[2px] border-b border-border mb-3">
                {trees.map(t => {
                  const spent = treePoints(t, plan.allocations[String(t.tree_id)])
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

              {activeTree && (
                <>
                  <SectionLabel variant="muted" className="mb-1.5">
                    {TREE_TYPE_LABEL[activeTree.tree_type] ?? activeTree.tree_type}
                  </SectionLabel>
                  <div className="overflow-x-auto md:overflow-visible">
                    <div className="min-w-[420px] md:min-w-0 md:w-[60%]">
                      <AATree tree={activeTree} spent={plan.allocations[String(activeTree.tree_id)] ?? {}} />
                    </div>
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </main>
  )
}
