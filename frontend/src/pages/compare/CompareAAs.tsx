import { useEffect, useMemo, useState } from 'react'
import { Badge, Card } from '../../components/ui'
import Caret from '../../components/Caret'
import { AANodeTooltip, type AANodeTooltipData } from '../../components/AATree'
import { useClasses } from '../../useClasses'
import type { Character } from '../characterSheet'
import { loadAAData, TREE_TYPE_LABEL, type AACacheEntry } from '../CharacterAAsTab'
import { partitionAASpend } from '../aaSpend'
import { alignTrees, diffTreeNodes, sameSubclass, type TreeSummaryDiff } from './diff'
import DeltaChip from './DeltaChip'

type AAState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; a: AACacheEntry; b: AACacheEntry }

function RankCell({ rank, maxtier, other }: { rank: number; maxtier: number; other: number }) {
  if (rank === 0) return <span className="text-text-muted opacity-50">—</span>
  const higher = rank > other
  return (
    <span
      className="tabular-nums font-medium"
      style={higher ? { color: 'var(--color-success)' } : undefined}
    >
      {rank}{maxtier > 0 ? `/${maxtier}` : ''}
    </span>
  )
}

function TreeRow({ summary, entryA, entryB, nameA, nameB }: {
  summary: TreeSummaryDiff
  entryA: AACacheEntry
  entryB: AACacheEntry
  nameA: string
  nameB: string
}) {
  const [open, setOpen] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [hoverTip, setHoverTip] = useState<AANodeTooltipData | null>(null)

  const spentA = entryA.charAAs.trees.find(t => t.tree_id === summary.tree_id)?.spent
  const spentB = entryB.charAAs.trees.find(t => t.tree_id === summary.tree_id)?.spent
  // Tree metadata: either character's cached copy (same tree_id → same JSON).
  const treeMeta = entryA.treeData.get(summary.tree_id) ?? entryB.treeData.get(summary.tree_id) ?? null

  // Computed once per (spend, spend, meta) — the badge needs `differing` even
  // collapsed, so there's no saving in deferring the row diff to expand time.
  const nodeDiff = useMemo(() => diffTreeNodes(spentA, spentB, treeMeta), [spentA, spentB, treeMeta])
  const differing = nodeDiff.differing
  const shownRows = open ? (showAll ? nodeDiff.rows : nodeDiff.rows.filter(r => r.delta !== 0)) : []

  return (
    <Card className="rounded-sm px-0 py-0 mb-2 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="appearance-none border-0 bg-transparent w-full flex items-center gap-2.5 px-3 py-2 cursor-pointer text-left"
      >
        <Caret open={open} />
        <span className="text-[0.9rem] font-medium">{summary.tree_name}</span>
        <Badge variant="muted">{TREE_TYPE_LABEL[summary.tree_type] ?? summary.tree_type}</Badge>
        {summary.onlyOn && (
          <span className="text-[0.7rem] text-text-muted italic">
            not unlocked for {summary.onlyOn === 'a' ? nameB : nameA}
          </span>
        )}
        <span className="ml-auto flex items-baseline gap-3 text-[0.85rem] tabular-nums">
          <span>{summary.spentA}</span>
          <span className="text-text-muted">vs</span>
          <span>{summary.spentB}</span>
          <DeltaChip delta={summary.delta} fmt="int" />
        </span>
        <Badge variant={differing > 0 ? 'warning' : 'muted'}>
          {differing > 0 ? `${differing} node${differing === 1 ? '' : 's'} differ` : 'identical'}
        </Badge>
      </button>

      {open && nodeDiff && (
        <div className="border-t border-border px-3 py-2">
          {treeMeta === null && (
            <p className="text-[0.78rem] text-text-muted italic mb-2">
              Tree data unavailable (tree #{summary.tree_id}) — showing node ids.
            </p>
          )}
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[0.7rem] uppercase tracking-[0.08em] text-text-muted">
              {showAll ? 'All spent nodes' : 'Differing nodes'}
            </span>
            <label className="flex items-center gap-1.5 text-[0.72rem] text-text-muted cursor-pointer select-none">
              <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
              show all spent nodes
            </label>
          </div>
          {shownRows.length === 0 && (
            <p className="text-[0.8rem] text-text-muted">No differing nodes in this tree.</p>
          )}
          {shownRows.length > 0 && (
            <div className="grid grid-cols-[24px_1fr_90px_90px_64px] gap-2 items-baseline pb-1 mb-0.5 border-b border-border text-[0.68rem] uppercase tracking-[0.08em] text-text-muted">
              <span />
              <span>Node</span>
              <span className="text-right truncate" title={nameA}>{nameA}</span>
              <span className="text-right truncate" title={nameB}>{nameB}</span>
              <span className="text-right">Δ</span>
            </div>
          )}
          {shownRows.map(row => {
            const meta = treeMeta?.nodes.find(n => n.node_id === row.node_id)
            return (
              <div
                key={row.node_id}
                className="grid grid-cols-[24px_1fr_90px_90px_64px] gap-2 items-center py-[3px] border-b border-border last:border-b-0 text-[0.82rem]"
                onMouseEnter={e => {
                  if (meta) setHoverTip({ node: meta, tier: Math.max(row.rankA, row.rankB), mx: e.clientX, my: e.clientY })
                }}
                onMouseLeave={() => setHoverTip(null)}
              >
                {row.icon_id != null
                  ? <img src={`/aa-assets/icons/${row.icon_id}.png`} alt="" className="w-6 h-6 rounded-sm" />
                  : <span className="w-6 h-6 inline-block rounded-sm bg-surface-raised" />}
                <span className="truncate">{row.name}</span>
                <span className="text-right"><RankCell rank={row.rankA} maxtier={row.maxtier} other={row.rankB} /></span>
                <span className="text-right"><RankCell rank={row.rankB} maxtier={row.maxtier} other={row.rankA} /></span>
                <span className="text-right"><DeltaChip delta={row.delta} fmt="int" /></span>
              </div>
            )
          })}
          {hoverTip && <AANodeTooltip data={hoverTip} />}
        </div>
      )}
    </Card>
  )
}

/**
 * AA comparison — only meaningful when both characters share a subclass (AA
 * trees are class-specific), so the gate lives here and NOTHING is fetched
 * when it fails. Tree summaries with deltas; each expands to a node diff.
 */
export default function CompareAAs({ charA, charB }: { charA: Character; charB: Character }) {
  const { colourFor } = useClasses()
  const gatePassed = sameSubclass(charA, charB)
  const [state, setState] = useState<AAState>({ status: 'loading' })

  useEffect(() => {
    if (!gatePassed) return
    let cancelled = false
    // Reset on pair change (e.g. swap-in of a new same-subclass character) so
    // the previous pair's diffs never show against the new names.
    setState({ status: 'loading' })
    Promise.all([loadAAData(charA.name), loadAAData(charB.name)])
      .then(([a, b]) => { if (!cancelled) setState({ status: 'ok', a, b }) })
      .catch(err => { if (!cancelled) setState({ status: 'error', message: String(err) }) })
    return () => { cancelled = true }
  }, [gatePassed, charA.name, charB.name])

  if (!gatePassed) {
    return (
      <Card className="rounded-sm px-4 py-3">
        <p className="text-[0.88rem] text-text-muted m-0">
          AA comparison is only available when both characters are the same subclass —{' '}
          <strong>{charA.cls ?? 'unknown class'}</strong> and <strong>{charB.cls ?? 'unknown class'}</strong>{' '}
          use different AA trees, so a node-by-node comparison isn't meaningful.
        </p>
      </Card>
    )
  }

  if (state.status === 'loading') return <p className="mt-4 text-text-muted">Loading AA data…</p>
  if (state.status === 'error') return <p className="mt-4 text-danger">Error loading AA data: {state.message}</p>

  const { a, b } = state
  const partsA = partitionAASpend(a.charAAs.trees)
  const partsB = partitionAASpend(b.charAAs.trees)
  const summaries = alignTrees(a.charAAs.trees, b.charAAs.trees)

  return (
    <div>
      {/* Totals header — values carry the character's name so it's always
          clear which side is which (both share a class colour here). */}
      <Card className="rounded-sm px-4 py-2.5 mb-4 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-[0.85rem]">
        <span>
          <span className="text-text-muted">Adventure AA:</span>{' '}
          <span style={{ color: colourFor(charA.cls, 'var(--text)') }}>
            {charA.name} <span className="font-semibold tabular-nums">{partsA.adventure}</span>
          </span>
          <span className="text-text-muted"> vs </span>
          <span style={{ color: colourFor(charB.cls, 'var(--text)') }}>
            {charB.name} <span className="font-semibold tabular-nums">{partsB.adventure}</span>
          </span>{' '}
          <DeltaChip delta={partsB.adventure - partsA.adventure} fmt="int" />
        </span>
        {(partsA.tradeskill > 0 || partsB.tradeskill > 0) && (
          <span>
            <span className="text-text-muted">Tradeskill AA:</span>{' '}
            <span className="text-text-muted">{charA.name}</span>{' '}
            <span className="font-semibold tabular-nums">{partsA.tradeskill}</span>
            <span className="text-text-muted"> vs {charB.name}</span>{' '}
            <span className="font-semibold tabular-nums">{partsB.tradeskill}</span>{' '}
            <DeltaChip delta={partsB.tradeskill - partsA.tradeskill} fmt="int" />
          </span>
        )}
      </Card>

      {summaries.map(s => (
        <TreeRow key={s.tree_id} summary={s} entryA={a} entryB={b} nameA={charA.name} nameB={charB.name} />
      ))}
    </div>
  )
}
