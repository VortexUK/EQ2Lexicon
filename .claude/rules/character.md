---
paths:
  - "backend/server/api/aa.py"
  - "backend/server/api/aa_plans.py"
  - "backend/server/api/character/**"
  - "backend/server/db/aa_plans.*"
  - "frontend/src/pages/aaplanner/**"
---

# Character sheet: AAs, AA planner, gear sets, rankings tab

## Files

| File | Purpose |
|---|---|
| `backend/server/api/aa.py` | GET /api/character/{name}/aas — AA profile list with per-tree data |
| `backend/server/api/aa_plans.py` | AA planner saved builds: CRUD (owner-scoped in SQL) + read-only share by always-minted slug (`GET /api/aa/plan/{slug}`, page `/aa-plan/{slug}`). Store `backend/server/db/aa_plans.py` (users schema). Rule legality is the frontend engine's job (`frontend/src/pages/aaplanner/engine.ts` — point-cost-weighted self-exclusive thresholds, parent ranks, flat 100/tree cap, separate tradeskill pool, no-stranding removals); the server validates structurally. Planner UI = "Planner" mode on the character AA tab (`aaplanner/PlannerMode.tsx`, interactive AATree: click spend / right-click refund). |
| `backend/server/api/character/gear_sets.py` | GET /api/character/{name}/gear-sets — saved in-game equipment sets (Census `adventure_sets`), store-first SWR mirroring aa.py; feeds the character-sheet set pills + the compare per-side set picker. Each set carries `stat_deltas` (set − worn, additive stats + active item-set bonuses, from the items schema `item_stats`) computed in sibling `stat_deltas.py` — the sheet shows approximated stats when a set is selected |
| `backend/server/api/character/rankings.py` | GET /api/character/{name}/rankings — WCL-style per-boss summary over the /api/rankings kills dataset (shared 60s cache). Gated to curated the zones schema content only (same universe as the rankings dropdowns — heuristic-matched kills never surface); zone sections carry `expansion` and the response lists the character's `expansions` (newest first) for the tab's xpac dropdown. Class-scoped rank percentiles (best/median vs every same-class parse), All Stars points = 100×best/class-record + rank among class peers (per boss + zone total; peers get credit for bosses the target never killed). Both dps+hps in one response. `zones: []` ⇒ character page hides the Rankings tab (`CharacterRankingsTab.tsx`; percentileColors.ts scale). Fake local data: `scripts/dev/seed_fake_parses.py --character X` (tagged uploaded_by='fake-seed', `--wipe` to remove). |
