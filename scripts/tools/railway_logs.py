"""Read production (Railway) logs without flooding the caller.

Runs the Railway CLI (``railway logs --json``, read-only), keeps records at or
above ``--level``, collapses repeats into one line with a count, and caps the
output. Line format::

    HH:MM:SS LEVEL logger  message  (xN, last HH:MM:SS)

Examples:
    uv run --frozen python scripts/tools/railway_logs.py                               # warnings+ from the last 30m
    uv run --frozen python scripts/tools/railway_logs.py --since 6h --level error
    uv run --frozen python scripts/tools/railway_logs.py --since 2h --level info --grep "ratelimit|client-throttle"
    uv run --frozen python scripts/tools/railway_logs.py --since 1h --grep slow-request --raw --max 30
    uv run --frozen python scripts/tools/railway_logs.py --since 1d --sort count --max 15   # biggest offenders
    uv run --frozen python scripts/tools/railway_logs.py --build --latest              # newest deployment's build log

Notes:
  * ``--since`` / ``--until`` go straight to the CLI (server-side window): relative
    ``30s 5m 2h 1d 1w`` or an ISO-8601 timestamp. Logs come from one deployment
    (the latest successful one unless ``--latest``).
  * ``--level`` is applied here, not by the CLI: the app writes to stderr, so
    Railway tags every deploy line ``error`` and its ``@level`` filter is useless.
  * Times are UTC. Repeats are grouped after normalising numbers, hex ids, uuids,
    IPs, quoted strings and capitalised ``/api/...`` path segments.
  * The CLI's project link is per directory: it runs in the main git worktree
    unless ``--link-dir`` says otherwise. CLI location: ``$RAILWAY_CLI`` or PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_LEVEL_ALIASES = {"WARN": "WARNING", "ERR": "ERROR", "FATAL": "CRITICAL", "TRACE": "DEBUG", "NOTICE": "INFO"}
MAX_EXTRA_LINES = 30  # continuation (traceback) lines shown per record in --raw
MERGE_MIN_VARIANTS = 4  # this many near-identical templates fold into one "prefix ..." group
MERGE_PREFIX_WORDS = 3  # ...when they share level, logger and this many leading words
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_SINCE_RE = re.compile(r"\d+[smhdw]|\d{4}-\d\d-\d\d(?:[T ][\d:.]+)?(?:Z|[+-]\d\d:?\d\d)?")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
#: backend/core/logging_config.py _TEXT_FORMAT:
#:   "%(asctime)s.%(msecs)03d  %(levelname)-8s  [req=... user=... world=...]  %(message)s"
_APP_TEXT_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[.,]\d{3}\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"\[req=\S* user=\S* world=[^\]]*\]\s+(.*)$",
    re.DOTALL,
)
_STD_TEXT_RE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):\s+(.*)$", re.DOTALL)
_TAG_RE = re.compile(r"^\[([a-z0-9][a-z0-9_-]*)\]\s*")
_ERROR_HINT_RE = re.compile(r"Traceback \(most recent call last\)|\b\w*(?:Error|Exception)\b|\bFATAL\b|\bCRITICAL\b")

_NORMALISERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"(?<![\w.:])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"), "<ip>"),
    (re.compile(r"(?<![\w:.])(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{0,4}(?![\w:])", re.I), "<ip>"),
    # /api/guild/Dread Army/progression -> /api/guild/*/progression (names are capitalised, routes are not)
    (re.compile(r"(/api/(?:[a-z0-9_.-]+/)*)[A-Z][^/\n]*?(?=/[a-z]|\s+(?:took|exceeded|failed)\b|\s+\(|\s*$)"), r"\1*"),
    (re.compile(r'"[^"\n]{0,120}"'), '"*"'),
    (re.compile(r"(?<!\w)'[^'\n]{0,120}'(?!\w)"), "'*'"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=\d*[a-f])[0-9a-f]{8,}\b", re.I), "<hex>"),
    # numbers, except HTTP status codes ("status 503" must not merge with "status 200")
    (re.compile(r"(?<![\w.#])(?<!status )(?<!status=)(?<!HTTP )\d+(?:\.\d+)?"), "#"),
)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (
        re.compile(
            r"(?i)\b(authorization|x-lexicon-signature|access_token|refresh_token|id_token|client_secret|"
            r"session_secret|discord_token|api[_-]?key|password|passwd|secret|signature|token)\b"
            r"(\s*[=:]\s*)(['\"]?)[^\s'\",;&)]+"
        ),
        r"\1\2\3***",
    ),
    (re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@", re.I), r"\1:***@"),
    (re.compile(r"/s:[^/\s]+/"), "/s:***/"),
)


class EnvError(Exception):
    """Environment/usage problem: exit code 2."""


@dataclass
class Record:
    ts: datetime | None
    level: str
    logger: str
    message: str
    extra: list[str] = field(default_factory=list)


@dataclass
class Group:
    level: str
    logger: str
    template: str
    first: Record
    last_ts: datetime | None
    count: int = 1
    variants: int = 1  # >1 once merge_similar folded several templates into this group


# ── locating + running the CLI ────────────────────────────────────────────────


def find_cli(environ: Mapping[str, str] | None = None, which=None) -> str:
    env = os.environ if environ is None else environ
    which = which or shutil.which
    explicit = env.get("RAILWAY_CLI")
    if explicit:
        if Path(explicit).is_file() or which(explicit):
            return explicit
        raise EnvError(f"RAILWAY_CLI points at {explicit!r}, which does not exist")
    found = which("railway")
    if found:
        return found
    raise EnvError("Railway CLI not found: set RAILWAY_CLI to railway(.exe) or put `railway` on PATH")


def parse_worktree_list(porcelain: str) -> str | None:
    """First ``worktree <path>`` entry of ``git worktree list --porcelain`` = the main worktree."""
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree ") :].strip()
    return None


def main_worktree(root: Path) -> Path:
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return root
    first = parse_worktree_list(proc.stdout) if proc.returncode == 0 else None
    return Path(first) if first else root


def build_command(
    cli: str,
    *,
    since: str | None = None,
    until: str | None = None,
    fetch: int = 5000,
    build: bool = False,
    latest: bool = False,
) -> list[str]:
    """The one CLI call this tool ever makes: ``railway logs`` with real flags
    (--json, --lines, --since, --until, --build, --latest)."""
    for label, value in (("--since", since), ("--until", until)):
        if value is not None and not _SINCE_RE.fullmatch(value):
            raise EnvError(f"{label} {value!r}: use a relative time (30s, 5m, 2h, 1d, 1w) or an ISO-8601 timestamp")
    if fetch < 1:
        raise EnvError("--fetch must be at least 1")
    cmd = [cli, "logs", "--json", "--lines", str(fetch)]
    if since:
        cmd += ["--since", since]
    if until:
        cmd += ["--until", until]
    if build:
        cmd.append("--build")
    if latest:
        cmd.append("--latest")
    return cmd


def fetch_lines(cmd: Sequence[str], link_dir: Path, timeout: float = 90) -> list[str]:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=link_dir,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise EnvError(f"railway logs timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise EnvError(f"cannot run the Railway CLI: {exc.strerror or exc}") from exc
    if proc.returncode != 0:
        reason = next((ln.strip() for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()), "no output")
        raise EnvError(f"railway logs failed (exit {proc.returncode}, cwd {link_dir}): {redact(reason)[:300]}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ── parsing ───────────────────────────────────────────────────────────────────


def parse_ts(value: object) -> datetime | None:
    """Railway's RFC 3339 timestamps (nanosecond precision) -> aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace(" ", "T", 1)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def norm_level(value: object, default: str = "INFO") -> str:
    name = str(value or "").strip().upper()
    name = _LEVEL_ALIASES.get(name, name)
    return name if name in LEVELS else default


def split_tag(message: str) -> tuple[str, str]:
    """``[census-refresh] foo`` -> (census-refresh, foo); ``audit: x`` -> (audit, x)."""
    m = _TAG_RE.match(message)
    if m:
        return m.group(1), message[m.end() :]
    if message.startswith("audit: "):
        return "audit", message[len("audit: ") :]
    return "-", message


def guess_level(text: str) -> str:
    if _ERROR_HINT_RE.search(text):
        return "ERROR"
    return "WARNING" if re.search(r"\bwarn(?:ing)?\b", text, re.I) else "INFO"


def _clean(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r", "").strip("\n")


def parse_entry(entry: Mapping[str, object], *, trust_level: bool = False) -> tuple[Record, bool]:
    """One ``railway logs --json`` object -> (Record, structured). ``structured``
    is False for a line that carried no level of its own (a continuation
    candidate). ``trust_level``: believe the CLI's level (build logs)."""
    ts = parse_ts(entry.get("timestamp"))
    text = _clean(str(entry.get("message") or ""))
    if entry.get("logger"):  # Railway lifted the app's JSON record to the top level
        record = Record(ts, norm_level(entry.get("level")), str(entry["logger"]), text)
        if entry.get("exc"):
            record.extra = str(entry["exc"]).splitlines()
        return record, True
    if text.startswith("{"):  # LOG_FORMAT=json: {"ts","level","logger","message",...,"exc"}
        try:
            inner = json.loads(text)
        except ValueError:
            inner = None
        if isinstance(inner, dict) and "message" in inner:
            record = Record(
                ts or parse_ts(inner.get("ts")),
                norm_level(inner.get("level")),
                str(inner.get("logger") or "-"),
                str(inner["message"]),
            )
            if inner.get("exc"):
                record.extra = str(inner["exc"]).splitlines()
            return record, True
    m = _APP_TEXT_RE.match(text)
    if m:
        logger, message = split_tag(m.group(3))
        return Record(ts or parse_ts(m.group(1)), m.group(2), logger, message), True
    m = _STD_TEXT_RE.match(text)
    if m:
        logger, message = split_tag(m.group(2))
        return Record(ts, m.group(1), logger, message), True
    if trust_level:
        parts = [p.strip() for p in text.splitlines() if p.strip() and p.strip("=-# ")]
        return Record(ts, norm_level(entry.get("level")), "build", " | ".join(parts)), True
    return Record(ts, guess_level(text), "-", text), False


def parse_lines(lines: Iterable[str], *, build: bool = False) -> list[Record]:
    """CLI output -> records. A level-less line arriving within a second of a
    structured record is folded into it (tracebacks arrive one line per entry)."""
    records: list[Record] = []
    open_record: Record | None = None
    last_ts: datetime | None = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if not isinstance(entry, dict):
            entry = {"message": line}
        record, structured = parse_entry(entry, trust_level=build)
        if not record.message.strip() and not record.extra:
            continue
        if structured:
            records.append(record)
            open_record = None if build else record
        else:
            near = record.ts is None or last_ts is None or abs((record.ts - last_ts).total_seconds()) <= 1.0
            if open_record is not None and near:
                open_record.extra.extend(record.message.splitlines())
            else:
                open_record = None
                records.append(record)
        last_ts = record.ts or last_ts
    return records


# ── filtering + collapsing ────────────────────────────────────────────────────


def normalise(message: str) -> str:
    """Replace the variable parts of a message so repeats share one key."""
    first = message.splitlines()[0] if message else ""
    for pattern, replacement in _NORMALISERS:
        first = pattern.sub(replacement, first)
    return first


def redact(text: str) -> str:
    """Mask anything shaped like a credential before it is printed."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def select(records: Iterable[Record], min_level: str, grep: re.Pattern[str] | None = None) -> list[Record]:
    floor = LEVELS[min_level]
    out = []
    for r in records:
        if LEVELS[r.level] < floor:
            continue
        if grep and not grep.search(f"{r.level} {r.logger} {r.message}\n" + "\n".join(r.extra)):
            continue
        out.append(r)
    return out


def collapse(records: Iterable[Record]) -> list[Group]:
    groups: dict[tuple[str, str, str], Group] = {}
    for r in records:
        key = (r.level, r.logger, normalise(r.message))
        group = groups.get(key)
        if group is None:
            groups[key] = Group(r.level, r.logger, key[2], r, r.ts)
        else:
            group.count += 1
            group.last_ts = r.ts or group.last_ts
    return list(groups.values())


def merge_similar(groups: Sequence[Group], min_variants: int = MERGE_MIN_VARIANTS) -> list[Group]:
    """Fold groups that differ only in free text the normaliser cannot see
    (lists of names, exception details). Groups sharing level, logger and their
    first ``MERGE_PREFIX_WORDS`` words merge once there are ``min_variants`` of
    them; the merged template is their common word prefix followed by ``...``."""
    buckets: dict[tuple[str, str, tuple[str, ...]], list[Group]] = {}
    for g in groups:
        words = g.template.split()
        if len(words) >= MERGE_PREFIX_WORDS:
            buckets.setdefault((g.level, g.logger, tuple(words[:MERGE_PREFIX_WORDS])), []).append(g)
    merged: dict[int, Group | None] = {}
    for members in buckets.values():
        if len(members) < min_variants:
            continue
        split = [m.template.split() for m in members]
        common = 0
        while all(len(w) > common for w in split) and len({w[common] for w in split}) == 1:
            common += 1
        ordered = sorted(members, key=lambda m: (m.first.ts is None, m.first.ts or _EPOCH))
        stamps = [m.last_ts for m in members if m.last_ts]
        combined = Group(
            ordered[0].level,
            ordered[0].logger,
            " ".join(split[0][:common]) + " ...",
            ordered[0].first,
            max(stamps) if stamps else None,
            sum(m.count for m in members),
            sum(m.variants for m in members),
        )
        merged[id(members[0])] = combined
        merged.update({id(m): None for m in members[1:]})
    out = []
    for g in groups:
        replacement = merged.get(id(g), g)
        if replacement is not None:
            out.append(replacement)
    return out


# ── formatting ────────────────────────────────────────────────────────────────


def _clock(ts: datetime | None, with_date: bool) -> str:
    if ts is None:
        return "--:--:--"
    return ts.strftime("%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def _shorten(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if width <= 0 or len(text) <= width else text[: width - 4] + " ..."


def _tail_note(extra: Sequence[str]) -> str:
    tail = next((ln.strip() for ln in reversed(extra) if ln.strip()), "")
    return f"  [+{len(extra)} lines: {tail}]" if extra else ""


def format_group(group: Group, *, with_date: bool = False, width: int = 200) -> str:
    first = group.first
    body = group.template if group.count > 1 else first.message + _tail_note(first.extra)
    line = f"{_clock(first.ts, with_date)} {group.level:<7} {group.logger}  {_shorten(redact(body), width)}"
    if group.count > 1:
        variants = f", {group.variants} variants" if group.variants > 1 else ""
        line += f"  (x{group.count}{variants}, last {_clock(group.last_ts, with_date)})"
    return line


def format_record(record: Record, *, with_date: bool = False, width: int = 200) -> list[str]:
    """--raw: the record as logged, followed by its continuation lines (tail-capped)."""
    message = _shorten(redact(record.message), width)
    head = f"{_clock(record.ts, with_date)} {record.level:<7} {record.logger}  {message}"
    extra = [ln for ln in record.extra if ln.strip()]
    lines = [head]
    if len(extra) > MAX_EXTRA_LINES:
        lines.append(f"    ... ({len(extra) - MAX_EXTRA_LINES} lines omitted)")
        extra = extra[-MAX_EXTRA_LINES:]
    lines += [f"    {_shorten(redact(ln), width)}" for ln in extra]
    return lines


def needs_date(records: Sequence[Record], now: datetime | None = None) -> bool:
    days = {r.ts.date() for r in records if r.ts}
    today = (now or datetime.now(UTC)).date()
    return bool(days) and days != {today}


def render(
    records: Sequence[Record],
    *,
    raw: bool = False,
    sort: str = "time",
    max_lines: int = 80,
    width: int = 200,
    exact: bool = False,
    now: datetime | None = None,
) -> tuple[list[str], int]:
    """Output lines plus the number of groups (records in --raw) before capping."""
    with_date = needs_date(records, now)
    if raw:
        body = [line for r in records for line in format_record(r, with_date=with_date, width=width)]
        total = len(records)
        if max_lines > 0 and len(body) > max_lines:
            body = [f"... {len(body) - max_lines} earlier lines omitted (raise --max or narrow)", *body[-max_lines:]]
        return body, total
    groups = collapse(records)
    if not exact:
        groups = merge_similar(groups)
    total = len(groups)
    floor = _EPOCH
    if sort == "count":
        groups.sort(key=lambda g: (-g.count, -(g.last_ts or floor).timestamp()))
        kept, dropped = (groups[:max_lines], len(groups) - max_lines) if max_lines > 0 else (groups, 0)
        body = [format_group(g, with_date=with_date, width=width) for g in kept]
        if dropped > 0:
            body.append(f"... {dropped} smaller groups omitted (raise --max or narrow)")
        return body, total
    groups.sort(key=lambda g: g.last_ts or floor)
    kept, dropped = (groups[-max_lines:], len(groups) - max_lines) if max_lines > 0 else (groups, 0)
    body = [format_group(g, with_date=with_date, width=width) for g in kept]
    if dropped > 0:
        body.insert(0, f"... {dropped} older groups omitted (raise --max or narrow)")
    return body, total


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="railway_logs.py",
        description="Filtered, collapsed, capped view of Railway logs (read-only: only runs `railway logs`).",
        epilog=(
            "Level filtering and collapsing happen client-side; the time window is the CLI's own --since/--until. "
            "Times are UTC. Exit codes: 0 ok (even with no matching lines), 2 usage or environment error."
        ),
    )
    parser.add_argument(
        "--since",
        metavar="TIME",
        help="window start: 30s/5m/2h/1d/1w or ISO-8601 (default 30m; with --build: the whole build log)",
    )
    parser.add_argument("--until", metavar="TIME", help="window end, same formats (default: now)")
    parser.add_argument(
        "--level",
        choices=("debug", "info", "warning", "error"),
        help="minimum level (default: warning; debug with --build)",
    )
    parser.add_argument("--grep", metavar="REGEX", help="keep records matching this regex (case-insensitive)")
    parser.add_argument("--max", type=int, default=80, metavar="N", help="output line cap (default 80, 0 = no cap)")
    parser.add_argument("--sort", choices=("time", "count"), default="time", help="group order (default: time)")
    parser.add_argument("--raw", action="store_true", help="no collapsing: one line per record, tracebacks expanded")
    parser.add_argument(
        "--exact",
        action="store_true",
        help=f"only group exact repeats; do not fold {MERGE_MIN_VARIANTS}+ same-prefix variants into 'prefix ...'",
    )
    parser.add_argument("--build", action="store_true", help="build logs instead of deploy logs")
    parser.add_argument("--latest", action="store_true", help="newest deployment even if it failed or is building")
    parser.add_argument("--fetch", type=int, default=5000, metavar="N", help="max lines pulled from Railway (5000)")
    parser.add_argument("--width", type=int, default=200, metavar="N", help="max characters per message (200)")
    parser.add_argument("--link-dir", metavar="DIR", help="directory holding the Railway link (default: main worktree)")
    return parser


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=stream.encoding if stream.isatty() else "utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    try:
        grep = re.compile(args.grep, re.IGNORECASE) if args.grep else None
    except re.error as exc:
        print(f"railway_logs: bad --grep regex: {exc}", file=sys.stderr)
        return 2
    since = args.since or (None if args.build else "30m")
    try:
        cli = find_cli()
        cmd = build_command(cli, since=since, until=args.until, fetch=args.fetch, build=args.build, latest=args.latest)
        link_dir = Path(args.link_dir) if args.link_dir else main_worktree(root)
        if not link_dir.is_dir():
            raise EnvError(f"--link-dir {link_dir} is not a directory")
        lines = fetch_lines(cmd, link_dir)
    except EnvError as exc:
        print(f"railway_logs: {exc}", file=sys.stderr)
        return 2

    level = (args.level or ("debug" if args.build else "warning")).upper()
    records = parse_lines(lines, build=args.build)
    kept = select(records, level, grep)
    body, total = render(kept, raw=args.raw, sort=args.sort, max_lines=args.max, width=args.width, exact=args.exact)
    window = (f"since {since}" if since else "(last lines)") + (f" until {args.until}" if args.until else "")
    header = f"# {'build' if args.build else 'deploy'} logs {window}: {len(lines)} lines, {len(kept)} at >={level}" + (
        f" matching /{args.grep}/" if args.grep else ""
    )
    header += f", {total} records" if args.raw else f", {total} groups"
    if len(lines) >= args.fetch:
        header += f" [hit --fetch {args.fetch}: older lines not read]"
    print(header + " (UTC)")
    if body:
        print("\n".join(body))
    return 0


if __name__ == "__main__":
    sys.exit(main())
