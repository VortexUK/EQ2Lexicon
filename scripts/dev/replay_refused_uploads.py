"""Restore uploads the spell audit refused on 2026-10-10.

For a few hours the audit quarantined any upload with a flagged participant:
the parse was never stored, only its payload went to parses.tamper_reports
(capped at 512 KiB). This replays those payloads through the normal ingest
writer. A payload cut by the cap still holds the encounter and every
combatant (they come first in the JSON); only the tail of the per-ability
breakdown is missing, and that row is stamped ``detail_pruned_at`` so the
parse page says so.

    uv run python scripts/dev/replay_refused_uploads.py            # dry run
    uv run python scripts/dev/replay_refused_uploads.py --apply

Additive and idempotent: an encid that is already stored is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from backend import pg  # noqa: E402
from backend.server.api.parses import ingest  # noqa: E402
from backend.server.api.parses.models import IngestRequest  # noqa: E402
from backend.server.parses.db import TAMPER_PAYLOAD_CAP  # noqa: E402
from backend.server.parses.db import store as parses_db  # noqa: E402

REASON = "server_out_of_era_spells"
_LOST_SQL = """
SELECT t.id, t.world, t.act_encid, t.title, t.uploader_logger_name, t.uploader_discord_id, t.payload_json
FROM tamper_reports t
WHERE t.reason = %s
  AND NOT EXISTS (SELECT 1 FROM encounters e WHERE e.world = t.world AND e.act_encid = t.act_encid)
ORDER BY t.started_at, t.id
"""


def repair(payload: str) -> tuple[dict | None, bool]:
    """Parse a stored payload. Returns (body, truncated). A capped payload is
    cut back to the last complete array element and closed."""
    try:
        return json.loads(payload), False
    except ValueError:
        pass
    text = payload[:TAMPER_PAYLOAD_CAP]
    end = len(text)
    for _ in range(200):
        end = text.rfind("},", 0, end)
        if end < 0:
            return None, True
        try:
            body = json.loads(text[:end] + "}]}")
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("encounter") and body.get("combatants"):
            return body, True
    return None, True


async def main(apply: bool) -> None:
    with pg.connection(parses_db.schema, autocommit=True) as conn:
        rows = conn.execute(_LOST_SQL, (REASON,)).fetchall()
    print(f"refused uploads not stored: {len(rows)}")
    counts = {"inserted": 0, "skipped": 0, "unparseable": 0, "invalid": 0, "truncated": 0, "error": 0}
    for r in rows:
        body, truncated = repair(r["payload_json"])
        if body is None:
            counts["unparseable"] += 1
            print(f"  UNPARSEABLE report={r['id']} {r['title']!r}")
            continue
        try:
            req = IngestRequest(**body)
        except Exception as exc:
            counts["invalid"] += 1
            print(f"  INVALID report={r['id']} {r['title']!r}: {str(exc)[:120]}")
            continue
        counts["truncated"] += truncated
        if not apply:
            counts["inserted"] += 1
            continue
        world = r["world"]
        uploader = r["uploader_logger_name"] or req.logger_name
        discord_id = r["uploader_discord_id"]
        try:
            guild = await ingest._resolve_uploader_guild_async(uploader, world)
            guild_name = guild if isinstance(guild, str) else None
            verified = await ingest._uploader_claimed(discord_id, uploader, world) if discord_id else False
            status, encounter_id, *_ = await asyncio.to_thread(
                ingest._ingest_payload_sync, req, uploader, guild_name, f"plugin:{discord_id}", {}, world, verified
            )
        except Exception as exc:
            counts["error"] += 1
            print(f"  ERROR report={r['id']} {r['title']!r}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        counts["inserted" if status == "inserted" else "skipped"] += 1
        if status == "inserted" and encounter_id is not None:
            if truncated:
                with pg.connection(parses_db.schema) as conn:
                    conn.execute(
                        "UPDATE encounters SET detail_pruned_at = %s WHERE id = %s", (int(time.time()), encounter_id)
                    )
                    conn.commit()
            names = [c.name for c in req.combatants if ingest._to_bool_tf(c.ally) and c.name and " " not in c.name]
            try:
                await ingest._resolve_and_update_snapshots(encounter_id, names[:48], world)
            except Exception as exc:
                print(f"  snapshot fill failed for encounter {encounter_id}: {exc}")
    print(("APPLIED " if apply else "DRY RUN ") + json.dumps(counts))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    pg.ensure_selector_event_loop_policy()
    asyncio.run(main(ap.parse_args().apply))
