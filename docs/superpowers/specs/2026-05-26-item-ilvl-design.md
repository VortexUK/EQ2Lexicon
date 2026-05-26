# Item Level (ilvl)

**Date:** 2026-05-26
**Status:** Design approved, building

## Summary

A WoW-style **item level** ("ilvl") for every piece of wearable gear — a single
number summarising an item's power — computed from its equip level, quality
tier, and Potency. Stored on `items.db`, surfaced in the web item tooltip, and
available for future search/sort.

## Goal

One stable, comparable number per gear item that:

- ranks roughly by power (higher level / better quality / more potency → higher),
- is **stable over time** — the same item gets the same ilvl regardless of when
  it's computed, even after a level-cap increase,
- is **defined for every wearable item**, including the ~37% of high-tier gear
  that carries no Potency.

## Formula

```
ilvl = SCALE × (L² / REF²) × Tier × (1 + Potency / K)
```

| Symbol | Value | Rationale |
|---|---|---|
| `L` | `level_to_use` (the equip level) | Player-scaled level. **Not** `item_level`, which is an internal 1–2560 scale that overshoots the player range. |
| `REF` | **100** (fixed constant) | Stable forever. Using the *server* max level would re-base every item's ilvl whenever the cap rises (and Varsoon is a TLE whose cap unlocks in stages) — the opposite of "stable". A fixed reference makes ilvls comparable across all expansions. Over-cap future gear simply scores >1 on the `L²/REF²` term, which is correct. |
| `Tier` | 1–6 | Quality band (see below). |
| `Potency` | the item's Potency stat, **0 when absent** | **Bonus form** (`1 + Potency/K`), not a multiplier. A pure `× Potency` would zero the 37% of fabled gear with no potency and let potency's ~1000× range (3.7 → 3578 observed) swamp level and tier. The bonus form keeps a sane base for every item and dampens potency to a ~1–5× contribution. |
| `K` | **1000** | Potency dampening. Tunable. |
| `SCALE` | **100** | Lands ilvls in a readable range. Tunable. |

`REF`, `K`, `SCALE` are module-level constants in `census/item_level.py`; changing
them is a recompute (re-run the backfill), not a schema change.

The `L²` squaring is intentional — it front-loads weight onto high-level gear.

### Worked examples

| Item | ilvl |
|---|---|
| Treasured, lvl 50, no potency | ~75 |
| Fabled, lvl 100, no potency | ~500 |
| Fabled, lvl 100, potency 480 | ~740 |
| Fabled, lvl 100, potency 3578 | ~2290 |
| Celestial, lvl 120, potency 3578 | ~3960 |

On a TLE server (low potency) ilvl is driven mostly by level + tier; as potency
inflates with later content, ilvl climbs accordingly.

## Tier band

The tier string is mapped to a 1–6 band by the **strongest quality keyword** it
contains. This handles the compound strings in the data
(`MASTERCRAFTED LEGENDARY`, `MASTERCRAFTED CELESTIAL`, …) correctly — they take
the band of their highest keyword.

| Band | Keywords |
|---|---|
| 1 | common |
| 2 | uncommon, handcrafted |
| 3 | treasured |
| 4 | mastercrafted, legendary |
| 5 | fabled |
| 6 | celestial, mythical, ethereal |

So `Mastercrafted Legendary` → 4, `Mastercrafted Fabled` → 5,
`Mastercrafted Celestial` → 6. (`uncommon` contains the substring `common`, but
`max()` over matched bands resolves it to 2 correctly.) An unrecognised/empty
tier defaults to band 1.

## Scope — what gets an ilvl

`type IN ('Armor', 'Weapon', 'Shield')`. All three carry a `slot_list` (they are
equippable), and adornments live under a *different* type, so this set is exactly
"wearable gear" and excludes adornments for free. Everything else (spell scrolls,
recipes, food, house items, …) gets **no** ilvl (`NULL`). Gear with no
`level_to_use` (heritage/appearance pieces) also gets `NULL` rather than a
misleading 0.

## Architecture

### `census/item_level.py` (new)
Single source of the formula. Pure, dependency-free, unit-testable:
- `GEAR_TYPES = frozenset({"Armor", "Weapon", "Shield"})`
- `ILVL_REF`, `ILVL_SCALE`, `ILVL_POTENCY_K` constants
- `tier_band(tier_display: str | None) -> int`
- `compute_ilvl(level_to_use, tier_display, potency, item_type) -> float | None`
  — applies the gear-type + level gating, returns `None` when out of scope.

### `parse_item` (`census/item_parser.py`)
`ItemData` gains `ilvl: float | None = None`. `parse_item` computes it via
`compute_ilvl`, reading Potency from the already-parsed `stats` list (no extra
parsing, no new import cycle). This makes ilvl available on the tooltip for both
DB-sourced and live-Census items.

### `items.db` (`census/db.py`)
- New `ilvl REAL` column (DDL + `_MIGRATIONS` `ALTER TABLE` entry so existing DBs
  gain it on `init_db`, NULL until backfilled).
- `_UPSERT_SQL` + `item_to_row` maintain it for newly-downloaded items (Potency
  via the existing `extract_item_stats`). This keeps the column current for
  future search/sort by ilvl.
- The column is a *materialised copy*; the tooltip reads `ItemData.ilvl` from the
  parser, so the two paths always agree because they call the same function.

### `scripts/backfill_item_levels.py` (new)
Fills `ilvl` for all existing gear rows in place — no re-download. Reads
`level_to_use`, `tier_display`, `type`, and Potency (LEFT JOIN `item_stats`),
computes via `compute_ilvl`, batched `UPDATE`s with progress. Idempotent
(recomputes). Run locally, then copy `items.db` to the Railway volume as usual.

### Web (`web/routes/item.py`)
`ItemResponse` gains `ilvl: float | None = None`; `get_item` passes
`ilvl=item.ilvl`.

### Frontend (`frontend/src/components/ItemTooltip.tsx`)
A single quiet line under the item name — *"Item Level N"* (rounded) — shown only
when `ilvl` is present. EQ2 tooltips are dense, so one line rather than a badge.
Item type added to the `ItemResponse` TS type as `ilvl?: number | null`.

## Out of scope (later, if wanted)
- The bot's PIL tooltip (`image/tooltip.py`) — trivial follow-on.
- Search/sort/filter by ilvl in the item search UI (column now exists to enable it).

## Testing
- `tier_band`: each keyword → expected band; compounds (`MASTERCRAFTED *`); the
  `uncommon`/`common` substring case; empty/unknown → 1.
- `compute_ilvl`: the worked examples (within rounding); non-gear → None; no
  level → None; no potency → base (×1); potency scales as `1 + p/K`.
- `parse_item`: a gear fixture gets a numeric ilvl; a non-gear fixture gets None.
- `item_to_row`: gear row carries ilvl; non-gear NULL.
- Full gate: ruff format/check, pyright, pytest, `tsc -b`.

## Rollout
Additive. Migration adds a nullable column (no data risk). Build locally:
`uv run python scripts/backfill_item_levels.py`, then copy `items.db` to the
Railway volume (same as the other generated DBs).
