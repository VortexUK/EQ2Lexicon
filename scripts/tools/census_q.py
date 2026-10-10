"""One Daybreak Census query with a narrow projection and compact output.

``--show`` is required (unless ``--raw`` / ``--count``): an unprojected Census
document runs to tens of kilobytes, which is what this script exists to avoid.
Output: a ``returned=N`` line echoing the request (service id masked as
``s:***``), then one compact JSON line per result. Nested lists are cut at 5
items, long strings truncated, and the whole output capped at ``--max-chars``.
Exit 0 = rows returned, 1 = no rows or a Census/network error, 2 = usage error.

Examples:
    uv run --frozen python scripts/tools/census_q.py character name.first=Menludiir locationdata.world=Wuoshi \
        --show name.first,type.level,type.class
    uv run --frozen python scripts/tools/census_q.py guild name=Halcyon world=Wuoshi --show name,level,members
    uv run --frozen python scripts/tools/census_q.py item "displayname=^Mark of" --show displayname,tier --limit 3
    uv run --frozen python scripts/tools/census_q.py character locationdata.world=Wuoshi type.classid=13 \
        --show name.first,stats.health.max --sort stats.health.max:-1 --limit 5
    uv run --frozen python scripts/tools/census_q.py character locationdata.world=Wuoshi "type.level=>60" --count
    uv run --frozen python scripts/tools/census_q.py spell id=123456 --raw --max-chars 1500

Filters are ``key=value``; the value may start with a Census operator
(``^`` starts-with, ``*`` contains, ``<`` ``>`` ``[`` ``]`` ``!``) - quote those
for the shell. Keys starting with ``c:`` pass through (``c:case=false``).
The service id comes from ``CENSUS_SERVICE_ID`` (environment, then ``.env`` in
this worktree, then the main worktree), like the app; it is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://census.daybreakgames.com"  # backend/census/config.py CENSUS_BASE_URL
DEFAULT_SERVICE_ID = "example"  # the app's default: public and rate-limited

MAX_LIST_ITEMS = 5
MAX_STR_CHARS = 200
RETRY_PAUSE_S = 1.5
#: Census operators and structure characters left readable in the echoed URL.
_SAFE = ":,()^*<>[]!./'"
_COLLECTION_RE = re.compile(r"[a-z][a-z0-9_]*")
_SERVICE_RE = re.compile(r"/s:[^/\s]+")

HttpGet = Callable[[str, float], tuple[int, str]]


class UsageError(Exception):
    """Bad arguments: exit code 2."""


class CensusError(Exception):
    """Census or network failure: exit code 1."""


# ── request building ──────────────────────────────────────────────────────────


def parse_filters(pairs: Sequence[str]) -> list[tuple[str, str]]:
    """``["name.first=Foo", "type.level=>50"]`` -> [(key, value)]; split at the first ``=``."""
    out = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise UsageError(f"filter {pair!r} is not key=value")
        out.append((key.strip(), value))
    return out


def build_url(
    service_id: str,
    collection: str,
    filters: Sequence[tuple[str, str]] = (),
    *,
    show: str | None = None,
    limit: int | None = None,
    sort: str | None = None,
    resolve: str | None = None,
    verb: str = "get",
) -> str:
    """The Census REST URL: ``/s:<id>/json/<verb>/eq2/<collection>/?filters&c:show=...``."""
    if not _COLLECTION_RE.fullmatch(collection):
        raise UsageError(f"collection {collection!r} must be a lowercase Census collection name (character, item, ...)")
    params = list(filters)
    if show:
        params.append(("c:show", ",".join(part.strip() for part in show.split(",") if part.strip())))
    if resolve:
        params.append(("c:resolve", resolve))
    if sort:
        params.append(("c:sort", sort))
    if limit is not None:
        params.append(("c:limit", str(limit)))
    query = "&".join(f"{quote(k, safe=_SAFE)}={quote(v, safe=_SAFE)}" for k, v in params)
    return f"{BASE_URL}/s:{quote(service_id, safe='')}/json/{verb}/eq2/{collection}/" + (f"?{query}" if query else "")


def mask(text: str) -> str:
    """Hide the service id wherever a Census URL appears (``/s:<id>`` -> ``/s:***``)."""
    return _SERVICE_RE.sub("/s:***", text)


# ── service id ────────────────────────────────────────────────────────────────


def _dotenv_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    try:
        from dotenv import dotenv_values

        return dotenv_values(path).get(key) or None
    except ImportError:  # minimal parser: KEY=value, optional quotes
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == key:
                return value.strip().strip("'\"") or None
    return None


def main_worktree(root: Path) -> Path | None:
    """First entry of ``git worktree list --porcelain`` (the main checkout, where ``.env`` lives)."""
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
        return None
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        if line.startswith("worktree "):
            return Path(line[len("worktree ") :].strip())
    return None


def resolve_service_id(environ: Mapping[str, str], env_files: Sequence[Path]) -> tuple[str, str]:
    """(service id, where it came from). Order: environment, each ``.env``, the app default."""
    value = environ.get("CENSUS_SERVICE_ID")
    if value:
        return value, "environment"
    for path in env_files:
        value = _dotenv_value(path, "CENSUS_SERVICE_ID")
        if value:
            return value, str(path)
    return DEFAULT_SERVICE_ID, "default"


# ── fetching ──────────────────────────────────────────────────────────────────


def _http_get(url: str, timeout: float) -> tuple[int, str]:
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, headers={"Accept": "application/json"})
    except httpx.TimeoutException as exc:
        raise TimeoutError(type(exc).__name__) from None
    except httpx.HTTPError as exc:  # message may embed the URL: keep only the type
        raise ConnectionError(type(exc).__name__) from None
    return response.status_code, response.text


def fetch_json(
    url: str, *, get: HttpGet = _http_get, sleep: Callable[[float], None] = time.sleep, timeout: float = 15.0
) -> dict:
    """GET + parse, retrying once after a short pause on a timeout or a 5xx."""
    failure = ""
    for attempt in (1, 2):
        try:
            status, text = get(url, timeout)
        except TimeoutError:
            failure = f"timed out after {timeout:.0f}s"
        except ConnectionError as exc:
            raise CensusError(f"network error ({exc})") from None
        else:
            if status >= 500:
                failure = f"HTTP {status}"
            elif status != 200:
                raise CensusError(f"HTTP {status}")
            else:
                try:
                    payload = json.loads(text)
                except ValueError:
                    raise CensusError("response was not JSON") from None
                if not isinstance(payload, dict):
                    raise CensusError("response was not a JSON object")
                problem = payload.get("error") or payload.get("errorCode") or payload.get("errorMessage")
                if problem:
                    raise CensusError(f"Census error: {mask(str(problem))[:200]}")
                return payload
        if attempt == 1:
            sleep(RETRY_PAUSE_S)
    raise CensusError(f"{failure} (after one retry)")


# ── compact output ────────────────────────────────────────────────────────────


def compact(value: object, max_items: int = MAX_LIST_ITEMS, max_str: int = MAX_STR_CHARS) -> object:
    """Cut nested lists to ``max_items`` (+ a ``... (+N more)`` marker) and long strings to ``max_str``."""
    if isinstance(value, dict):
        return {k: compact(v, max_items, max_str) for k, v in value.items()}
    if isinstance(value, list):
        head = [compact(v, max_items, max_str) for v in value[:max_items]]
        return head + [f"... (+{len(value) - max_items} more)"] if len(value) > max_items else head
    if isinstance(value, str) and len(value) > max_str:
        return f"{value[:max_str]}... (+{len(value) - max_str} chars)"
    return value


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render(payload: Mapping[str, object], collection: str, url: str, *, raw: bool = False) -> tuple[list[str], int]:
    """Output lines and the row count. First line: ``returned=N  GET <masked url>``."""
    rows = payload.get(f"{collection}_list")
    if "count" in payload and rows is None:  # the count verb
        count = int(str(payload["count"]))
        return [f"count={count}  GET {mask(url)}"], count
    if not isinstance(rows, list):
        rows = []
    returned = payload.get("returned", len(rows))
    lines = [f"returned={returned}  GET {mask(url)}"]
    lines += [_dump(row if raw else compact(row)) for row in rows]
    paging = (f"{collection}_list", "returned", "limit", "min_ts", "max_ts")
    extras = {k: v for k, v in payload.items() if k not in paging}
    if extras and not rows:
        lines.append(_dump(compact(extras)))
    return lines, len(rows)


def cap_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    more = len(text) - max_chars
    return f"{text[:max_chars]}\n... truncated at {max_chars} chars (+{more} more): narrow --show or raise --max-chars"


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="census_q.py",
        description="One Daybreak Census (EQ2) query with a narrow projection and compact, capped output.",
        epilog=(
            "The service id (CENSUS_SERVICE_ID from the environment or .env) is never printed. "
            "Exit codes: 0 rows returned, 1 no rows or Census/network error, 2 usage error."
        ),
    )
    parser.add_argument("collection", help="Census collection: character, guild, item, spell, recipe, ...")
    parser.add_argument("filters", nargs="*", metavar="key=value", help="query filters, e.g. name.first=Foo")
    parser.add_argument("--show", metavar="a,b,c", help="fields to return (c:show); required unless --raw/--count")
    parser.add_argument("--limit", type=int, default=5, metavar="N", help="max rows (c:limit, default 5)")
    parser.add_argument("--sort", metavar="FIELD[:-1]", help="c:sort, e.g. stats.health.max:-1")
    parser.add_argument("--resolve", metavar="EXPR", help="c:resolve, e.g. members(name,type)")
    parser.add_argument("--count", action="store_true", help="use the count verb: print how many rows match")
    parser.add_argument(
        "--raw", action="store_true", help="allow a query without --show and print rows uncut (still capped)"
    )
    parser.add_argument(
        "--max-chars", type=int, default=4000, metavar="N", help="total output cap (default 4000, 0 off)"
    )
    parser.add_argument("--timeout", type=float, default=15.0, metavar="SEC", help="per-attempt timeout (default 15)")
    return parser


def main(argv: Sequence[str] | None = None, root: Path = ROOT, get: HttpGet = _http_get) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=stream.encoding if stream.isatty() else "utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    try:
        if not (args.show or args.raw or args.count):
            raise UsageError("--show a,b,c is required (an unprojected Census document is huge); --raw overrides")
        if args.limit < 1:
            raise UsageError("--limit must be at least 1")
        filters = parse_filters(args.filters)
        env_files = [root / ".env"]
        main_root = main_worktree(root)
        if main_root and main_root.resolve() != root.resolve():
            env_files.append(main_root / ".env")
        service_id, source = resolve_service_id(os.environ, env_files)
        if args.count:
            url = build_url(service_id, args.collection, filters, verb="count")
        else:
            url = build_url(
                service_id,
                args.collection,
                filters,
                show=args.show,
                limit=args.limit,
                sort=args.sort,
                resolve=args.resolve,
            )
    except UsageError as exc:
        print(f"census_q: {exc}", file=sys.stderr)
        return 2
    if source == "default":
        print("census_q: CENSUS_SERVICE_ID not set; using the rate-limited public default", file=sys.stderr)
    try:
        payload = fetch_json(url, get=get, timeout=args.timeout)
    except CensusError as exc:
        print(f"census_q: {mask(str(exc))}  [GET {mask(url)}]", file=sys.stderr)
        return 1
    lines, rows = render(payload, args.collection, url, raw=args.raw)
    print(cap_text("\n".join(lines), args.max_chars))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
