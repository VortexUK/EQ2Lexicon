---
paths:
  - "backend/image/**"
  - "backend/eq2db/aas.py"
  - "scripts/preview_*.py"
  - "scripts/build_aas_db.py"
  - "scripts/download_aa_*.py"
---

# Image rendering: item tooltips and AA trees

## AA tree notes

Tree + node + per-xpac-limit reference data lives in the Postgres `aas` schema (see `backend/eq2db/aas.py`); migration `db/migrations/0012_aas.sql` carries the FULL dataset as seeds, so every environment is data-complete from migrations alone. Refresh: `scripts/download_aa_trees.py` fetches tree JSONs locally (gitignored intermediates), `scripts/build_aas_db.py` upserts them + the committed `aa_limits.json` into the schema at DATABASE_URL. Icons stay as files: `data/AAs/icons/{id}.png` served at `/aa-assets`. `bg_sprite.png` has backdrop circles (44px) and badge circles (24px) — see `backend/image/aa_tree.py` for exact offsets.

`tree_type` is precomputed at build time (`aas.detect_tree_type`): `class`, `subclass`, `shadows`, `heroic`, `tradeskill`, `tradeskill_general`, `warder`, `prestige`, `dragon`, `reign_of_shadows`, `far_seas`, or `unknown`. The last six fall back to `render_subclass_tree` pending calibration. Coordinate systems are native 640×480 (SCALE=2 → 1280×960); full arithmetic is in `backend/image/aa_tree.py`. The `/aacheck` command offers five static tree choices (Class/Subclass/Shadows/Heroic/Trade) to avoid autocomplete API calls.

## Tooltip rendering notes

Quality tier colours, primary/secondary stat colours, stat ordering, and class-list collapsing are all in `backend/image/tooltip.py`. The config-driven `ITEM_DISPLAY` / `TYPEINFO_DISPLAY` dicts in `backend/census/constants.py` control which extra info rows appear (Type, Slot, Mitigation, Level, Charges, Duration, etc.).

## Files

| File | Purpose |
|---|---|
| `backend/image/tooltip.py` | PIL renderer for item tooltips. Renders at 2× then downsamples (SCALE=2, ZOOM=1.3). See file for colour/stat/ordering details. |
| `backend/image/aa_tree.py` | AA tree renderers and coordinate systems. See file for tree-type detection and coordinate arithmetic. |
