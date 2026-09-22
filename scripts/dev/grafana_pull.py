"""Pull Grafana Cloud dashboard data without screenshots.

Reads GRAFANA_URL + GRAFANA_TOKEN from the repo .env (service-account
token, Viewer role). The token is only ever sent as the Authorization
header — never printed, never logged.

Usage:
  python scripts/dev/grafana_pull.py panels [--uid eq2companion-highlights]
  python scripts/dev/grafana_pull.py data --panel 4 [--frm now-24h --to now]
  python scripts/dev/grafana_pull.py render --panel 4 [--out panel.png]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Windows consoles default to cp1252 — arrows/em-dashes in dashboard titles
# must not crash a metrics pull.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

BASE = (os.getenv("GRAFANA_URL") or "").rstrip("/")
TOKEN = os.getenv("GRAFANA_TOKEN") or ""
DEFAULT_UID = "eq2companion-highlights"


def _client() -> httpx.Client:
    if not BASE or not TOKEN:
        sys.exit("GRAFANA_URL / GRAFANA_TOKEN missing from .env")
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)


def _dashboard(client: httpx.Client, uid: str) -> dict:
    r = client.get(f"/api/dashboards/uid/{uid}")
    r.raise_for_status()
    return r.json()


def _walk_panels(dash: dict) -> list[dict]:
    out: list[dict] = []
    for p in dash.get("dashboard", {}).get("panels", []):
        if p.get("type") == "row":
            out.extend(p.get("panels") or [])
        else:
            out.append(p)
    return out


def _template_vars(dash: dict) -> dict[str, str]:
    """{var name: current value} for simple template variables."""
    out: dict[str, str] = {}
    for v in dash.get("dashboard", {}).get("templating", {}).get("list", []):
        cur = v.get("current") or {}
        val = cur.get("value")
        if isinstance(val, list):
            val = val[0] if val else None
        if isinstance(val, str) and val and val != "$__all":
            out[v["name"]] = val
    return out


def _substitute(expr: str, variables: dict[str, str]) -> str:
    for name, val in variables.items():
        expr = expr.replace(f"${{{name}}}", val).replace(f"${name}", val)
    return expr


def cmd_panels(args: argparse.Namespace) -> None:
    with _client() as client:
        dash = _dashboard(client, args.uid)
    print(f"Dashboard: {dash['dashboard'].get('title')}  (uid={args.uid})")
    for p in _walk_panels(dash):
        exprs = [t.get("expr") or t.get("query") or "?" for t in p.get("targets") or []]
        print(f"  [{p.get('id'):>3}] {p.get('type'):<12} {p.get('title')}")
        for e in exprs:
            print(f"        {e}")


def _summarise_frame(frame: dict) -> str:
    data = frame.get("data", {})
    values = data.get("values") or []
    name = (frame.get("schema") or {}).get("name") or ""
    if len(values) < 2 or not values[-1]:
        return f"{name}: (no points)"
    series = [v for v in values[-1] if v is not None]
    if not series:
        return f"{name}: (all null)"
    return (
        f"{name}: last={series[-1]:.4g} min={min(series):.4g} "
        f"max={max(series):.4g} avg={statistics.fmean(series):.4g} n={len(series)}"
    )


def _resolve_datasource(client: httpx.Client, ref: Any) -> Any:
    """Dashboards often reference '${datasource}' (a datasource template
    variable with no stored default) — resolve it to the instance's default
    datasource of the same type (e.g. grafanacloud-prom for prometheus)."""
    if not (isinstance(ref, dict) and "$" in str(ref.get("uid", ""))):
        return ref
    wanted_type = ref.get("type")
    rows = client.get("/api/datasources").json()
    for row in rows:
        if row.get("type") == wanted_type and row.get("isDefault"):
            return {"type": wanted_type, "uid": row["uid"]}
    for row in rows:
        if row.get("type") == wanted_type:
            return {"type": wanted_type, "uid": row["uid"]}
    return ref


def cmd_data(args: argparse.Namespace) -> None:
    with _client() as client:
        dash = _dashboard(client, args.uid)
        panels = {p.get("id"): p for p in _walk_panels(dash)}
        panel = panels.get(args.panel)
        if panel is None:
            sys.exit(f"No panel id {args.panel} — run `panels` to list them.")
        variables = _template_vars(dash)
        queries: list[dict[str, Any]] = []
        for i, t in enumerate(panel.get("targets") or []):
            q = dict(t)
            if isinstance(q.get("expr"), str):
                q["expr"] = _substitute(q["expr"], variables)
            q.setdefault("refId", chr(ord("A") + i))
            q["datasource"] = _resolve_datasource(client, t.get("datasource") or panel.get("datasource"))
            q.setdefault("intervalMs", 60_000)
            q.setdefault("maxDataPoints", 500)
            queries.append(q)
        r = client.post("/api/ds/query", json={"queries": queries, "from": args.frm, "to": args.to})
        r.raise_for_status()
        results = r.json().get("results", {})
    print(f"Panel [{args.panel}] {panel.get('title')}  ({args.frm} → {args.to})")
    for ref, res in results.items():
        for frame in res.get("frames", []):
            print(f"  {ref}  {_summarise_frame(frame)}")
    if args.raw:
        print(json.dumps(results, indent=2)[:20_000])


def cmd_render(args: argparse.Namespace) -> None:
    out = Path(args.out or f"panel-{args.panel}.png")
    with _client() as client:
        r = client.get(
            f"/render/d-solo/{args.uid}/_",
            params={"panelId": args.panel, "from": args.frm, "to": args.to, "width": 1100, "height": 500},
            timeout=60,
        )
        r.raise_for_status()
        out.write_bytes(r.content)
    print(f"saved {out} ({len(r.content)} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uid", default=DEFAULT_UID)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("panels")
    for name in ("data", "render"):
        p = sub.add_parser(name)
        p.add_argument("--panel", type=int, required=True)
        p.add_argument("--frm", default="now-24h")
        p.add_argument("--to", default="now")
        if name == "data":
            p.add_argument("--raw", action="store_true")
        else:
            p.add_argument("--out")
    args = ap.parse_args()
    {"panels": cmd_panels, "data": cmd_data, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    main()
