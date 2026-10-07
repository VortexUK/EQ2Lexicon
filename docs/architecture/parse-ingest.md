# Parse ingest

Code: `backend/server/api/parses/ingest.py` (route + helpers),
`backend/server/api/parses/models.py` (Pydantic wire models),
`backend/server/parses/models.py` (typed dataclasses + coercion helpers),
`backend/server/parses/db.py` (`ParsesStore`), `backend/server/parses/pet_detection.py`.

`POST /api/parses/ingest` receives one finished encounter from the ACT plugin
(or EQ2Parser) as an ACT-shaped JSON payload: an encounter, its combatants, and
per-combatant damage-type and attack-type rollups. Upload integrity (HMAC, the
server allowlist, tamper reports, the plausibility gate) is covered in
[upload-integrity.md](upload-integrity.md); this page covers what happens to a
payload that is allowed in.

## Request flow

1. **Client flood gate** (`_client_flood_gate`, a route dependency) runs before
   body validation and auth, so a runaway client is rejected before it costs a
   Pydantic parse or a token lookup.
2. **Auth + HMAC** (`require_user_session_or_token`, `_validate_payload_signature`).
3. **Uploader name**: `logger_name` must be an EQ2 character-name shape (1-15
   letters). This keeps garbage out of Census URLs and blocks `:` injection
   into the `"name:world"` cache keys.
4. **Server allowlist**: `logger_server` is required, shape-checked and must be
   in `ALLOWED_SERVERS` (case-insensitive). It becomes the parse's `world`.
5. **Plausibility gate**: REJECT → 400, QUARANTINE → stored as a tamper report
   and answered with a normal-looking 201.
6. **Claim binding** (`_uploader_claimed`): the logger must be a character the
   token owner has an approved claim on for that world. Unverified uploads are
   kept for their uploader but get no guild attribution, no guild backfill, and
   never rank.
7. **Guild + snapshot lookup from cache/store only** (see below).
8. **Insert** in one transaction on the executor, then two optional background
   tasks after the response.

## The response path never waits on Census

The plugin's HttpClient times out after 20 seconds, and a single degraded
Census call can take most of that. Everything on the response path therefore
reads only the in-memory `character_cache` (any age — stale beats a live call)
and the durable `census_store`, which never deletes a character once resolved.

- **Uploader's guild** (`_resolve_uploader_guild_async`): cache, then store. A
  never-seen uploader returns the `CENSUS_UNAVAILABLE` sentinel (distinct from
  `None`, which means "genuinely unguilded"). The encounter commits with
  `guild_name = NULL` and `_backfill_encounter_guild` runs the live Census lookup
  as a background task (`allow_census=True`). When it learns a guild it
  fire-and-forgets a roster prewarm, so the rest of the raid hits the store.
- **Combatant snapshots** (`_cached_snapshots`): the same cache-then-store lookup
  for each player-like ally name. Whatever is known is frozen into the insert;
  the rest is filled by `_resolve_and_update_snapshots` in the background.

## Identity freeze

Each player combatant row stores the character's level, guild, class and item
level as they were at ingest (`CombatantSnapshot`). Later Census changes — a
guild move, a level-up, a class change on a recycled name — cannot rewrite
historical parses or rankings.

The background resolver (`_resolve_combatant_snapshots`) works per name:

1. `character_cache` hit → snapshot it.
2. `census_store` hit → snapshot the last-known data.
3. Never seen → one Census call to find the guild, then an *awaited* full roster
   prewarm that caches and persists every guildmate; re-check cache then store.
   Because a raid is overwhelmingly one guild, the first miss warms everyone else.
4. Item-level backfill: a roster resolve can have class and level but no
   equipment. Only then is one `get_character` made, written through to the store.

Names the resolver cannot resolve (pugs, Census errors) keep NULL identity.

Constraints on the resolver:

- Only allies with a valid single-word character-name shape are candidates, and
  at most `_MAX_SNAPSHOT_NAMES` (40) per upload. Each never-seen name costs a live
  Census call against the shared service ID, so fabricated names would otherwise
  be an amplification lever.
- A pooled DB connection is never held across a Census await. The sync pool has
  five slots; store work runs in short executor hops and a semaphore
  (`_RESOLVER_SEM`, 2) caps concurrent resolvers.
- It never raises — enrichment failure must not affect an accepted upload.

## Idempotency

An upload is keyed by `(act_encid, world)` in `ingest_log`; the same encid from
two servers is two encounters. A repeat upload returns `status="skipped"` and:

- **never un-hides a soft-deleted parse.** Soft-delete is the moderation action,
  and the uploader is the person most likely to re-send; restoring a hidden
  parse is an explicit action (`POST /parses/{id}/unhide`).
- **does not echo the existing encounter id.** With client-chosen encids that
  would be an oracle for which encounters exist.

## Payload shape rules

- Rows with an empty name / type are dropped; ACT's per-combatant `All`
  attack-type rollups are stripped.
- `damage_types` must be unique on `(combatant, damage_type)` and `attack_types`
  on `(combatant, swing_type, attack_name)`. Exact duplicates are collapsed;
  conflicting duplicates raise `DuplicatePayloadRows`, returned as a **422** that
  names the keys. A 500 would read as "server broke, retry", so client bugs must
  never surface as one.
- Per-row `dps` falls back to `encdps`, and crit percentage falls back to
  `crithits / hits`, for clients that send only one of the pair.
- Numbers are clamped to the Postgres bigint range and non-finite floats become
  0, so garbage degrades to a value the plausibility gate can judge instead of
  crashing the insert.
- `client_warnings` (soft plugin signals such as `folder_hint_mismatch`) is
  deduplicated, each entry truncated to 64 characters, and stored as JSON.

## Pet detection

ACT reports pets, mercenaries and players alike as allies, so "ally" is not
"player". `classify_combatants` (pure, no DB) assigns `combatants.is_player`,
which is the only player signal readers use: the parses list player count, the
parse detail Allies/Pets split, the rankings scope and the mirror-merge top-N
gate.

Stages, in order, for each ally:

1. Not an ally → omitted from the result (enemies keep `is_player` NULL).
2. Empty name or `Unknown` → pet.
3. Multi-word name → pet.
4. Matches the EQ2 auto-named-pet pattern, a known example, or a fixed-name
   deity pet (`FIXED_NAME_PETS`, kept in step with EQ2Parser's classifier) → pet.
5. Has a resolved class → player.
6. Otherwise unconfirmed, and **bucket-fill** decides: unconfirmed allies are
   promoted by contribution (encDPS + encHPS) up to a target of 24 for raid
   zones, 6 for dungeons, and for other zones nothing (≤ 6 allies), 6 (7-10) or
   24 (≥ 11). Raid-sized fights are also trimmed back down to 24 by demoting the
   lowest contributors.

Classification runs inside the insert transaction, again after the background
snapshot resolution fills in classes, and lazily on read for any encounter
that still has unclassified ally rows (see [parse-grouping.md](parse-grouping.md)).
