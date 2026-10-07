"""Raid-attendance category derivation — pure, no I/O.

Categories are derived at read time, never stored, so later role/claim/afk
edits stay correct. Per-character precedence: present > sat_out > afk > awol
> absent (observed behaviour beats a declared AFK). A user takes the best
category across their claimed characters; raid alts are never AWOL.
"""

from __future__ import annotations

CATEGORY_ORDER = ["present", "sat_out", "afk", "awol", "absent"]

#: The timed states a character can move through WITHIN a session. AWOL and
#: absent are whole-session judgements — they never carry a time range.
SEGMENT_CATEGORIES = ("present", "sat_out", "afk")

#: A derived lead/tail sat_out period shorter than this is login noise
#: (everyone is online a few minutes before the pull), not a real bench.
MIN_DERIVED_SPLIT_S = 10 * 60


def _derived_segments(raid_o: dict | None, online_o: dict | None, role: str | None) -> list[dict]:
    """A character's timeline as the observations tell it: the raid window is
    present; rostered characters' online time OUTSIDE the raid window is the
    bench (sat_out), when long enough to mean something."""
    segs: list[dict] = []
    if raid_o is not None:
        if (
            online_o is not None
            and role is not None
            and raid_o["first_seen"] - online_o["first_seen"] >= MIN_DERIVED_SPLIT_S
        ):
            segs.append({"category": "sat_out", "started_at": online_o["first_seen"], "ended_at": raid_o["first_seen"]})
        segs.append({"category": "present", "started_at": raid_o["first_seen"], "ended_at": raid_o["last_seen"]})
        if (
            online_o is not None
            and role is not None
            and online_o["last_seen"] - raid_o["last_seen"] >= MIN_DERIVED_SPLIT_S
        ):
            segs.append({"category": "sat_out", "started_at": raid_o["last_seen"], "ended_at": online_o["last_seen"]})
    elif online_o is not None and role is not None:
        segs.append({"category": "sat_out", "started_at": online_o["first_seen"], "ended_at": online_o["last_seen"]})
    return segs


def _clamp_segments(segs: list[dict], window: tuple[int, int] | None) -> list[dict]:
    """Clip DERIVED segments to the session window: an overnight parser's
    online rows can run hours past the raid, and fixing the session window
    must fix every derived timeline with it. Manual segments are never
    clamped — the officer's word stands as written."""
    if window is None:
        return segs
    lo, hi = window
    out = []
    for s in segs:
        a, b = max(s["started_at"], lo), min(s["ended_at"], hi)
        if a < b:
            out.append({**s, "started_at": a, "ended_at": b})
    return out


def resolve_mains(
    role_rows: list[dict],
    claims: dict[str, str],
    primaries: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Best-effort "raid main" resolution. Returns (user_mains, char_mains).

    A player's raid main is their claimed 'raider' (primary claim first, else
    alphabetical), falling back to their primary claim.

    user_mains: {discord_id: main display name}.
    char_mains: {char display name: main display name} — the parser's
                DKP-substitution table: every rostered character plus every
                other claimed character of a player with a main, so a
                dual-boxed second-account character cannot double-dip DKP.
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
    # Primary-claim fallback for players with no rostered raider (sorted so
    # two primaries for one player — shouldn't happen — stay deterministic).
    for lower in sorted(primaries):
        uid = claims.get(lower)
        if uid is not None and uid not in user_mains:
            user_mains[uid] = display.get(lower, lower.capitalize())

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
    afk_by_char: dict[str, str] | None = None,
    segments_by_char: dict[str, list[dict]] | None = None,
    window: tuple[int, int] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Returns (char_rows, user_rows).

    char_rows: {name, role, category, first_seen, last_seen,
                owner_discord_id, overridden, segments, manual_timeline}
    user_rows: {discord_id, category, afk_declared, characters: [names],
                main: raid-main display name or None (see resolve_mains),
                in_voice}

    ``overrides`` (attendance_overrides) beat every derived category, and
    overridden names join the universe even when never observed.
    ``segments_by_char`` (attendance_segments) REPLACES a character's derived
    timeline; without an override their category is the best segment state.
    ``window`` (session started_at/ended_at) clips derived segments and
    fallback seen-times; manual segments are never clipped.
    """
    raid_obs = {o["character_name"]: o for o in obs if o["kind"] == "raid"}
    online_obs = {o["character_name"]: o for o in obs if o["kind"] == "online"}
    # kind='voice' rows carry DISCORD IDS in character_name.
    # They never enter the character universe — they only flag the player.
    voice_ids = {o["character_name"] for o in obs if o["kind"] == "voice"}

    # Universe: everyone observed + every rostered character (raider
    # no-shows must surface for AWOL; unseen non-raiders are filtered
    # back out below — they carry no signal and their rows can't be
    # deleted, having no observations behind them).
    names: dict[str, str] = {}  # lower -> display casing (observed wins)
    for o in obs:
        if o["kind"] in ("raid", "online"):
            names.setdefault(o["character_name"].lower(), o["character_name"])
    for lower in roles:
        names.setdefault(lower, lower.capitalize())
    for lower, ov in (overrides or {}).items():
        names.setdefault(lower, ov.get("character_name") or lower.capitalize())
    for lower, segs in (segments_by_char or {}).items():
        if segs:
            names.setdefault(lower, segs[0].get("character_name") or lower.capitalize())

    char_rows: list[dict] = []
    for lower, display in names.items():
        role = roles.get(lower)
        owner = claims.get(lower)
        raid_o = raid_obs.get(display) or next((v for k, v in raid_obs.items() if k.lower() == lower), None)
        online_o = online_obs.get(display) or next((v for k, v in online_obs.items() if k.lower() == lower), None)
        in_raid = raid_o is not None
        online = online_o is not None
        # Per-character availability verdict: routes pass afk_by_char as
        # the NEWEST-EDIT-WINS merge of officer character entries and the
        # owner's own calendar (see availability.merge_availability). Where
        # a per-character verdict exists it decides alone — an explicit
        # 'available'/'tentative' must not be trumped by a stale owner-level
        # AFK. Characters with no entry fall back to their owner's calendar.
        char_status = (afk_by_char or {}).get(lower)
        if char_status is not None:
            declared_afk = char_status == "afk"
        else:
            declared_afk = owner is not None and afk_by_user.get(owner) == "afk"

        manual_segs = (segments_by_char or {}).get(lower) or []
        segs = (
            [{"category": s["category"], "started_at": s["started_at"], "ended_at": s["ended_at"]} for s in manual_segs]
            if manual_segs
            else _clamp_segments(_derived_segments(raid_o, online_o, role), window)
        )

        if manual_segs:
            # The officer wrote the timeline — the best state in it is the label.
            category = min((s["category"] for s in segs), key=CATEGORY_ORDER.index)
        elif in_raid:
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

        # Drop never-observed, never-corrected rostered NON-RAIDERS: the row
        # has no observations for the officer ✕ to delete, so it would
        # respawn from the roles table on every render. Raiders keep their
        # no-show row — that IS the AWOL/absent signal.
        if (
            category == "absent"
            and override is None
            and raid_o is None
            and online_o is None
            and not manual_segs
            and role != "raider"
        ):
            continue
        if segs:
            first_seen: int | None = min(s["started_at"] for s in segs)
            last_seen: int | None = max(s["ended_at"] for s in segs)
        else:
            o = raid_o or online_o
            first_seen = o["first_seen"] if o else None
            last_seen = o["last_seen"] if o else None
            if first_seen is not None and last_seen is not None and window is not None:
                first_seen = max(first_seen, window[0])
                last_seen = min(last_seen, window[1])
                if first_seen >= last_seen:
                    first_seen = last_seen = None
        char_rows.append(
            {
                "name": display,
                "role": role,
                "category": category,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "owner_discord_id": owner,
                "overridden": override is not None,
                "segments": segs,
                "manual_timeline": bool(manual_segs),
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

    Claimed characters credit their owner (one row per player); unclaimed
    characters are their own row; rows absent in every session are dropped.
    Attendance percent = (present + sat_out) / ALL supplied sessions — a
    missing cell counts like an absent one.
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
