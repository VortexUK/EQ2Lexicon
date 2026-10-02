import { useRef, useState } from 'react'
import { Badge, Card, SectionLabel } from '../../components/ui'
import { useClasses } from '../../useClasses'
import CharacterSearchInput from './CharacterSearchInput'
import { SpellIcon } from './RotationBuilder'
import type { ClassBuff, GroupMemberBook } from './types'

// Group make-up: add the ACTUAL characters in your group (searched by
// name), see each one's real buff book at the spell ranks THEY own, and
// tick what they actually run. Ticked permanent buffs apply always-on;
// ticked temps become rotated windows with their real duration/recast;
// proc buffs feed the proc engine.

const MAX_GROUP = 5

/** Which BuffMods keys read as what in the summary line. */
const MOD_LABEL: Record<string, string> = {
  hastePct: 'Haste',
  dpsModPct: 'DPS',
  doubleAttackPct: 'Multi Attack',
  critChancePct: 'Crit Chance',
  critBonusPct: 'Crit Bonus',
  castSpeedPct: 'Cast Speed',
  reuseSpeedPct: 'Reuse',
  recoverySpeedPct: 'Recovery',
  abilityModFlat: 'Ability Mod',
  potencyPct: 'Potency',
  fervorPct: 'Fervor',
  weaponDamagePct: 'Weapon Damage %',
  doublecastPct: 'Doublecast',
  baseDamagePct: 'Base Damage %',
  strFlat: 'STR',
  agiFlat: 'AGI',
  wisFlat: 'WIS',
  intFlat: 'INT',
}

export function isTempBuff(b: ClassBuff): boolean {
  return b.duration_s != null && b.duration_s > 0 && b.duration_s <= 300
}

function modsSummary(b: ClassBuff): string {
  const bits = Object.entries(b.mods).map(([k, v]) => `+${v} ${MOD_LABEL[k] ?? k}`)
  for (const p of b.procs) {
    const dmg = p.components.map(c => `${c.min_dmg}-${c.max_dmg} ${c.school}`).join(', ')
    const rate = p.per_minute != null ? `~${p.per_minute}/min` : `${p.chance_pct.toFixed(0)}%`
    bits.push(`${rate} proc: ${dmg}`)
  }
  return bits.join(' · ')
}

function BuffRow({ buff, enabled, onToggle }: {
  buff: ClassBuff
  enabled: boolean
  onToggle: (on: boolean) => void
}) {
  const summary = modsSummary(buff)
  const temp = isTempBuff(buff)
  return (
    <label
      className="flex items-center gap-2 px-2 py-1 rounded-sm cursor-pointer hover:bg-gold/5"
      title={buff.effect_lines.join('\n')}
    >
      <input
        type="checkbox"
        checked={enabled}
        onChange={e => onToggle(e.target.checked)}
        className="accent-[var(--color-gold)]"
      />
      <SpellIcon ability={buff} size={18} />
      <div className="min-w-0 flex-1">
        <div className="text-[0.82rem] truncate flex items-center gap-1.5">
          {buff.name}
          {buff.rank != null
            ? (buff.max_rank ?? 0) > 1 && <Badge variant="muted">{`${buff.rank}/${buff.max_rank}`}</Badge>
            : buff.tier_name && <Badge variant="muted">{buff.tier_name}</Badge>}
          <Badge variant={buff.target_scope === 'raid' ? 'gold' : buff.target_scope === 'ally' ? 'info' : 'muted'}>
            {buff.target_scope}
          </Badge>
          {temp && (
            <Badge variant="warning" title={`Rotated: ${buff.duration_s}s up, ${buff.recast_s}s recast`}>
              {buff.duration_s}s / {buff.recast_s}s
            </Badge>
          )}
          {(buff.aa_adjustments?.length ?? 0) > 0 && (
            <Badge variant="success" title={buff.aa_adjustments!.join('\n')}>
              AA
            </Badge>
          )}
        </div>
        <div className="text-[0.7rem] truncate">
          {summary ? (
            <span className="text-success">{summary}</span>
          ) : (
            <span className="text-text-muted">{buff.effect_lines[0] ?? 'no effect text'}</span>
          )}
        </div>
      </div>
    </label>
  )
}

export default function GroupMakeupPanel({ members, books, enabled, excludeName, hiddenBaseNames, onAddMember, onRemoveMember, onToggle }: {
  /** Group member character names, in add order. */
  members: string[]
  /** name → their buff book; undefined = loading, null = lookup failed. */
  books: Record<string, GroupMemberBook | null | undefined>
  /** Buff base names handled by the Raid buffs panel (CoB, PotM…) —
   * hidden here so they aren't configured twice. The book still carries
   * them for supplier binding. */
  hiddenBaseNames: string[]
  /** Enabled buff keys ("member::base_name") — ticking under a specific
   * member says WHO supplies the buff (their stats scale its procs). */
  enabled: string[]
  /** The simmed character — never offered (own buffs are in sheet stats). */
  excludeName: string | null
  onAddMember: (name: string) => void
  onRemoveMember: (name: string) => void
  onToggle: (baseName: string, on: boolean) => void
}) {
  const { colourFor } = useClasses()
  // Members collapse to one line (books are long); a freshly added member
  // starts expanded so their buffs are immediately tickable.
  const [expanded, setExpanded] = useState<string[]>([])
  const seenRef = useRef<Set<string>>(new Set(members))
  for (const m of members) {
    if (!seenRef.current.has(m)) {
      seenRef.current.add(m)
      setExpanded(prev => (prev.includes(m) ? prev : [...prev, m]))
    }
  }

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Group make-up</SectionLabel>
      <p className="text-[0.75rem] text-text-muted mt-1 mb-2">
        Add the actual characters in your group — their buff books use the spell ranks THEY own.
        Tick what they actually run on you. Your own permanent buffs are already in your sheet
        stats — you can't add yourself.
      </p>

      <div className="flex items-center gap-2">
        <div className="flex-1">
          <CharacterSearchInput
            placeholder={members.length >= MAX_GROUP ? 'Group is full' : 'Add a character by name…'}
            disabled={members.length >= MAX_GROUP}
            exclude={[...members, ...(excludeName ? [excludeName] : [])]}
            onPick={r => onAddMember(r.name)}
          />
        </div>
        <span className="text-[0.72rem] text-text-muted shrink-0">{members.length}/{MAX_GROUP}</span>
      </div>

      <div className="flex flex-col gap-3 mt-3">
        {members.map(name => {
          const book = books[name]
          const open = expanded.includes(name)
          // Buffs the Raid buffs panel owns (CoB, PotM…) are hidden here;
          // the book still carries them for supplier binding.
          const shown = book ? book.buffs.filter(b => !hiddenBaseNames.includes(b.base_name)) : []
          const ticked = book ? shown.filter(b => enabled.includes(`${name}::${b.base_name}`)).length : 0
          return (
            <div key={name} className="rounded-sm bg-surface-raised border border-border px-2 py-2">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setExpanded(prev => (open ? prev.filter(x => x !== name) : [...prev, name]))}
                  aria-expanded={open}
                  title={open ? 'Collapse' : 'Expand'}
                  className="appearance-none border-0 bg-transparent p-0 flex items-center gap-2 cursor-pointer text-text min-w-0 flex-1 text-left"
                >
                  <span className="text-[0.7rem] text-text-muted w-3">{open ? '▾' : '▸'}</span>
                  <span className="text-[0.85rem] font-semibold text-gold">{book?.character_name ?? name}</span>
                  {book && (
                    <span className="text-[0.78rem]" style={{ color: colourFor(book.cls ?? '') }}>
                      {book.cls ?? '?'}
                    </span>
                  )}
                  <span className="text-[0.7rem] text-text-muted">
                    {book === undefined
                      ? 'loading…'
                      : book === null
                        ? 'lookup failed'
                        : `${ticked}/${shown.length} buffs ticked`}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onRemoveMember(name)}
                  title={`Remove ${name}`}
                  className="appearance-none border-0 bg-transparent ml-auto text-text-muted hover:text-text cursor-pointer text-[0.85rem] leading-none p-1"
                >
                  ✕
                </button>
              </div>
              {open && book && shown.length === 0 && (
                <p className="text-[0.75rem] text-text-muted m-0 px-2 mt-1">No group-scope buffs found.</p>
              )}
              {open && book && (
                <div className="flex flex-col mt-1">
                  {shown.map(b => (
                    <BuffRow
                      key={b.base_name}
                      buff={b}
                      enabled={enabled.includes(`${name}::${b.base_name}`)}
                      onToggle={on => onToggle(`${name}::${b.base_name}`, on)}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}
