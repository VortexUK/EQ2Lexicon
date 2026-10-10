---
paths:
  - "backend/bot/cogs/spellcheck.py"
  - "backend/bot/cogs/guild.py"
  - "backend/eq2db/spells.py"
  - "backend/server/api/character/spells.py"
  - "data/spells/blocklist.json"
---

# Guild and spellcheck output rules, spell blocklist

## Guild and spellcheck command notes

**Guild**: members without a `type` dict are filtered out; rank resolved via `rank_list`; columns are Rank / Name / Class (Level) / AA / Tradeskill (Level) / Deity; sorted by rank ID asc then level desc; sent as `.txt` attachment if > 2000 chars.

**Spellcheck**: strips trailing Roman numerals, keeps highest-level entry per base name per type. Bot filter: `level > 0`, type in `(spells, arts)`, `given_by NOT IN (alternateadvancement, class)`. Web filter: same but `given_by == 'spellscroll'` (covers scribed mage spells and combat arts). Blocklist applied in both paths.

## Spell blocklist

`data/spells/blocklist.json` holds base spell names (no Roman numerals) to suppress:
```json
{ "blocked": ["Fighting Chance"] }
```
- `SpellCatalogue.load_blocklist()` in `backend/eq2db/spells.py` re-reads the file on every call
- Applied in both the web `/spells` endpoint and the Discord `/spellcheck` cog
- Add to `blocked` and the change takes effect without a restart
