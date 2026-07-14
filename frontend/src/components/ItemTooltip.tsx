/**
 * HTML tooltip mirroring image/tooltip.py.
 * Colours, layout order, and section logic kept in sync with that file.
 */
import { type ReactNode, useCallback, useEffect, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { qualityStyle, type QualityStyle } from '../rarityColors'
import { useTooltipPosition } from '../hooks/useTooltipPosition'

// Re-exported so existing importers (SpellScrollTooltip) keep working while the
// canonical definition lives in rarityColors.
export { qualityStyle }
export type { QualityStyle }

// ── Types ─────────────────────────────────────────────────────────────────────

interface ItemStat {
  display_name: string
  value: number
  stat_group: string
}

interface EffectLine {
  indentation: number
  text: string
}

interface ItemEffect {
  name: string
  trigger: string
  lines: EffectLine[]
}

export interface SetBonus {
  required_items: number
  effect: string
  lines: string[]
}

export interface ItemDetail {
  id: string
  name: string
  quality: string
  description: string
  icon_id: string | null
  slot_type: string
  armor_type: string
  mitigation: number | null
  item_level: number | null
  required_level: number | null
  ilvl: number | null
  container_slots: number | null
  classes_label: string
  stats: ItemStat[]
  effects: ItemEffect[]
  adornment_slots: string[]
  flags: string[]
  extra_info: [string, string][]
  set_name: string | null
  set_bonuses: SetBonus[]
}

export interface AdornIlvlHint {
  color: string
  bonus: number
}

export interface TooltipState {
  itemId: string
  x: number
  y: number
  adorns?: AdornIlvlHint[]  // socketed adorns' ilvl bonuses (character context only)
}

// ── Colours (from tooltip.py) ─────────────────────────────────────────────────

export const BORDER_OUTER = '#c49e2c'
export const BORDER_INNER = '#364c5c'
const C_BODY       = '#c7cfc7'
const C_NAME       = '#e8e8e8'
const C_PRIMARY    = '#22ff22'
const C_SECONDARY  = '#3cc0c0'
const C_GOLD       = '#e6e970'
const C_WHITE      = '#dcdcdc'

const _outline = '-1px 0 0 #000, 0 1px 0 #000, 1px 0 0 #000, 0 -1px 0 #000'
function glow(color: string) {
  return `${_outline}, 0 0 4px ${color}, 0 0 4px ${color}`
}


const ADORN_COLOR: Record<string, string> = {
  white: '#dcdcdc', turquoise: '#3cc0c0', orange: '#ff8a00',
  red: '#d73737', blue: '#5076da', green: '#37c037',
  yellow: '#d7c037', purple: '#b04eda', black: '#a0a0a0',
}

// ── Cache ─────────────────────────────────────────────────────────────────────

const _cache = new Map<string, ItemDetail>()

/** Read an already-fetched item without triggering a network request. */
export function getCachedItem(id: string): ItemDetail | undefined {
  return _cache.get(id)
}

/** Populate the cache for `id` if not already present (fire-and-forget safe). */
export async function prefetchItem(id: string): Promise<void> {
  if (_cache.has(id)) return
  try {
    const r = await fetch(`/api/item/${id}`)
    if (!r.ok) return
    const data: ItemDetail = await r.json()
    _cache.set(id, data)
  } catch { /* swallow network errors */ }
}

// ── Tooltip portal ────────────────────────────────────────────────────────────

const TIP_W = 360

export function ItemTooltip({ state }: { state: TooltipState }) {
  const [item, setItem]   = useState<ItemDetail | null>(_cache.get(state.itemId) ?? null)
  const [loading, setLoading] = useState(!_cache.has(state.itemId))

  useEffect(() => {
    if (_cache.has(state.itemId)) {
      setItem(_cache.get(state.itemId)!)
      setLoading(false)
      return
    }
    setLoading(true)
    fetch(`/api/item/${state.itemId}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: ItemDetail) => { _cache.set(state.itemId, data); setItem(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [state.itemId])

  const { ref, position } = useTooltipPosition({ x: state.x, y: state.y, width: TIP_W, marginX: 16, marginY: 8 })

  const qs = item ? qualityStyle(item.quality) : null

  return createPortal(
    <div ref={ref} style={{
      position: 'fixed', left: position.left, top: position.top,
      width: TIP_W, zIndex: 9999,
      pointerEvents: 'none', userSelect: 'none',
      fontFamily: '"Times New Roman", Times, serif',
      background: '#0a0a0e',
      border: `2px solid ${BORDER_OUTER}`,
      boxShadow: `inset 0 0 0 1px ${BORDER_INNER}, 0 8px 32px rgba(0,0,0,0.9)`,
      borderRadius: 2,
      padding: 6,
    }}>
      <div style={{ border: `1px solid ${BORDER_INNER}`, padding: '8px 10px' }}>
        {loading && <div style={{ color: '#777', fontSize: '0.82rem' }}>Loading…</div>}
        {!loading && !item && <div style={{ color: 'var(--danger)', fontSize: '0.82rem' }}>Item not found</div>}
        {item && <TooltipContent item={item} qs={qs!} adorns={state.adorns} />}
      </div>
    </div>,
    document.body,
  )
}

// ── Content ───────────────────────────────────────────────────────────────────

export function TooltipContent({ item, qs, adorns }: { item: ItemDetail; qs: QualityStyle; adorns?: AdornIlvlHint[] }) {
  const primary   = item.stats.filter(s => s.stat_group === 'primary'   && s.value !== 0)
  const secondary = item.stats.filter(s => s.stat_group === 'secondary' && s.value !== 0)
  const isAdorn   = item.armor_type.toLowerCase().includes('adornment')
  // Food/drink: don't show individual effect name headers, just list content
  const isConsumable = ['food', 'drink'].some(t => item.slot_type.toLowerCase().includes(t))

  return (
    <div style={{ fontSize: '0.83rem', lineHeight: 1.4, color: C_BODY }}>

      {/* Name + icon */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 4 }}>
        <div style={{ flex: 1 }}>
          <div style={{ color: C_NAME, fontWeight: 'bold', fontSize: '0.97rem', lineHeight: 1.25 }}>
            {item.name}
          </div>
          {item.description && (
            <div style={{ color: C_BODY, marginTop: 3 }}>{item.description}</div>
          )}
          {item.container_slots != null && (
            <KV label="Slots" value={String(item.container_slots)} />
          )}
        </div>
        {item.icon_id && (
          <div style={{
            width: 46, height: 46, flexShrink: 0,
            backgroundImage: "url('/slot-empty-blue.png')",
            backgroundSize: 'cover',
            border: `1px solid ${BORDER_INNER}`,
            borderRadius: 2, overflow: 'hidden',
          }}>
            <img src={`/icons/${item.icon_id}.png`} alt=""
              style={{ width: 46, height: 46, display: 'block' }}
              onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
          </div>
        )}
      </div>

      {/* Quality badge */}
      {item.quality && (
        <div style={{
          color: qs.color, fontWeight: 'bold', fontSize: '0.9rem',
          textShadow: qs.glowColor ? glow(qs.glowColor) : undefined,
          marginBottom: 6,
        }}>
          {item.quality.toUpperCase()}
        </div>
      )}

      {/* Item level — a site annotation, not an in-game field. Subtle.
          When viewed on a character, each socketed adorn's bonus is shown
          after the base, tinted by the adorn's colour. */}
      {item.ilvl != null && (
        <div style={{
          color: '#c49e2c', fontSize: '0.78rem', letterSpacing: '0.02em',
          opacity: 0.85, marginTop: -2, marginBottom: 6,
        }}>
          Item Level {Math.round(item.ilvl).toLocaleString()}
          {(adorns ?? [])
            .filter(a => a.bonus > 0)
            .map((a, i) => (
              <span key={i} style={{ color: ADORN_COLOR[a.color.toLowerCase()] ?? C_WHITE, fontWeight: 'bold' }}>
                {' +'}{Math.round(a.bonus)}
              </span>
            ))}
        </div>
      )}

      {/* Adornment preamble */}
      {isAdorn && (
        <div style={{ color: C_BODY, marginBottom: 4 }}>Adds the following to an item:</div>
      )}

      {/* Primary stats */}
      {primary.length > 0 && (
        <Section>
          <StatCols stats={primary} color={C_PRIMARY} />
        </Section>
      )}

      {/* Secondary stats */}
      {secondary.length > 0 && (
        <Section>
          <StatCols stats={secondary} color={C_SECONDARY} />
        </Section>
      )}

      {/* Properties block — skip zero mitigation */}
      {(item.armor_type || item.slot_type || (item.mitigation != null && item.mitigation > 0) ||
        item.item_level != null || item.extra_info.length > 0) && (
        <Section>
          {item.armor_type && <KV label="Type"       value={item.armor_type} />}
          {item.slot_type  && <KV label="Slot"       value={item.slot_type} />}
          {item.mitigation != null && item.mitigation > 0 &&
            <KV label="Mitigation" value={item.mitigation.toLocaleString()} />}
          {item.item_level != null &&
            <KV label="Level" value={String(item.item_level)} valueColor={C_PRIMARY} />}
          {item.extra_info.map(([label, val]) => (
            <KV key={label} label={label} value={val} />
          ))}
        </Section>
      )}

      {/* Classes */}
      {item.classes_label && (
        <div style={{ color: C_PRIMARY, fontWeight: 'bold', marginTop: 5 }}>
          {item.classes_label}
        </div>
      )}

      {/* Effects */}
      {item.effects.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', marginBottom: 4 }}>Effects:</div>
          {item.effects.map((eff, i) => (
            <EffectBlock key={i} eff={eff} qs={qs} showName={!isConsumable} />
          ))}
        </Section>
      )}

      {/* Set bonuses */}
      {item.set_bonuses && item.set_bonuses.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', marginBottom: 4 }}>
            Set Bonus: {item.set_name}
          </div>
          {item.set_bonuses.map((bonus, i) => (
            <div key={i} style={{ marginBottom: i < item.set_bonuses.length - 1 ? 5 : 0 }}>
              <div style={{ color: C_WHITE, fontWeight: 'bold' }}>
                ({bonus.required_items}) {bonus.effect}
              </div>
              {bonus.lines.map((line, j) => (
                <div key={j} style={{ color: C_BODY, paddingLeft: 10, fontSize: '0.8rem' }}>
                  {'• '}{line}
                </div>
              ))}
            </div>
          ))}
        </Section>
      )}

      {/* Adornment slots */}
      {item.adornment_slots.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', marginBottom: 3 }}>Adornment Slots:</div>
          <div>
            {item.adornment_slots.map((slot, i) => (
              <span key={i}>
                <span style={{ color: ADORN_COLOR[slot.toLowerCase()] ?? C_WHITE }}>{slot}</span>
                {i < item.adornment_slots.length - 1 &&
                  <span style={{ color: C_WHITE }}>, </span>}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Flags */}
      {item.flags.length > 0 && (
        <Section>
          <div style={{ color: C_GOLD, fontWeight: 'bold', letterSpacing: '0.04em' }}>
            {item.flags.join('   ')}
          </div>
        </Section>
      )}

    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

export function Section({ children }: { children: ReactNode }) {
  return (
    <div style={{ borderTop: `1px solid ${BORDER_INNER}`, marginTop: 6, paddingTop: 5 }}>
      {children}
    </div>
  )
}

function KV({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ display: 'flex' }}>
      <span style={{ color: C_WHITE, minWidth: 90 }}>{label}</span>
      <span style={{ color: valueColor ?? C_SECONDARY }}>{value}</span>
    </div>
  )
}

function StatCols({ stats, color }: { stats: ItemStat[]; color: string }) {
  const rows: [ItemStat, ItemStat | null][] = []
  for (let i = 0; i < stats.length; i += 2) rows.push([stats[i], stats[i + 1] ?? null])
  return (
    <>
      {rows.map(([a, b], i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 4px' }}>
          <span style={{ color, fontWeight: 'bold' }}>{fmtStat(a)}</span>
          {b && <span style={{ color, fontWeight: 'bold' }}>{fmtStat(b)}</span>}
        </div>
      ))}
    </>
  )
}

function EffectBlock({ eff, qs, showName }: { eff: ItemEffect; qs: QualityStyle; showName: boolean }) {
  return (
    <div style={{ marginBottom: 5 }}>
      {showName && eff.name && (
        <div style={{
          color: qs.color, fontWeight: 'bold',
          textShadow: qs.glowColor ? glow(qs.glowColor) : undefined,
        }}>
          {eff.name}
        </div>
      )}
      {eff.trigger && (
        <div style={{ color: C_BODY, fontStyle: 'italic', fontSize: '0.8rem' }}>{eff.trigger}</div>
      )}
      {eff.lines.map((ln, j) => (
        <div key={j} style={{
          color: C_BODY, fontSize: '0.8rem',
          paddingLeft: `${Math.max(0, ln.indentation - 1) * 10}px`,
        }}>
          {'• '}{ln.text}
        </div>
      ))}
    </div>
  )
}

function fmtStat(s: ItemStat): string {
  const v = s.value
  return `${Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1)} ${s.display_name}`
}

// ── useItemTooltip hook ───────────────────────────────────────────────────────

/**
 * Encapsulates the tooltip/showTip/hideTip/moveTip state triple used by
 * CharacterPage and ItemSearchPage (and any future page that needs item
 * hover-tooltips). Callers just spread `{ tooltip, showTip, hideTip, moveTip }`.
 */
export function useItemTooltip() {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)

  const showTip = useCallback((itemId: string, e: MouseEvent, adorns?: TooltipState['adorns']) => {
    setTooltip({ itemId, x: e.clientX, y: e.clientY, adorns })
  }, [])
  const hideTip = useCallback(() => setTooltip(null), [])
  const moveTip = useCallback((e: MouseEvent) => {
    setTooltip(t => t ? { ...t, x: e.clientX, y: e.clientY } : null)
  }, [])

  return { tooltip, showTip, hideTip, moveTip }
}
