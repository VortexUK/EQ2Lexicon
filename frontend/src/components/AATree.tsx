/**
 * Interactive AA tree viewer.
 * Mirrors the coordinate systems used by image/aa_tree.py,
 * all in native 640×480 space, converted to percentages for fluid layout.
 */
import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useTooltipPosition } from '../hooks/useTooltipPosition'

// ── Types ──────────────────────────────────────────────────────────────────────

export interface AANode {
  node_id:          number
  name:             string
  description:      string
  classification:   string
  xcoord:           number
  ycoord:           number
  icon_id:          number
  backdrop_id:      number
  maxtier:          number
  pointspertier:    number
  points_to_unlock: number
  title:            string
  spellcrc:         number
}

interface SpellEffect {
  description: string
  indentation: number
}

interface SpellEffectsResponse {
  effects:        SpellEffect[]
  matched_tier:   number | null
  requested_tier: number | null
}

// Module-level cache: "spellcrc:tier" → response (null = in-flight / failed)
const _spellCache = new Map<string, SpellEffectsResponse | null>()

export interface AATreeData {
  tree_id:   number
  tree_name: string
  tree_type: string
  nodes:     AANode[]
}

interface AATreeProps {
  tree:  AATreeData
  spent: Record<string, number>   // node_id (string) → tier spent
}

// ── Coordinate systems (native 640×480 space) ─────────────────────────────────

const COL_X: Record<number, number> = { 1: 86, 4: 206, 7: 327, 10: 447, 13: 567 }
const CLASS_BASE_Y = 42
const CLASS_ROW_H  = (442 - 42) / 6  // ≈ 66.67

function classCoord(x: number, y: number): [number, number] {
  return [COL_X[x] ?? 320, CLASS_BASE_Y + y * CLASS_ROW_H]
}

const SUB_ANCHOR_X  = 234
const SUB_ANCHOR_XC = 15
const SUB_STEP_X    = 155 / 12         // ≈ 12.917
const SUB_BASE_Y    = 42
const SUB_STEP_Y    = (442 - 42) / 19  // ≈ 21.05

function subclassCoord(x: number, y: number): [number, number] {
  return [
    SUB_ANCHOR_X + (x - SUB_ANCHOR_XC) * SUB_STEP_X,
    SUB_BASE_Y   + y * SUB_STEP_Y,
  ]
}

// bg_shadows.png is 632×472 native; scale to 640×480
const SHAD_NW = 632
const SHAD_NH = 472
const SHAD_AX = 40
const SHAD_SX = 13
const SHAD_ROW_Y: Record<number, number> = { 1: 59, 6: 166, 11: 273, 16: 377 }

function shadowsCoord(x: number, y: number): [number, number] {
  return [
    ((SHAD_AX + x * SHAD_SX) / SHAD_NW) * 640,
    (SHAD_ROW_Y[y] ?? 240) / SHAD_NH * 480,
  ]
}

function heroicCoord(x: number, y: number): [number, number] {
  return [65 + (x - 2) * 13, 50 + (y - 1) * 22]
}

function tradeskillCoord(x: number, y: number): [number, number] {
  return [65 + (x - 2) * 13, 60 + (y - 1) * 21]
}

function getCoord(treeType: string, x: number, y: number): [number, number] {
  switch (treeType) {
    case 'class':      return classCoord(x, y)
    case 'shadows':    return shadowsCoord(x, y)
    case 'heroic':     return heroicCoord(x, y)
    case 'tradeskill': return tradeskillCoord(x, y)
    // subclass + all unimplemented types share the subclass coordinate system
    default:           return subclassCoord(x, y)
  }
}

// ── Backgrounds ────────────────────────────────────────────────────────────────

function getBgOverlay(treeType: string): string | null {
  switch (treeType) {
    case 'class':    return '/aa-assets/bg_class.png'
    case 'subclass': return '/aa-assets/bg_subclass.png'
    case 'shadows':  return '/aa-assets/bg_shadows.png'
    default:         return null
  }
}

// Node diameter in native 640×480 space (matches NODE_R = 44 output at 2× scale → 22 native radius → 44 diameter)
const NODE_D = 44

// ── Hover tooltip ──────────────────────────────────────────────────────────────
// Exported: the compare page's AA node-diff rows reuse the exact same tooltip
// (name, rank, description, lazy-fetched spell effects) as the tree view.

export interface AANodeTooltipData {
  node: AANode
  tier: number
  mx:   number
  my:   number
}

type TooltipData = AANodeTooltipData

export function AANodeTooltip({ data }: { data: TooltipData }) {
  const { node, tier, mx, my } = data
  const spent = tier > 0

  // Lazy-fetch spell effects; re-render when they arrive.
  // Use spent tier so effect values reflect the character's actual rank;
  // fall back to tier 1 for unspent nodes so the tooltip still shows preview text.
  const effectTier = tier > 0 ? tier : 1
  const cacheKey   = node.spellcrc ? `${node.spellcrc}:${effectTier}` : ''

  const [spellResp, setSpellResp] = useState<SpellEffectsResponse | null>(
    cacheKey ? (_spellCache.get(cacheKey) ?? null) : null
  )

  useEffect(() => {
    if (!node.spellcrc) return
    if (_spellCache.has(cacheKey)) {
      setSpellResp(_spellCache.get(cacheKey) ?? null)
      return
    }
    fetch(`/api/aa/spell/${node.spellcrc}?tier=${effectTier}`)
      .then(r => r.ok ? r.json() : null)
      .then((d: SpellEffectsResponse | null) => {
        _spellCache.set(cacheKey, d)
        setSpellResp(d)
      })
      .catch(() => {
        _spellCache.set(cacheKey, null)
        setSpellResp(null)
      })
  }, [cacheKey, node.spellcrc, effectTier])

  const effects   = spellResp?.effects ?? null
  const tierMismatch = spellResp &&
    spellResp.requested_tier !== null &&
    spellResp.matched_tier   !== null &&
    spellResp.matched_tier   !== spellResp.requested_tier

  const TIP_W = 320
  const { ref, position } = useTooltipPosition({ x: mx, y: my, width: TIP_W, marginX: 14, marginY: 8 })

  return createPortal(
    <div ref={ref} style={{
      position: 'fixed', left: position.left, top: position.top,
      width: TIP_W, zIndex: 9999,
      pointerEvents: 'none', userSelect: 'none',
      fontFamily: '"Times New Roman", Times, serif',
      background: '#0a0a0e',
      border: '2px solid #c49e2c',
      boxShadow: 'inset 0 0 0 1px #364c5c, 0 8px 32px rgba(0,0,0,0.9)',
      borderRadius: 2,
      padding: '8px 10px',
      fontSize: '0.83rem',
      color: '#c7cfc7',
      lineHeight: 1.4,
    }}>
      {/* Header row: name left, rank right */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 2 }}>
        <div style={{ color: '#e6e970', fontWeight: 'bold', fontSize: '0.95rem' }}>
          {node.name}
        </div>
        {spent && (
          <div style={{ color: '#e8e8e8', fontWeight: 'bold', fontSize: '0.83rem', whiteSpace: 'nowrap', flexShrink: 0 }}>
            Rank ({tier} / {node.maxtier})
          </div>
        )}
      </div>

      {/* Second row: classification left, cost right */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        {node.classification && (
          <div style={{ color: '#e6e970', fontSize: '0.83rem' }}>
            {node.classification}
          </div>
        )}
        {spent ? (
          <div style={{ color: '#c84040', fontSize: '0.83rem', whiteSpace: 'nowrap', flexShrink: 0, marginLeft: 'auto' }}>
            {node.pointspertier} point{node.pointspertier !== 1 ? 's' : ''}
          </div>
        ) : (
          <div style={{ color: '#888', fontSize: '0.78rem', whiteSpace: 'nowrap', flexShrink: 0, marginLeft: 'auto' }}>
            {node.maxtier} tier{node.maxtier !== 1 ? 's' : ''}
            {node.points_to_unlock > 0 && ` · ${node.points_to_unlock} pts req.`}
          </div>
        )}
      </div>

      {/* Title */}
      {node.title && (
        <div style={{ color: '#e6e970', fontStyle: 'italic', fontSize: '0.78rem', marginBottom: 4 }}>
          {node.title}
        </div>
      )}

      {/* Description */}
      {node.description && (
        <div style={{ color: '#e8e8e8', fontWeight: 'bold', fontSize: '0.83rem', marginBottom: 6 }}>
          {node.description}
        </div>
      )}

      {/* Spell effects */}
      {effects === null && node.spellcrc > 0 && (
        <div style={{ borderTop: '1px solid #364c5c', paddingTop: 5, color: '#555', fontSize: '0.75rem' }}>
          Loading…
        </div>
      )}
      {effects && effects.length > 0 && (
        <div style={{ borderTop: '1px solid #364c5c', paddingTop: 5 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
            <div style={{ color: '#e6e970', fontWeight: 'bold' }}>Effect:</div>
            {tierMismatch && (
              <div style={{ color: '#888', fontSize: '0.7rem' }}>
                (rank {spellResp!.matched_tier} data)
              </div>
            )}
          </div>
          {effects.map((e, i) => (
            <div key={i} style={{
              color: '#c7cfc7',
              fontSize: '0.78rem',
              paddingLeft: `${Math.max(0, e.indentation) * 12 + 8}px`,
              lineHeight: 1.35,
            }}>
              {e.description}
            </div>
          ))}
        </div>
      )}
    </div>,
    document.body,
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export function AATree({ tree, spent }: AATreeProps) {
  const [hovered, setHovered] = useState<TooltipData | null>(null)

  const overlay = getBgOverlay(tree.tree_type)

  // As a % of the 640×480 native container
  const wPct = (NODE_D / 640) * 100   // ≈ 6.875 %
  const hPct = (NODE_D / 480) * 100   // ≈ 9.167 %

  return (
    <div style={{ position: 'relative' }}>
      {/* 640 : 480 aspect container */}
      <div style={{
        position: 'relative',
        width: '100%',
        aspectRatio: '640 / 480',
        overflow: 'hidden',
        borderRadius: 4,
        border: '1px solid #364c5c',
      }}>
        {/* Base background */}
        <img
          src="/aa-assets/background.jpg"
          alt=""
          draggable={false}
          style={{
            position: 'absolute', inset: 0,
            width: '100%', height: '100%',
            objectFit: 'cover', display: 'block',
            pointerEvents: 'none',
          }}
        />

        {/* Connector-line overlay (screen blend: dark → transparent, light → visible) */}
        {overlay && (
          <img
            src={overlay}
            alt=""
            draggable={false}
            style={{
              position: 'absolute', inset: 0,
              width: '100%', height: '100%',
              objectFit: 'fill', display: 'block',
              mixBlendMode: 'screen',
              pointerEvents: 'none',
            }}
          />
        )}

        {/* Nodes */}
        {tree.nodes.map(node => {
          const [cx, cy] = getCoord(tree.tree_type, node.xcoord, node.ycoord)
          const tier    = spent[String(node.node_id)] ?? 0
          const hasSpent = tier > 0
          const maxed   = tier >= node.maxtier

          const borderColor = maxed  ? '#22cc22'
                            : hasSpent ? '#d4a017'
                            : '#6a6050'
          const glowColor   = maxed  ? 'rgba(34,200,34,0.55)'
                            : hasSpent ? 'rgba(212,160,23,0.45)'
                            : 'none'

          return (
            <div
              key={node.node_id}
              style={{
                position: 'absolute',
                left:      `${(cx / 640) * 100}%`,
                top:       `${(cy / 480) * 100}%`,
                width:     `${wPct}%`,
                height:    `${hPct}%`,
                transform: 'translate(-50%, -50%)',
                cursor: 'default',
              }}
              onMouseEnter={e => setHovered({ node, tier, mx: e.clientX, my: e.clientY })}
              onMouseMove={e  => setHovered(h => h ? { ...h, mx: e.clientX, my: e.clientY } : null)}
              onMouseLeave={() => setHovered(null)}
              onClick={e => setHovered({ node, tier, mx: e.clientX, my: e.clientY })}
            >
              {/* Icon circle */}
              <div style={{
                width: '100%', height: '100%',
                borderRadius: '50%',
                overflow: 'hidden',
                border: `2px solid ${borderColor}`,
                boxShadow: glowColor !== 'none' ? `0 0 6px ${glowColor}` : undefined,
                opacity: hasSpent ? 1 : 0.45,
                background: '#0a0a0e',
                position: 'relative',
              }}>
                {node.icon_id > 0 && (
                  <img
                    src={`/aa-assets/icons/${node.icon_id}.png`}
                    alt={node.name}
                    draggable={false}
                    style={{ width: '100%', height: '100%', display: 'block' }}
                    onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                  />
                )}
              </div>

              {/* Tier badge — bottom-right overlap */}
              {hasSpent && (
                <div style={{
                  position: 'absolute',
                  right:  '-22%',
                  bottom: '-22%',
                  width:  '50%',
                  height: '50%',
                  borderRadius: '50%',
                  background: maxed ? '#1a6b1a' : '#6b5a00',
                  border: `1px solid ${maxed ? '#22cc22' : '#d4a017'}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#fff',
                  fontWeight: 'bold',
                  fontSize: '0.5em',
                  lineHeight: 1,
                  zIndex: 2,
                  pointerEvents: 'none',
                }}>
                  {tier}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {hovered && <AANodeTooltip data={hovered} />}
    </div>
  )
}
