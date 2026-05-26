# Character Item Level

**Date:** 2026-05-26
**Status:** Built

## Summary

A character's **average gear ilvl**, derived from the per-item ilvls
([2026-05-26-item-ilvl-design](2026-05-26-item-ilvl-design.md)). Shown on the
character page, frozen onto each combatant at parse-ingest time, and surfaced as
a new leaderboard column.

## Computation

`character_ilvl` = the mean of the per-item ilvls across a character's equipped
**standard gear slots** that hold an ilvl-bearing item.

- **Slots counted** (`CHARACTER_GEAR_SLOTS`): primary, secondary, head, chest,
  shoulders, forearms, hands, legs, feet, left_ring, right_ring, ears, ears2,
  neck, left_wrist, right_wrist, ranged, waist, cloak, activate1, activate2.
  Excluded: ammo, food, drink, mount_adornment, mount_armor, event_slot (the
  event slot can hold an off-level token that would skew the average).
- Only Armor/Weapon/Shield carry an ilvl, so consumables/mounts/charms-without-ilvl
  drop out naturally; appearance pieces (no `level_to_use`) contribute nothing.
- **Two-handed weapons** are already halved at the item level, and the empty
  `secondary` slot simply isn't in the average — so a 2H wielder is neither
  double-counted nor penalised for the empty off-hand. (This is why we average
  over *filled* slots rather than a fixed slot count.)

Each equipped item's id is looked up in items.db (`ilvls_for_ids`, read-only
batch). No Census calls beyond the one the character page already makes.

Verified against `scripts/dev/example_census_character.json` (Menludiir): **372.2**.

## Where it lives

| Layer | Change |
|---|---|
| `census/item_level.py` | `CHARACTER_GEAR_SLOTS` + pure `character_ilvl(equipped)` |
| `census/db.py` | `ilvls_for_ids(ids)` read-only batch lookup |
| `web/routes/character.py` | `CharacterResponse.ilvl`, computed **once** in `_build_char_response` (so it's cached, and the parse snapshot gets it for free) |
| `frontend CharacterPage` | "Item Level N" line in the raid-ready box, under the check (the check itself is untouched — it stays the basic per-item grade) |
| `parses/db.py` + `parses/models.py` | `combatants.ilvl` column + migration; `CombatantSnapshot.ilvl`; insert/update carry it |
| `web/routes/parses.py` | snapshot resolution reads `cached.ilvl` (same `getattr` path as level/guild/cls) |
| `web/routes/rankings.py` + `frontend RankingsPage` | new `iLvl` column |

## Parse snapshot

Like level/guild/class, a combatant's ilvl is **frozen at ingest** from the
website's `character_cache` (which stores the full `CharacterResponse`, ilvl
included). It's not shown on the parse detail page — it exists so that when a
parse becomes a PB it carries the gear level the character had at that kill.
Subject to the same Census recent-login limitation as the other snapshot fields
(combatants that don't resolve store NULL).

## Leaderboard column

- **Character boards (Damage/Healing):** the PB character's snapshotted ilvl.
- **Speed board (per-guild):** the **average** ilvl of the resolved player
  combatants in that kill — a "raid ilvl" for the guild's run.
- NULL/unresolved ilvls render as `—`.

## Out of scope
- Showing ilvl on the parse detail page (deliberately not shown there).
- Changing the raid-ready check logic.
