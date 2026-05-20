import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'

// ── Types ────────────────────────────────────────────────────────────────────

interface EquipmentSlot {
  slot: string
  name: string
  item_id: string | null
  icon_id: string | null
  tier: string | null
}

interface CharacterStats {
  health_max: number | null
  health_regen: number | null
  power_max: number | null
  power_regen: number | null
  run_speed: number | null
  status_points: number | null
  str_eff: number | null
  sta_eff: number | null
  agi_eff: number | null
  wis_eff: number | null
  int_eff: number | null
  armor: number | null
  avoidance: number | null
  block_chance: number | null
  parry: number | null
  mit_physical: number | null
  mit_elemental: number | null
  mit_noxious: number | null
  mit_arcane: number | null
  potency: number | null
  crit_chance: number | null
  crit_bonus: number | null
  fervor: number | null
  dps: number | null
  double_attack: number | null
  ability_doublecast: number | null
  attack_speed: number | null
  strikethrough: number | null
  accuracy: number | null
  ability_mod: number | null
  weapon_damage_bonus: number | null
  flurry: number | null
  lethality: number | null
  toughness: number | null
  reuse_speed: number | null
  casting_speed: number | null
  recovery_speed: number | null
  primary_min: number | null
  primary_max: number | null
  primary_delay: number | null
  secondary_min: number | null
  secondary_max: number | null
  secondary_delay: number | null
  ranged_min: number | null
  ranged_max: number | null
  ranged_delay: number | null
}

interface Character {
  id: string
  name: string
  level: number | null
  cls: string | null
  race: string | null
  gender: string | null
  deity: string | null
  aa_count: number
  world: string
  ts_class: string | null
  ts_level: number | null
  stats: CharacterStats
  equipment: EquipmentSlot[]
}

// ── Paperdoll slot config ────────────────────────────────────────────────────

const LEFT_SLOTS: [string, string][] = [
  ['Charm',      'activate1'],
  ['Cloak',      'cloak'],
  ['Head',       'head'],
  ['Shoulders',  'shoulders'],
  ['Chest',      'chest'],
  ['Arms',       'forearms'],
  ['Hands',      'hands'],
  ['Legs',       'legs'],
  ['Feet',       'feet'],
  ['Primary',    'primary'],
  ['Secondary',  'secondary'],
]

const RIGHT_SLOTS: [string, string][] = [
  ['Charm',      'activate2'],
  ['Ear',        'ears'],
  ['Ear',        'ears2'],
  ['Neck',       'neck'],
  ['Ring',       'left_ring'],
  ['Ring',       'right_ring'],
  ['Wrist',      'left_wrist'],
  ['Wrist',      'right_wrist'],
  ['Waist',      'waist'],
  ['Ranged',     'ranged'],
  ['Food',       'food'],
  ['Drink',      'drink'],
]

const DISPLAY_TO_BASE: Record<string, string> = {
  Primary: 'primary', Secondary: 'secondary', Ranged: 'ranged',
  Head: 'head', Chest: 'chest', Shoulders: 'shoulders',
  Forearms: 'forearms', Hands: 'hands', Legs: 'legs',
  Feet: 'feet', Waist: 'waist', Neck: 'neck', Cloak: 'cloak',
  Charm: 'activate', Finger: 'ring', Ear: 'ear', Wrist: 'wrist',
  Food: 'food', Drink: 'drink',
}
const MULTI_SUFFIXES: Record<string, string[]> = {
  activate: ['activate1', 'activate2'],
  ring:     ['left_ring', 'right_ring'],
  ear:      ['ears', 'ears2'],
  wrist:    ['left_wrist', 'right_wrist'],
}

function buildSlotMap(equipment: EquipmentSlot[]): Map<string, EquipmentSlot> {
  const map = new Map<string, EquipmentSlot>()
  const counters: Record<string, number> = {}
  for (const s of equipment) {
    const base = DISPLAY_TO_BASE[s.slot]
    if (!base) continue
    const suffixes = MULTI_SUFFIXES[base]
    let key: string
    if (suffixes) {
      counters[base] = (counters[base] ?? 0) + 1
      key = suffixes[counters[base] - 1] ?? base
    } else {
      key = base
    }
    map.set(key, s)
  }
  return map
}

const TIER_COLOUR: Record<string, string> = {
  FABLED:    'var(--tier-fabled)',
  LEGENDARY: 'var(--tier-legendary)',
  TREASURED: 'var(--tier-treasured)',
  UNCOMMON:  'var(--tier-uncommon)',
  COMMON:    'var(--tier-common)',
}
function tierColour(tier: string | null) {
  return TIER_COLOUR[(tier ?? '').toUpperCase()] ?? 'var(--text)'
}

// ── Page ─────────────────────────────────────────────────────────────────────

// Module-level cache: survives re-renders and Vite HMR remounts.
// Keyed by lower-cased character name.
const _charCache = new Map<string, Character>()

type State =
  | { status: 'loading' }
  | { status: 'ok'; char: Character }
  | { status: 'not_found'; name: string }
  | { status: 'error'; message: string }

export default function CharacterPage() {
  const { name } = useParams<{ name: string }>()
  const [state, setState] = useState<State>(() => {
    // Initialise from cache so there's never a loading flash on back-navigation.
    const cached = name ? _charCache.get(name.toLowerCase()) : undefined
    return cached ? { status: 'ok', char: cached } : { status: 'loading' }
  })

  useEffect(() => {
    if (!name) return
    // Already have fresh data — don't hit Census again.
    if (_charCache.has(name.toLowerCase())) return
    fetch(`/api/character/${encodeURIComponent(name)}`, { credentials: 'include' })
      .then(async res => {
        if (res.status === 404) { setState({ status: 'not_found', name }); return }
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          setState({ status: 'error', message: body.detail ?? `HTTP ${res.status}` })
          return
        }
        const char: Character = await res.json()
        _charCache.set(name.toLowerCase(), char)
        setState({ status: 'ok', char })
      })
      .catch(err => setState({ status: 'error', message: String(err) }))
  }, [name])

  return (
    <main style={{ maxWidth: 1280, margin: '2rem auto', padding: '0 1rem' }}>
      <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>← Back</Link>
      {state.status === 'loading' && <p style={{ marginTop: '2rem', color: 'var(--text-muted)' }}>Loading…</p>}
      {state.status === 'not_found' && <p style={{ marginTop: '2rem', color: 'var(--text-muted)' }}>Character <strong>{state.name}</strong> not found.</p>}
      {state.status === 'error' && <p style={{ marginTop: '2rem', color: '#f87171' }}>Error: {state.message}</p>}
      {state.status === 'ok' && <CharacterView char={state.char} />}
    </main>
  )
}

// ── Character view ────────────────────────────────────────────────────────────

function CharacterView({ char }: { char: Character }) {
  const bySlot = buildSlotMap(char.equipment)

  return (
    <div style={{ marginTop: '1.5rem' }}>
      {/* Full-width general banner */}
      <GeneralBanner char={char} />

      {/* Below: stats panel + paperdoll side by side */}
      <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'flex-start', marginTop: '1rem' }}>
        {/* Left: detailed stats */}
        <div style={{ width: 260, flexShrink: 0 }}>
          <StatsPanel char={char} />
        </div>

        {/* Right: paperdoll */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={sectionHeading}>Equipment</h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 12px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {LEFT_SLOTS.map(([label, key]) => (
                <SlotRow key={key} label={label} item={bySlot.get(key) ?? null} iconSide="left" />
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {RIGHT_SLOTS.map(([label, key]) => (
                <SlotRow key={key} label={label} item={bySlot.get(key) ?? null} iconSide="right" />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── General banner (full width, above equipment) ──────────────────────────────

function GeneralBanner({ char }: { char: Character }) {
  const s = char.stats

  // Build the stat cells: [label, display value]
  const cells: [string, string][] = [
    ['Level', `${char.level ?? '—'} ${char.cls ?? ''}`.trim()],
    ...(char.ts_class ? [['Tradeskill', `${char.ts_level ?? '—'} ${char.ts_class}`] as [string, string]] : []),
    ...(char.deity    ? [['Deity',      char.deity] as [string, string]] : []),
    ['AAs',        char.aa_count.toLocaleString()],
    ...(s.health_max   != null ? [['Health',     s.health_max.toLocaleString()]          as [string, string]] : []),
    ...(s.power_max    != null ? [['Power',      s.power_max.toLocaleString()]           as [string, string]] : []),
    ...(s.run_speed    != null ? [['Run Speed',  `${Math.round(s.run_speed)}%`]          as [string, string]] : []),
    ...(s.status_points != null ? [['Status',   s.status_points.toLocaleString()]        as [string, string]] : []),
  ]

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '0.75rem 1rem',
    }}>
      {/* Name + subtitle row */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', marginBottom: '0.6rem' }}>
        <h1 style={{ fontSize: '1.4rem', margin: 0 }}>{char.name}</h1>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          {[char.world, char.race, char.gender].filter(Boolean).join(' · ')}
        </span>
      </div>

      {/* Stat chips */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
        {cells.map(([label, val]) => (
          <div key={label} style={{ display: 'flex', alignItems: 'baseline', gap: '0.3rem' }}>
            <span style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>{label}</span>
            <span style={{ fontSize: '0.9rem', fontWeight: 600 }}>{val}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Stats panel (left of paperdoll, no General group) ─────────────────────────

function StatsPanel({ char }: { char: Character }) {
  const s = char.stats

  return (
    <div>
      <StatGroup title="Attributes">
        <StatRow label="Strength"     value={s.str_eff} fmt="int" />
        <StatRow label="Stamina"      value={s.sta_eff} fmt="int" />
        <StatRow label="Agility"      value={s.agi_eff} fmt="int" />
        <StatRow label="Wisdom"       value={s.wis_eff} fmt="int" />
        <StatRow label="Intelligence" value={s.int_eff} fmt="int" />
      </StatGroup>

      <StatGroup title="Defense">
        <StatRow label="Armor"              value={s.armor}        fmt="int" />
        <StatRow label="Avoidance"          value={s.avoidance}    fmt="int" />
        <StatRow label="Block Chance"       value={s.block_chance} fmt="pct1" />
        <StatRow label="Parry"              value={s.parry}        fmt="int" />
        <StatRow label="Physical Mit"       value={s.mit_physical}  fmt="pct1" />
        <StatRow label="Elemental Mit"      value={s.mit_elemental} fmt="pct1" />
        <StatRow label="Noxious Mit"        value={s.mit_noxious}   fmt="pct1" />
        <StatRow label="Arcane Mit"         value={s.mit_arcane}    fmt="pct1" />
      </StatGroup>

      <StatGroup title="Combat">
        <StatRow label="Potency"            value={s.potency}              fmt="dec1" />
        <StatRow label="Crit Chance"        value={s.crit_chance}          fmt="pct1" />
        <StatRow label="Crit Bonus"         value={s.crit_bonus}           fmt="pct1" />
        <StatRow label="Fervor"             value={s.fervor}               fmt="dec1" />
        <StatRow label="DPS"                value={s.dps}                  fmt="dec1" />
        <StatRow label="Double Attack"      value={s.double_attack}        fmt="pct1" />
        <StatRow label="Ability Doublecast" value={s.ability_doublecast}   fmt="pct1" />
        <StatRow label="Attack Speed"       value={s.attack_speed}         fmt="pct1" />
        <StatRow label="Ability Mod"        value={s.ability_mod}          fmt="int" />
        <StatRow label="Weapon Damage"      value={s.weapon_damage_bonus}  fmt="pct1" />
        <StatRow label="Flurry"             value={s.flurry}               fmt="pct1" />
        <StatRow label="Strikethrough"      value={s.strikethrough}        fmt="pct1" />
        <StatRow label="Accuracy"           value={s.accuracy}             fmt="pct1" />
        <StatRow label="Lethality"          value={s.lethality}            fmt="pct1" />
        <StatRow label="Toughness"          value={s.toughness}            fmt="dec1" />
      </StatGroup>

      <StatGroup title="Casting">
        <StatRow label="Reuse Speed"    value={s.reuse_speed}    fmt="pct1" />
        <StatRow label="Casting Speed"  value={s.casting_speed}  fmt="pct1" />
        <StatRow label="Recovery Speed" value={s.recovery_speed} fmt="pct1" />
      </StatGroup>

      <StatGroup title="Weapon">
        {s.primary_min != null && s.primary_max != null &&
          <StatRow label="Primary"   value={`${s.primary_min.toLocaleString()} – ${s.primary_max.toLocaleString()}  (${s.primary_delay?.toFixed(2)}s)`} />}
        {s.secondary_min != null && s.secondary_max != null &&
          <StatRow label="Secondary" value={`${s.secondary_min.toLocaleString()} – ${s.secondary_max.toLocaleString()}  (${s.secondary_delay?.toFixed(2)}s)`} />}
        {s.ranged_min != null && s.ranged_max != null &&
          <StatRow label="Ranged"    value={`${s.ranged_min.toLocaleString()} – ${s.ranged_max.toLocaleString()}  (${s.ranged_delay?.toFixed(2)}s)`} />}
      </StatGroup>
    </div>
  )
}

// ── Stat display helpers ──────────────────────────────────────────────────────

type Fmt = 'int' | 'pct' | 'pct1' | 'dec1'

function fmt(value: number, format?: Fmt): string {
  switch (format) {
    case 'int':  return value.toLocaleString()
    case 'pct':  return `${Math.round(value)}%`
    case 'pct1': return `${value.toFixed(1)}%`
    case 'dec1': return value.toFixed(1)
    default:     return String(value)
  }
}

function StatRow({ label, value, fmt: format }: {
  label: string
  value: number | string | null | undefined
  fmt?: Fmt
}) {
  if (value === null || value === undefined) return null
  const display = typeof value === 'number' ? fmt(value, format) : value
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '2px 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem', paddingRight: '0.5rem' }}>{label}</span>
      <span style={{ fontSize: '0.85rem', fontWeight: 500, textAlign: 'right' }}>{display}</span>
    </div>
  )
}

function StatGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '1rem' }}>
      <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--accent)', fontWeight: 600, marginBottom: '3px' }}>
        {title}
      </div>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 5, padding: '4px 8px' }}>
        {children}
      </div>
    </div>
  )
}

// ── Paperdoll helpers ─────────────────────────────────────────────────────────

function SlotRow({ label, item, iconSide }: {
  label: string
  item: EquipmentSlot | null
  iconSide: 'left' | 'right'
}) {
  const url = item?.icon_id ? `/icons/${item.icon_id}.png` : null
  const iconEl = (
    <div style={{ ...iconBox, backgroundImage: `url('/slot-empty-blue.png')`, backgroundSize: 'cover', backgroundPosition: 'center' }}>
      {url && <img src={url} alt={item?.name ?? ''} style={{ width: 40, height: 40, display: 'block' }} onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />}
    </div>
  )
  const textEl = (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
      <span style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', lineHeight: 1 }}>{label}</span>
      {item
        ? <span style={{ color: tierColour(item.tier), fontWeight: 500, fontSize: '0.88rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', lineHeight: 1.3 }}>{item.name}</span>
        : <span style={{ color: 'var(--border)', fontSize: '0.82rem', fontStyle: 'italic', lineHeight: 1.3 }}>Empty</span>}
    </div>
  )
  return (
    <div style={{ ...slotRow, flexDirection: iconSide === 'left' ? 'row' : 'row-reverse' }}>
      {iconEl}{textEl}
    </div>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

const sectionHeading: React.CSSProperties = {
  fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.07em',
  color: 'var(--text-muted)', marginBottom: '0.5rem',
}
const slotRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '0.5rem',
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 4, padding: '3px 6px', minWidth: 0, height: 50,
}
const iconBox: React.CSSProperties = {
  width: 40, height: 40, flexShrink: 0, borderRadius: 3,
  display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden',
}
