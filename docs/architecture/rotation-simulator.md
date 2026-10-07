# Rotation simulator

The rotation simulator (`/simulator`, `frontend/src/pages/SimulatorPage.tsx`)
lets a player pick a character, build a priority-ordered rotation from that
character's real abilities, and simulate a fight against a boss dummy using
the character's sheet stats, gear-derived hidden modifiers, AA passives and
group/raid buffs.

Everything that does maths lives in `frontend/src/pages/simulator/` and is
React-free so it can be unit-tested directly:

| File | Role |
|---|---|
| `types.ts` | Shapes of `/api/character/{name}/rotation-data`, the buff-book endpoints, and the engine's config/result types |
| `formulas.ts` | The damage and timing model: every tunable constant plus pure per-cast / per-swing formulas |
| `engine.ts` | The discrete-event priority loop that turns a rotation + stats into a timeline, totals and DPS bins |
| `buffs.ts` | Buff windows: building external windows, merging overlaps, applying mods to stats at a point in time |
| `calibration.ts` | Per-ability calibration factors from observed hits |
| `optimizer.ts` | Rotation-order suggestions |
| `persistence.ts` | Per-character saved state in `localStorage` |

Ability data (timings, damage components, procs) comes from the backend's
rotation-data endpoint, which reads the spells and AA catalogues and parses the
effect text into `DamageComponent`s.

## The damage model (formulas.ts)

The model was fitted against in-game tooltips and then verified blind on other
abilities and against combat logs. "Tooltip-validated" means: given a
character's sheet stats, the model reproduces the min/max numbers the game
shows on the ability tooltip. Tooltips never include crit, fervor or
doublecast, so those multiply *dealt* damage only.

### Per cast

For one cast of an ability at spell level L:

```
coefficient   = (1 + primaryStatBonus(S, L) + base_damage_bonus_pct/100) × (1 + potency/100)
per component = B̄ × (coefficient + flat) × targets × (ticks, for a DoT) × (dot mod, for a DoT)
chain part    = Σ per component  × (1 + Enhance damage %)
flat part     = (ability mod + school-matched gear flat) × AM share   — once, on the primary component
cast damage   = (chain part + flat part + trigger-budget procs) × expected crit × fervor × doublecast × calibration
```

- `B̄` is the component's census midpoint `(min + max) / 2`.
- The primary-stat bonus and gear/AA "increases base damage" percentages are
  one **additive** bucket; potency multiplies the result.
- The **primary component** is the first unconditional `hit` (else the first
  unconditional component). Only it receives the ability mod. DoT ticks and
  conditional riders receive none.
- **Enhance** AAs ("Increases damage by 5%") scale the base-chain part only,
  never the flat part or proc payloads. With a known +30 disease flat from
  gear, Soulrot VII's hit back-solves to `chain × 1.05 + (AM + 30)` exactly;
  scaling the whole tooltip by 1.05 only looked right by coincidence.
- Everything snapshots at cast time.

### The ½ flat (`COMPONENT_BASE_FLAT_FRACTION`, `COMPONENT_FLAT_LEVEL_MIN`)

Each component application adds a constant `½ × B̄` to **both** tooltip ends.
Tooltip ranges therefore widen by the bare chain, not `chain + ½`; the average
is the same either way, so only displayed ranges and tooltip-minimum
predictions depend on the distinction. Divine Smite VII's tooltip width
back-solves the bare chain, and the min end then reproduces the character's
ability mod to ±0.03%.

Spells below level 61 (T6 and lower) carry no ½: Velium Winds (59) and Wrath of
the Ancients (60) spreads sit on the bare chain, while level 67/70+ spells on
the same character need it.

Auto-scaled class-granted ranks (e.g. Wrath) set `no_flat_mod`: their tooltip
is the bare chain, with no ½, no ability mod and no school flat.

### Ability-mod share by scope (`abilityModShare`)

| Primary component scope | Share of ability mod | Evidence |
|---|---|---|
| single target | 1 | Divine Strike VII, blind, ±0.15% |
| encounter AE | ⅓ (`AM_SHARE_ENCOUNTER`) | Wrath of the Ancients' two tooltip ends give a constant flat ≈ AM × 0.36; AM/2 misses by 10% |
| self-pulse blue AoE | 0 (`AM_SHARE_AOE`) | Exorcise across two stat loadouts |

### Primary-stat bonus (`primaryStatBonus`)

With soft cap `C(L) = 15·L + 20`:

- `S < C` — linear ramp to 0.65 (the real sub-cap curve is undocumented; raid
  characters sit well above it).
- `C ≤ S < 1200` — 0.65.
- `S ≥ 1200` — `0.28·log₂(S) − 2.2`.

### Procs (`procHitDamage`, `PROC_AM_SHARE`)

A proc payload hit deals `payload avg × (coefficient + flat) + AM × ⅓` for
single-target payloads, then crit × fervor. Fitted on Bolt of Power and Blessed
Armament dealt damage from logs (means within 1.1%, mins within 0.3%) and
confirmed again by Rabies II across a gear swap. Maintained pulse streams (AoE
scope) carry no ability mod. The school-flat share on procs is assumed to
follow the same ⅓ but is unvalidated.

Buff-granted procs (e.g. a bard's Precise Note) scale with the **supplier's**
stats, not the receiving player's (`proc_stats`).

Trigger-budget procs ("Grants a total of N triggers", e.g. Slothful Spirit's
three Sloth's Habitat hits) fire exactly N × chance times per cast of the
carrying ability, credited at cast, and are not scaled by that ability's
Enhance.

### Other per-ability rules

- **Per-HP components** (Lifeburn): `per_hp_rate × hp_fraction × caster max
  health` per application — flat, outside the coefficient chain and Enhance.
  The roughly-25%-of-pool fraction is an observed estimate; the in-game cap
  based on the target's max health is not modelled.
- **School flat** (`school_damage_flat`): gear such as "+30 disease damage done
  by spells" behaves like ability mod but only on components of that school.
  Lifeburn's hit − tick equals sheet AM + 30 exactly at both tooltip ends.
- **Crit**: expected multiplier `1 + chance × (0.3 + crit bonus)`. On the RoK
  TLE era Crit Bonus has no effect even though the stat exists
  (`CRIT_BONUS_ENABLED = false`); per-ability "Improves the Crit Bonus" Enhance
  lines raise only that ability's crit.
- **Timings**: cast, reuse and recovery are divided by `1 + speed/100`, each
  capped at 100. Scoped worn-item cuts ("Reduces reuse time of hostile
  spells") add to the speed only for abilities of matching polarity. Cast and
  reuse never fall below **half the original base**; AA second-cuts count
  toward that floor (a 5 s cast with a −1 s AA at +100% cast speed lands on
  2.5 s).
- **Perks**: EQ2 membership perks give +20% beneficial spell duration
  (`PERK_BENEFICIAL_DURATION_MULT`), toggleable because not every account has
  them. Hostile durations are unaffected.
- **DoTs with no known duration** assume 12 s and are flagged "est.".
- **Grey ranks**: when a rank is more than `GREY_RANK_LEVEL_GAP` (35) levels
  below the character, the game substitutes a level curve census does not
  carry; the sim's numbers there are knowingly wrong and the UI badges them.
  Within 35 levels the normal model holds (Divine Smite V and VI fit to 0.5%;
  IV at 49 levels down does not).

### Auto-attack

- Census sheet weapon min/max/delay are already fully cooked (the game folds
  STR/DPS scaling in), so a swing is uniform on `[min, max]` with no further
  sheet multipliers. `dps` and `attack_speed` in the sim stats are buff-window
  **deltas**; the caller zeroes the sheet values.
- Crits use a floor: `crit = max(critMult × roll, max + 1)` (most observed
  crits sit exactly at the floor).
- Haste and DPS-mod have diminishing returns: linear to 100, 200 stat = 125%
  actual, hard cap; the region between is modelled linearly.
- Multi Attack is linear (237 = two guaranteed extra swings + 37% chance of a
  third), capped at 600. Each flurry proc adds one extra hit (a 10.7-minute
  auto-only log matched flurry × 1 and ruled out × 2).
- Only **base** swings trigger procs: in a wand-only log, 190 procs landed on
  exactly 190 base swings out of 304 total auto hits.

Every constant is exported from `formulas.ts`; per-ability calibration absorbs
residual error.

## The engine (engine.ts)

A discrete-event priority loop over a single boss dummy (optionally with extra
stacked mobs and toggled conditions via `SimTarget`):

- At each decision point, cast the **first** ability in priority order whose
  recast is up. Busy time = effective cast + recovery; the recast timer starts
  when the cast completes (`readyAt = castStart + castTime + recast`). A
  minimum busy time stops a zero-timing data anomaly from spinning the loop.
- When nothing is ready, time jumps to the next recast expiry (idle).
- **DoTs** are analytic snapshot-at-cast: all tick damage is credited at cast
  and the remainder is clipped if the ability is re-cast before the last tick
  or the fight ends. With `dotRefreshHold`, a held DoT is not re-cast until its
  final tick has landed.
- **Buffs**: external windows are precomputed (`buffs.buildExternalWindows`);
  casting one of the character's own temp buffs opens a window at cast end.
  Windows with the same buff id merge instead of stacking. Stats at a cast are
  sheet stats plus whatever windows are open at that instant.
- **Auto-attack** is a continuous stream integrated piecewise across buff
  window edges.
- **Passive proc streams**: expected procs = trigger events × chance, or the
  per-minute rate for rate-limited procs. Damage-shield procs use the
  user-set incoming-hits-per-minute. Cast-applied proc buffs fire for every
  qualifying event while their window is open.
- `dpsBins` holds exact damage per 1 s bin; every credit and clip is mirrored
  into it, so the bins sum to `totalDamage`.

Known approximations: passive procs use base-stat crit and count an AoE or
multi-hit cast as one trigger event; power is ignored.

## Saved state (persistence.ts)

Per-character state (rotation, buff choices, timing sliders, calibration
inputs, group make-up, target) is saved in `localStorage` under
`eq2lexicon.simulator.v1.<character>`. It is a per-viewer convenience only:
every read and write is wrapped in try/catch so a private window or blocked
site data yields a fresh page rather than an error. A payload whose `v` does
not match the current version is ignored.

Older saves may carry fields the current UI no longer writes
(`groupClasses`, `baseDamageBonusPct`, `hiddenCastSpeedPct`, a bare
`autoAttack` boolean). The loader ignores `groupClasses`, reads the manual
bonus fields as overrides, and maps `autoAttack: true` without a mode to the
class default.

`SimulatorPage` tracks which character the loaded state belongs to
(`loadedFor`) so switching characters never saves one character's state under
another's key.
