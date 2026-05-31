"""Hydrate ``data/raids/raids.db`` with ACT triggers from ``spell_timers.xml``.

Reads ``scripts/dev/spell_timers.xml`` (an ACT export) and writes the relevant
triggers + spell-timers into raids.db, attributed to the right
``(zone_name, position)`` per the manual mapping below.

Scope: EoF + RoK only (matches the project's TLE focus). Other expansions in
the XML (TSO, SF, DoV, ToV, AoM, …) are ignored.

Usage
-----

  python scripts/dev/ingest_act_triggers.py                # dry-run, prints plan
  python scripts/dev/ingest_act_triggers.py --apply        # writes raids.db
  python scripts/dev/ingest_act_triggers.py --xml <path>   # override XML source

Idempotent: re-runs skip triggers whose (encounter_id, regex, sound_data)
already exists in raids.db. Spell timers are upserted by name (the existing
UNIQUE (encounter_id, name_lower) takes care of dedup).
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Path setup so this works when run from the repo root.
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.eq2db import raids as raids_db  # noqa: E402
from backend.eq2db import zones as zones_db

# ---------------------------------------------------------------------------
# Attribution: XML Category → default (zone_name, encounter_position)
# ---------------------------------------------------------------------------
#
# The XML's ``Category`` attribute is the contributor's loose grouping in ACT
# — sometimes a zone name, sometimes a boss name. We resolve each Category to
# a single (zone, position) target. For categories where the contributor
# tagged the zone but the triggers actually fire against a specific boss in
# that zone, this is the per-user-confirmed attribution.

DEFAULT_ATTRIBUTION: dict[str, tuple[str, int]] = {
    # EoF
    "Freethinker Hideout": ("Freethinker Hideout", 4),  # all 3 → Malkonis D'Morte
    # RoK
    "Milyex": ("Veeshan's Peak", 6),  # Milyex Vioren
    "Trakanon": ("Trakanon's Lair", 1),  # Trakanon
    "Venril Sathir": ("Venril Sathir's Lair", 1),  # Venril Sathir
    "Veeshan's Peak": ("Veeshan's Peak", 4),  # all 5 → Druushk (user-confirmed)
    "Shard of Hate": ("Shard of Hate", 4),  # all 4 → Kpul D'Vngur ("Maestro" references)
}


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


def _xml_bool(s: str | None) -> bool:
    """ACT writes booleans as the strings 'True' / 'False'."""
    return (s or "").strip().lower() == "true"


def _xml_int(s: str | None, default: int = 0) -> int:
    try:
        return int((s or "").strip())
    except (TypeError, ValueError):
        return default


def parse_triggers_xml(path: Path) -> tuple[list[dict], list[dict]]:
    """Parse the ACT XML into (triggers, spell_timers) plain-dict lists.

    Each dict is keyed by the snake_case raids_db column name so the
    downstream upsert call sites stay simple."""
    tree = ET.parse(path)
    root = tree.getroot()

    triggers: list[dict] = []
    for t in root.iter("Trigger"):
        triggers.append(
            {
                "active": _xml_bool(t.get("Active")),
                "regex": t.get("Regex") or "",
                "sound_data": t.get("SoundData") or "",
                "sound_type": _xml_int(t.get("SoundType"), 3),
                "category_restrict": _xml_bool(t.get("CategoryRestrict")),
                "category_xml": (t.get("Category") or "").strip(),
                "timer": _xml_bool(t.get("Timer")),
                "timer_name": (t.get("TimerName") or "").strip() or None,
                "tabbed": _xml_bool(t.get("Tabbed")),
            }
        )

    # ACT can define the same Name multiple times in different categories
    # (see Taskmaster Out — appears twice). Last-write-wins keyed by lower
    # name; in practice the values are identical across duplicates we care
    # about.
    spell_timers: dict[str, dict] = {}
    for s in root.iter("Spell"):
        name = (s.get("Name") or "").strip()
        if not name:
            continue
        spell_timers[name.lower()] = {
            "name": name,
            "checked": _xml_bool(s.get("Checked")),
            "timer_duration_s": _xml_int(s.get("Timer"), 30),
            "only_master_ticks": _xml_bool(s.get("OnlyMasterTicks")),
            "restrict": _xml_bool(s.get("Restrict")),
            "absolute_": _xml_bool(s.get("Absolute")),
            "start_wav": s.get("StartWav") or "",
            "warning_wav": s.get("WarningWav") or "",
            "warning_value": _xml_int(s.get("WarningValue"), 10),
            "radial_display": _xml_bool(s.get("RadialDisplay")),
            "modable": _xml_bool(s.get("Modable")),
            "tooltip": s.get("Tooltip") or "",
            "fill_color": _xml_int(s.get("FillColor"), -16776961),
            "panel1": _xml_bool(s.get("Panel1")),
            "panel2": _xml_bool(s.get("Panel2")),
            "remove_value": _xml_int(s.get("RemoveValue"), -15),
            "category_xml": (s.get("Category") or "").strip(),
            "restrict_category": _xml_bool(s.get("RestrictCategory")),
        }

    return triggers, spell_timers


# ---------------------------------------------------------------------------
# Encounter resolution (zones.db + raids.db lazy-create)
# ---------------------------------------------------------------------------


def resolve_encounter(zone_name: str, position: int) -> tuple[int, str] | None:
    """Return ``(encounter_id, mob_name)`` for the target encounter, lazy-
    creating the raid_zones + raid_encounters rows if missing.

    ``zone_name`` must match canonical zones_db naming; lookups go through
    ``find_by_name`` which respects aliases."""
    import sqlite3

    z = zones_db.find_by_name(zone_name)
    if z is None:
        return None
    canonical_zone = z["name"]
    expansion_short = z["expansion_short"]

    mob_name: str | None = None
    for boss in z.get("bosses", []):
        if int(boss.get("position", -1)) == position:
            mob_name = boss["encounter_name"]
            break
    if mob_name is None:
        return None

    raids_db.init_db().close()
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        zrow = conn.execute(
            "SELECT id FROM raid_zones WHERE zone_name_lower = ?",
            (canonical_zone.lower(),),
        ).fetchone()
        if zrow is None:
            zone_id = raids_db.upsert_raid_zone(
                conn,
                zone_name=canonical_zone,
                expansion_short=expansion_short,
                source=raids_db.SOURCE_MANUAL,
            )
        else:
            zone_id = zrow["id"]

        erow = conn.execute(
            "SELECT id FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?",
            (zone_id, mob_name.lower()),
        ).fetchone()
        if erow is None:
            encounter_id = raids_db.upsert_raid_encounter(
                conn,
                raid_zone_id=zone_id,
                mob_name=mob_name,
                position=position,
                strategy_md=None,
                source=raids_db.SOURCE_MANUAL,
            )
        else:
            encounter_id = erow["id"]

    return int(encounter_id), mob_name


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _trigger_exists(encounter_id: int, regex: str, sound_data: str) -> bool:
    """Idempotency check — (encounter_id, regex, sound_data) is treated as
    identity. There's no UNIQUE constraint at the DB level (regexes can
    legitimately overlap with different sounds), so we check before insert."""
    import sqlite3

    if not raids_db.DB_PATH.exists():
        return False
    with sqlite3.connect(raids_db.DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM act_triggers WHERE raid_encounter_id = ? AND regex = ? AND sound_data = ?",
            (encounter_id, regex, sound_data),
        ).fetchone()
    return row is not None


def apply_ingest(*, xml_path: Path, dry_run: bool) -> dict:
    """Drive the full ingest. Returns a small summary dict.

    Output:
      * triggers_planned / triggers_written / triggers_skipped
      * spell_timers_planned / spell_timers_written
      * skipped_categories  — counts of (category, reason)
    """
    triggers, spell_timer_lib = parse_triggers_xml(xml_path)

    # Group XML triggers by category → list[trigger_dict]
    by_cat: dict[str, list[dict]] = {}
    for t in triggers:
        by_cat.setdefault(t["category_xml"], []).append(t)

    triggers_planned = 0
    triggers_written = 0
    triggers_skipped_existing = 0
    spell_timers_planned: dict[tuple[int, str], dict] = {}  # (encounter_id, name) -> spell row
    spell_timers_written = 0
    skipped_unmapped: dict[str, int] = {}

    for cat, ts in by_cat.items():
        target = DEFAULT_ATTRIBUTION.get(cat)
        if target is None:
            skipped_unmapped[cat] = len(ts)
            continue
        zone_name, position = target
        resolved = resolve_encounter(zone_name, position)
        if resolved is None:
            print(f"!! could not resolve {zone_name!r} pos {position} for category {cat!r}", file=sys.stderr)
            skipped_unmapped[cat] = len(ts)
            continue
        encounter_id, mob_name = resolved

        for t in ts:
            triggers_planned += 1
            if _trigger_exists(encounter_id, t["regex"], t["sound_data"]):
                triggers_skipped_existing += 1
                print(f"  skip (already exists): [{cat}] {t['regex'][:60]!r}")
                continue
            print(f"  add  [{cat} -> {mob_name}] sound={t['sound_data']!r} regex={t['regex'][:60]!r}")

            if not dry_run:
                conn = raids_db.init_db()
                try:
                    raids_db.upsert_act_trigger(
                        conn,
                        raid_encounter_id=encounter_id,
                        regex=t["regex"],
                        position=0,
                        label=None,
                        notes=f"Ingested from spell_timers.xml (Category={cat!r})",
                        active=t["active"],
                        sound_data=t["sound_data"],
                        sound_type=t["sound_type"],
                        category_restrict=t["category_restrict"],
                        category=mob_name,  # restamp to canonical mob name
                        timer=t["timer"],
                        timer_name=t["timer_name"],
                        tabbed=t["tabbed"],
                        edited_by="ingest_act_triggers",
                    )
                finally:
                    conn.close()
                triggers_written += 1

            # Queue the matching spell-timer (per encounter) if defined in
            # the XML SpellTimers library. Skip if the timer name isn't in
            # the XML — the trigger still ingests, the user can fill in the
            # timer details later via the UI.
            if t["timer"] and t["timer_name"]:
                key = t["timer_name"].lower()
                spell = spell_timer_lib.get(key)
                if spell is None:
                    print(
                        f"     [warn] no <Spell> definition for {t['timer_name']!r} - trigger imported without timer detail"
                    )
                else:
                    spell_timers_planned[(encounter_id, key)] = {**spell, "_mob_name": mob_name}

    # Write the spell timers
    for (encounter_id, _name_lower), spell in spell_timers_planned.items():
        if dry_run:
            print(f"  + spell-timer [{spell['_mob_name']}] {spell['name']!r} {spell['timer_duration_s']}s")
            continue
        conn = raids_db.init_db()
        try:
            # Find existing by (encounter_id, name_lower) so re-runs update,
            # don't 409.
            import sqlite3 as _sq

            with _sq.connect(raids_db.DB_PATH) as q:
                row = q.execute(
                    "SELECT id FROM act_spell_timers WHERE raid_encounter_id = ? AND name_lower = ?",
                    (encounter_id, spell["name"].lower()),
                ).fetchone()
            timer_id = row[0] if row else None
            raids_db.upsert_act_spell_timer(
                conn,
                timer_id=timer_id,
                raid_encounter_id=encounter_id,
                name=spell["name"],
                timer_duration_s=spell["timer_duration_s"],
                checked=spell["checked"],
                only_master_ticks=spell["only_master_ticks"],
                restrict=spell["restrict"],
                absolute_=spell["absolute_"],
                start_wav=spell["start_wav"],
                warning_wav=spell["warning_wav"],
                warning_value=spell["warning_value"],
                radial_display=spell["radial_display"],
                modable=spell["modable"],
                tooltip=spell["tooltip"],
                fill_color=spell["fill_color"],
                panel1=spell["panel1"],
                panel2=spell["panel2"],
                remove_value=spell["remove_value"],
                category=spell["_mob_name"],
                restrict_category=spell["restrict_category"],
                edited_by="ingest_act_triggers",
            )
        finally:
            conn.close()
        spell_timers_written += 1

    return {
        "triggers_planned": triggers_planned,
        "triggers_written": triggers_written,
        "triggers_skipped_existing": triggers_skipped_existing,
        "spell_timers_planned": len(spell_timers_planned),
        "spell_timers_written": spell_timers_written,
        "skipped_unmapped": skipped_unmapped,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--xml",
        type=Path,
        default=Path(__file__).resolve().parent / "spell_timers.xml",
        help="Path to spell_timers.xml (default: scripts/dev/spell_timers.xml)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to raids.db (default: dry-run, no writes).",
    )
    args = ap.parse_args()

    if not args.xml.exists():
        print(f"error: {args.xml} not found", file=sys.stderr)
        sys.exit(1)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== ingest_act_triggers ({mode}) ===")
    print(f"  source: {args.xml}")
    print(f"  raids.db: {raids_db.DB_PATH}")
    print()

    summary = apply_ingest(xml_path=args.xml, dry_run=not args.apply)

    print()
    print("=== summary ===")
    print(f"  triggers planned:        {summary['triggers_planned']}")
    print(f"  triggers written:        {summary['triggers_written']}")
    print(f"  triggers skipped (dupe): {summary['triggers_skipped_existing']}")
    print(f"  spell timers planned:    {summary['spell_timers_planned']}")
    print(f"  spell timers written:    {summary['spell_timers_written']}")
    if summary["skipped_unmapped"]:
        print()
        print("--- unmapped categories (no attribution; ignored) ---")
        for cat, n in sorted(summary["skipped_unmapped"].items(), key=lambda x: -x[1]):
            print(f"  {n:>3}  {cat!r}")
    if not args.apply:
        print()
        print("(dry-run — re-run with --apply to write)")


if __name__ == "__main__":
    main()
