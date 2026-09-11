"""Raid-attendance category derivation — pure, no I/O.

Categories are derived at READ time (never stored) so role/claim/afk edits
after the fact stay correct. Inputs are gathered by the route from existing
registries:

  obs          attendance_observations rows for one session
  roles        raid_planning.get_roles → {char_lower: 'raider'|'raid_alt'}
               (placeholder raiders included — they are rostered like anyone)
  claims       raid_planning.claims_map → {char_lower: discord_id}
  afk_by_user  availability.statuses_for_day(session_day) → {discord_id: status}
  scheduled    the session's frozen scheduled flag

Per-character precedence: present > sat_out > afk > awol > absent.
Observed behaviour beats declaration (declared-afk-but-showed-up = present;
declared-afk-but-online = sat_out — they were demonstrably available). If
officers dislike that call, flipping afk above sat_out is a one-line reorder.

Per-user rollup: characters group by their claim owner; the user takes the
BEST category across their characters (a raid alt attending credits its
owner). Raid alts are never expected, so never AWOL.
"""

from __future__ import annotations

CATEGORY_ORDER = ["present", "sat_out", "afk", "awol", "absent"]


def resolve_mains(
    role_rows: list[dict],
    claims: dict[str, str],
    primaries: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Best-effort "raid main" resolution. Returns (user_mains, char_mains).

    A player's raid main is their claimed character rostered as 'raider' —
    preferring the primary claim when several qualify, else alphabetical.

    user_mains: {discord_id: main display name} (players with no rostered
                raider are absent — a pure-alt player has no main).
    char_mains: {char display name: main display name} — the parser's
                DKP-substitution table. Covers every rostered character
                (raiders map to themselves, rostered alts to their owner's
                main) AND every other claimed character of a player who has
                a main — so a second-account character dual-boxed into the
                raid still collapses onto the same main instead of
                double-dipping DKP.
    """
    display = {r["character_name"].lower(): r["character_name"] for r in role_rows}
    roles = {r["character_name"].lower(): r["role"] for r in role_rows}

    candidates: dict[str, list[str]] = {}
    for lower, role in roles.items():
        if role == "raider" and lower in claims:
            candidates.setdefault(claims[lower], []).append(lower)

    user_mains: dict[str, str] = {}
    for uid, lowers in candidates.items():
        primary = sorted(lo for lo in lowers if lo in primaries)
        user_mains[uid] = display[(primary or sorted(lowers))[0]]

    char_mains: dict[str, str] = {}
    for lower, role in roles.items():
        owner = claims.get(lower)
        if role == "raid_alt" and owner is not None and owner in user_mains:
            char_mains[display[lower]] = user_mains[owner]
        else:
            char_mains[display[lower]] = display[lower]
    # Unrostered claims of players WITH a main (any account — claims are
    # Discord-scoped): map to the main so raid appearances credit it.
    for lower, owner in claims.items():
        if lower not in roles and owner in user_mains:
            char_mains[lower.capitalize()] = user_mains[owner]
    return user_mains, char_mains


def derive_categories(
    obs: list[dict],
    roles: dict[str, str],
    claims: dict[str, str],
    afk_by_user: dict[str, str],
    scheduled: bool,
    user_mains: dict[str, str] | None = None,
    overrides: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Returns (char_rows, user_rows).

    char_rows: {name, role, category, first_seen, last_seen,
                owner_discord_id, overridden}
    user_rows: {discord_id, category, afk_declared, characters: [names],
                main: raid-main display name or None (see resolve_mains),
                in_voice}

    ``overrides`` ({char_lower: {character_name, category, ...}} from
    attendance_overrides) beats every derived category — officer corrections
    are the last word. Overridden names join the universe even when never
    observed (the "add a missed raider" case).
    """
    raid_obs = {o["character_name"]: o for o in obs if o["kind"] == "raid"}
    online_obs = {o["character_name"]: o for o in obs if o["kind"] == "online"}
    # kind='voice' rows carry DISCORD IDS in character_name (Phase 3 bot).
    # They never enter the character universe — they only flag the player.
    voice_ids = {o["character_name"] for o in obs if o["kind"] == "voice"}

    # Universe: everyone observed + every rostered character (total no-shows
    # must surface for AWOL).
    names: dict[str, str] = {}  # lower -> display casing (observed wins)
    for o in obs:
        if o["kind"] in ("raid", "online"):
            names.setdefault(o["character_name"].lower(), o["character_name"])
    for lower in roles:
        names.setdefault(lower, lower.capitalize())
    for lower, ov in (overrides or {}).items():
        names.setdefault(lower, ov.get("character_name") or lower.capitalize())

    char_rows: list[dict] = []
    for lower, display in names.items():
        role = roles.get(lower)
        owner = claims.get(lower)
        in_raid = display in raid_obs or any(k.lower() == lower for k in raid_obs)
        online = display in online_obs or any(k.lower() == lower for k in online_obs)
        declared_afk = owner is not None and afk_by_user.get(owner) == "afk"

        if in_raid:
            category = "present"
        elif online and role is not None:
            category = "sat_out"
        elif declared_afk and role is not None:
            category = "afk"
        elif scheduled and role == "raider":
            category = "awol"
        else:
            category = "absent"

        override = (overrides or {}).get(lower)
        if override is not None:
            category = override["category"]

        o = raid_obs.get(display) or online_obs.get(display)
        char_rows.append(
            {
                "name": display,
                "role": role,
                "category": category,
                "first_seen": o["first_seen"] if o else None,
                "last_seen": o["last_seen"] if o else None,
                "owner_discord_id": owner,
                "overridden": override is not None,
            }
        )

    # Per-user rollup: best category across the user's characters.
    by_user: dict[str, dict] = {}
    for row in char_rows:
        owner = row["owner_discord_id"]
        if owner is None:
            continue
        entry = by_user.setdefault(
            owner,
            {
                "discord_id": owner,
                "category": "absent",
                "afk_declared": afk_by_user.get(owner) == "afk",
                "characters": [],
                "main": (user_mains or {}).get(owner),
                "in_voice": owner in voice_ids,
            },
        )
        entry["characters"].append(row["name"])
        if CATEGORY_ORDER.index(row["category"]) < CATEGORY_ORDER.index(entry["category"]):
            entry["category"] = row["category"]

    char_rows.sort(key=lambda r: (CATEGORY_ORDER.index(r["category"]), r["name"].lower()))
    user_rows = sorted(by_user.values(), key=lambda r: (CATEGORY_ORDER.index(r["category"]), r["discord_id"]))
    return char_rows, user_rows


def session_counts(char_rows: list[dict]) -> dict[str, int]:
    counts = {c: 0 for c in CATEGORY_ORDER}
    for row in char_rows:
        counts[row["category"]] += 1
    return {"present": counts["present"], "sat_out": counts["sat_out"], "afk": counts["afk"], "awol": counts["awol"]}


def summarize_attendance(per_session: list[tuple[int, list[dict], list[dict]]]) -> list[dict]:
    """Cross-session rollup for the summary matrix (one row per player,
    one cell per session). ``per_session`` is [(session_id, char_rows,
    user_rows)] straight from :func:`derive_categories`.

    Row identity: claimed characters credit their OWNER (one row per
    player, best category per session, combined first/last-seen window
    across their characters); unclaimed characters stand as their own
    row. Rows absent in every supplied session are dropped.

    Attendance % counts present + sat_out over ALL supplied sessions —
    a benched raider showed up (and banks sit-out DKP), so the bench is
    attendance. Sessions where a row has no cell at all count against
    the percentage exactly like an absent cell.
    """
    rows: dict[str, dict] = {}

    def entry(key: str, name: str | None, discord_id: str | None = None) -> dict:
        return rows.setdefault(
            key,
            {
                "key": key,
                "discord_id": discord_id,
                "name": name,
                "cells": {},
                "counts": {c: 0 for c in CATEGORY_ORDER},
            },
        )

    n = len(per_session)
    for sid, char_rows, user_rows in per_session:
        by_owner: dict[str, list[dict]] = {}
        for c in char_rows:
            if c["owner_discord_id"] is not None:
                by_owner.setdefault(c["owner_discord_id"], []).append(c)

        for u in user_rows:
            e = entry(f"u:{u['discord_id']}", u["main"], u["discord_id"])
            if u["main"]:
                e["name"] = u["main"]
            chars = [c for c in by_owner.get(u["discord_id"], []) if c["category"] != "absent"]
            firsts = [c["first_seen"] for c in chars if c["first_seen"] is not None]
            lasts = [c["last_seen"] for c in chars if c["last_seen"] is not None]
            e["cells"][sid] = {
                "category": u["category"],
                "first_seen": min(firsts) if firsts else None,
                "last_seen": max(lasts) if lasts else None,
                "characters": sorted(c["name"] for c in chars),
            }

        for c in char_rows:
            if c["owner_discord_id"] is None and c["category"] != "absent":
                e = entry(f"c:{c['name'].lower()}", c["name"])
                e["cells"][sid] = {
                    "category": c["category"],
                    "first_seen": c["first_seen"],
                    "last_seen": c["last_seen"],
                    "characters": [c["name"]],
                }

    out = []
    for e in rows.values():
        cats = [cell["category"] for cell in e["cells"].values()]
        if all(c == "absent" for c in cats):
            continue
        for c in cats:
            e["counts"][c] += 1
        attended = e["counts"]["present"] + e["counts"]["sat_out"]
        e["attended"] = attended
        e["pct"] = round(100 * attended / n) if n else 0
        out.append(e)
    out.sort(key=lambda r: (-r["pct"], (r["name"] or "").lower()))
    return out
