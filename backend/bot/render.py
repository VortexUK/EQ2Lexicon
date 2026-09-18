"""Pure text rendering for bot commands — no discord import, fully testable.

The monospace-table idioms (column fitting, ljust/rjust rows, ─ rules) and
the three response builders live here; the "wrap in a code fence or attach
as a file" decision is the pure ``plan_code_block`` -> ``SendPlan`` half of
what backend/bot/messaging.py sends. Cogs are thin Discord adapters over
these functions.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from backend.census.constants import SPELL_TIER_ORDER
from backend.census.models import CharacterSpells, GuildData, GuildMember, SpellEntry
from backend.eq2db.spells import catalogue as _spells

COL_SEP = "  "

#: Discord's hard message-length cap.
MESSAGE_LIMIT = 2000


def truncate(s: str, width: int) -> str:
    """Hard-cap a cell to ``width`` characters, eliding with ``…``."""
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def fit_width(header: str, values: list[str], max_width: int) -> int:
    """Column width: widest of header/data, capped at ``max_width``."""
    data_w = max((len(v) for v in values), default=0)
    return min(max(len(header), data_w), max_width)


def format_row(values: list[str], widths: list[int]) -> str:
    return COL_SEP.join(v.ljust(widths[i]) for i, v in enumerate(values))


def rule(widths: list[int]) -> str:
    return COL_SEP.join("─" * w for w in widths)


# ---------------------------------------------------------------------------
# Send planning — the pure half of "message or file attachment?"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SendPlan:
    """What a cog should send: either ``content`` alone (code-fenced text)
    or ``content`` (an optional header line) + ``file_text`` attached as
    ``filename``."""

    content: str | None
    file_text: str | None
    filename: str


def plan_code_block(text: str, *, filename: str, file_header: str | None = None) -> SendPlan:
    """Fence ``text`` when it fits in one Discord message; otherwise plan a
    file attachment with ``file_header`` as the message body."""
    wrapped = f"```\n{text}\n```"
    if len(wrapped) <= MESSAGE_LIMIT:
        return SendPlan(content=wrapped, file_text=None, filename=filename)
    return SendPlan(content=file_header, file_text=text, filename=filename)


# ---------------------------------------------------------------------------
# /guild
# ---------------------------------------------------------------------------


def build_guild_table(data: GuildData) -> str:
    members = sorted(
        data.members,
        key=lambda m: (m.rank_id if m.rank_id is not None else 9999, -(m.level or 0)),
    )

    def _cls(m: GuildMember) -> str:
        if m.cls and m.level is not None:
            return f"{m.cls} ({m.level})"
        return m.cls or "—"

    def _ts(m: GuildMember) -> str:
        ts = m.ts_class.capitalize() if m.ts_class else None
        if ts and m.ts_level is not None:
            return f"{ts} ({m.ts_level})"
        return ts or "—"

    cols: list[tuple[str, Callable[[GuildMember], str], int]] = [
        ("Rank", lambda m: m.rank or "—", 16),
        ("Name", lambda m: m.name, 22),
        ("Class", _cls, 24),
        ("AA", lambda m: str(m.aa_level) if m.aa_level is not None else "—", 4),
        ("Tradeskill", _ts, 24),
        ("Deity", lambda m: m.deity or "—", 16),
    ]

    widths = [fit_width(header, [fn(m) for m in members], max_w) for header, fn, max_w in cols]

    lines = [
        f"{data.name}  —  {data.world}  ({len(members)} members with data)",
        "",
        format_row([h for h, _, _ in cols], widths),
        rule(widths),
    ]
    for m in members:
        lines.append(format_row([truncate(fn(m), widths[i]) for i, (_, fn, _) in enumerate(cols)], widths))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /spellcheck
# ---------------------------------------------------------------------------


def apply_blocklist(entries: list[SpellEntry]) -> list[SpellEntry]:
    blocklist = _spells.load_blocklist()
    if not blocklist:
        return entries
    return [e for e in entries if _spells.strip_roman(e.name).lower() not in blocklist]


def build_spell_details(data: CharacterSpells) -> str:
    entries = _spells.unique_highest_entries(apply_blocklist(data.entries))
    tier_order = {t: i for i, t in enumerate(SPELL_TIER_ORDER)}
    entries.sort(key=lambda e: (tier_order.get(e.tier, 99), e.level, e.name))

    name_w = max(len("Spell"), max((len(e.name) for e in entries), default=0))
    name_w = min(name_w, 50)

    def _row(name: str, level: object, tier: str) -> str:
        return f"{name:<{name_w}}  {str(level):>3}  {tier}"

    sep = "─" * (name_w + 2 + 3 + 2 + max(len(t) for t in SPELL_TIER_ORDER))
    lines = [
        f"{data.character_name} — All Spells & Arts",
        "",
        _row("Spell", "Lvl", "Tier"),
        sep,
    ]
    current_tier = None
    for e in entries:
        if e.tier != current_tier:
            if current_tier is not None:
                lines.append("")
            current_tier = e.tier
        lines.append(_row(e.name[:name_w], e.level, e.tier))

    lines.append(f"\n{len(entries)} unique spells/arts")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /afksummary
# ---------------------------------------------------------------------------


def build_afk_summary(guild_name: str, entries: list[dict]) -> str:
    """Upcoming raid days with who has declared AFK on each.

    ``entries``: [{label, names: [str]}] in date order — assembled by the
    cog. Days with no declarations show a middot placeholder."""
    label_w = fit_width("Raid day", [e["label"] for e in entries], 16)
    lines = [
        f"{guild_name} — declared AFK, next {len(entries)} raid night(s)",
        "",
    ]
    for e in entries:
        names = ", ".join(sorted(e["names"], key=str.lower)) if e["names"] else "·"
        lines.append(f"{e['label'].ljust(label_w)}{COL_SEP}{names}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /attendance
# ---------------------------------------------------------------------------


def build_attendance_summary(guild_name: str, entries: list[dict], session_count: int) -> str:
    """Per-player attendance table over the summarised sessions.

    ``entries``: {name, present, sat_out, afk, awol} per player — pre-
    aggregated by the cog. Sorted here: attendance desc, then name."""
    rows = sorted(entries, key=lambda e: (-e["present"], e["name"].lower()))

    headers = ["Player", "Present", "Sat out", "AFK", "AWOL", "Att%"]
    cells = [
        [
            e["name"],
            str(e["present"]),
            str(e["sat_out"]),
            str(e["afk"]),
            str(e["awol"]),
            f"{round(100 * e['present'] / session_count)}%" if session_count else "—",
        ]
        for e in rows
    ]
    widths = [fit_width(headers[i], [row[i] for row in cells], 24 if i == 0 else 8) for i in range(len(headers))]

    lines = [
        f"{guild_name} — attendance over the last {session_count} raid night(s)",
        "",
        format_row(headers, widths),
        rule(widths),
    ]
    for row in cells:
        lines.append(format_row([truncate(v, widths[i]) for i, v in enumerate(row)], widths))
    return "\n".join(lines)


def build_spell_summary(data: CharacterSpells) -> str:
    entries = _spells.unique_highest_entries(apply_blocklist(data.entries))

    count: Counter[str] = Counter(e.tier for e in entries)
    all_tiers = [t for t in SPELL_TIER_ORDER if count[t]]

    tier_w = max(len("Tier"), max((len(t) for t in all_tiers), default=0))
    count_w = max(len("Count"), max((len(str(count[t])) for t in all_tiers), default=0))

    def _row(tier: str, n: object) -> str:
        return tier.ljust(tier_w) + COL_SEP + str(n).rjust(count_w)

    sep = "─" * (tier_w + count_w + len(COL_SEP))
    lines = [
        f"{data.character_name} — Spell Summary",
        "",
        _row("Tier", "Count"),
        sep,
    ]
    for tier in all_tiers:
        lines.append(_row(tier, count[tier]))

    lines += [sep, _row("Total", sum(count.values()))]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parse posts — the pure half of the parse-posting cog's embeds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsePost:
    """Everything a Discord embed needs, with no discord import: the cog
    maps this 1:1 onto discord.Embed."""

    title: str
    url: str | None
    color: int
    description: str
    fields: list[tuple[str, str, bool]]  # (name, value, inline)
    footer: str


#: Embed accent colours (kill / wipe / unknown-mixed).
_COLOR_KILL = 0x2EA043
_COLOR_WIPE = 0xD64545
_COLOR_NEUTRAL = 0xC9A227


def fmt_compact(n: float) -> str:
    """1234567 → '1.23M' — the site's compact number style for embeds."""
    n = float(n or 0)
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= scale:
            v = n / scale
            digits = 0 if v >= 100 else 1 if v >= 10 else 2
            return f"{v:.{digits}f}{suffix}"
    return f"{n:.0f}"


def _fmt_fight_duration(seconds: int) -> str:
    m, s = divmod(max(int(seconds), 0), 60)
    return f"{m}m {s:02d}s"


def _top_lines(players: list[dict], key: str, limit: int = 5) -> str:
    ranked = sorted((p for p in players if (p.get(key) or 0) > 0), key=lambda p: p[key], reverse=True)[:limit]
    lines = []
    for i, p in enumerate(ranked, start=1):
        cls = f" · {p['cls']}" if p.get("cls") else ""
        lines.append(f"`{fmt_compact(p[key]):>7}` {i}. **{p.get('name', '?')}**{cls}")
    return "\n".join(lines) or "—"


def build_parse_post(fight: dict, players: list[dict], url: str | None) -> ParsePost:
    """One boss fight → one clean embed: outcome + duration in the title,
    raid-wide DPS/HPS up top, top-5 DPS and HPS side by side.

    ``fight`` is a mirror-grouped encounter dict (canonical upload fields +
    ``uploads``); ``players`` its ally player combatants."""
    success = fight.get("success_level") or 0
    emoji, color = (
        ("✅", _COLOR_KILL) if success == 1 else ("💀", _COLOR_WIPE) if success == 2 else ("⚔️", _COLOR_NEUTRAL)
    )
    wipe = " (wipe)" if success == 2 else ""
    title = f"{emoji} {fight.get('title') or 'Unknown encounter'} — {_fmt_fight_duration(fight.get('duration_s') or 0)}{wipe}"

    raid_dps = sum(p.get("encdps") or 0 for p in players)
    raid_hps = sum(p.get("enchps") or 0 for p in players)
    where = f"{fight['zone']} · " if fight.get("zone") else ""
    description = (
        f"{where}{len(players)} players\n**Raid DPS** `{fmt_compact(raid_dps)}` **Raid HPS** `{fmt_compact(raid_hps)}`"
    )

    fields = [
        ("Top DPS", _top_lines(players, "encdps"), True),
        ("Top HPS", _top_lines(players, "enchps"), True),
    ]
    uploads = len(fight.get("uploads") or ()) or 1
    footer = f"EQ2Lexicon · {uploads} upload{'s' if uploads != 1 else ''}"
    return ParsePost(title=title, url=url, color=color, description=description, fields=fields, footer=footer)
