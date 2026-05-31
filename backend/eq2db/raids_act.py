"""ACT trigger + spell-timer DB helpers for the raids database.

Extracted from ``census/raids_db.py`` (BE-055). The ACT tables
(``act_triggers``, ``act_spell_timers``) are created by
``raids_db.init_db`` and share ``raids_db.DB_PATH`` — import both from
there.

Scope: everything that talks to ``act_triggers`` or ``act_spell_timers``
rows. Zone/encounter/revision helpers live in ``census/raids_db.py``.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from backend.eq2db.raids import DB_PATH, init_db  # re-exported for callers  # noqa: F401

# ---------------------------------------------------------------------------
# Column lists
# ---------------------------------------------------------------------------

_ACT_TRIGGER_COLS = (
    "id, raid_encounter_id, position, label, notes, "
    "active, regex, sound_data, sound_type, "
    "category_restrict, category, "
    "timer, timer_name, tabbed, "
    "last_edited_at, last_edited_by, created_at"
)

_ACT_SPELL_TIMER_COLS = (
    "id, raid_encounter_id, name, name_lower, "
    "checked, timer_duration_s, only_master_ticks, restrict, absolute_, "
    "start_wav, warning_wav, warning_value, "
    "radial_display, modable, tooltip, fill_color, "
    "panel1, panel2, remove_value, category, restrict_category, "
    "last_edited_at, last_edited_by, created_at"
)


# ---------------------------------------------------------------------------
# ACT Trigger helpers
# ---------------------------------------------------------------------------


def list_act_triggers_for_encounter(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    """Every ACT trigger row for an encounter, ordered by position then id."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers WHERE raid_encounter_id = ? ORDER BY position, id",
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_act_trigger(trigger_id: int, path: Path = DB_PATH) -> dict | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_ACT_TRIGGER_COLS} FROM act_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_act_trigger(
    conn: sqlite3.Connection,
    *,
    trigger_id: int | None = None,
    raid_encounter_id: int,
    regex: str,
    position: int = 0,
    label: str | None = None,
    notes: str | None = None,
    active: bool = True,
    sound_data: str = "",
    sound_type: int = 3,
    category_restrict: bool = False,
    category: str | None = None,
    timer: bool = False,
    timer_name: str | None = None,
    tabbed: bool = False,
    edited_by: str | None = None,
) -> int:
    """Insert or update a single trigger row. Pass ``trigger_id`` to UPDATE,
    omit it to INSERT. Returns the row id either way.

    Stores the audit stamp via the ``edited_by`` argument so route callers
    don't have to reach into the schema themselves."""
    now = int(time.time())
    params = (
        raid_encounter_id, position, label, notes,
        int(bool(active)), regex, sound_data, int(sound_type),
        int(bool(category_restrict)), category,
        int(bool(timer)), timer_name, int(bool(tabbed)),
        now, edited_by,
    )  # fmt: skip

    if trigger_id is None:
        cur = conn.execute(
            """
            INSERT INTO act_triggers (
                raid_encounter_id, position, label, notes,
                active, regex, sound_data, sound_type,
                category_restrict, category,
                timer, timer_name, tabbed,
                last_edited_at, last_edited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    conn.execute(
        """
        UPDATE act_triggers SET
            raid_encounter_id = ?, position = ?, label = ?, notes = ?,
            active = ?, regex = ?, sound_data = ?, sound_type = ?,
            category_restrict = ?, category = ?,
            timer = ?, timer_name = ?, tabbed = ?,
            last_edited_at = ?, last_edited_by = ?
        WHERE id = ?
        """,
        params + (trigger_id,),
    )
    conn.commit()
    return trigger_id


def delete_act_trigger(conn: sqlite3.Connection, trigger_id: int) -> bool:
    """Delete a trigger by id. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM act_triggers WHERE id = ?", (trigger_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# ACT Spell Timer helpers
# ---------------------------------------------------------------------------


def list_act_spell_timers_for_encounter(encounter_id: int, path: Path = DB_PATH) -> list[dict]:
    """Every spell-timer row for an encounter, alphabetical by name."""
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers WHERE raid_encounter_id = ? ORDER BY name",
            (encounter_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_act_spell_timer(timer_id: int, path: Path = DB_PATH) -> dict | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_ACT_SPELL_TIMER_COLS} FROM act_spell_timers WHERE id = ?",
            (timer_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_act_spell_timer(
    conn: sqlite3.Connection,
    *,
    timer_id: int | None = None,
    raid_encounter_id: int,
    name: str,
    timer_duration_s: int,
    checked: bool = False,
    only_master_ticks: bool = False,
    restrict: bool = False,
    absolute_: bool = False,
    start_wav: str = "",
    warning_wav: str = "",
    warning_value: int = 10,
    radial_display: bool = False,
    modable: bool = False,
    tooltip: str = "",
    fill_color: int = -16776961,
    panel1: bool = True,
    panel2: bool = False,
    remove_value: int = -15,
    category: str | None = None,
    restrict_category: bool = False,
    edited_by: str | None = None,
) -> int:
    """Insert or update a spell-timer row. Pass ``timer_id`` to UPDATE,
    omit it to INSERT. ``(raid_encounter_id, name_lower)`` is UNIQUE — on
    insert collision the caller should pass ``timer_id`` of the existing
    row instead."""
    now = int(time.time())
    params = (
        raid_encounter_id, name, name.lower(),
        int(bool(checked)), int(timer_duration_s),
        int(bool(only_master_ticks)), int(bool(restrict)), int(bool(absolute_)),
        start_wav, warning_wav, int(warning_value),
        int(bool(radial_display)), int(bool(modable)), tooltip, int(fill_color),
        int(bool(panel1)), int(bool(panel2)), int(remove_value),
        category, int(bool(restrict_category)),
        now, edited_by,
    )  # fmt: skip

    if timer_id is None:
        cur = conn.execute(
            """
            INSERT INTO act_spell_timers (
                raid_encounter_id, name, name_lower,
                checked, timer_duration_s,
                only_master_ticks, restrict, absolute_,
                start_wav, warning_wav, warning_value,
                radial_display, modable, tooltip, fill_color,
                panel1, panel2, remove_value,
                category, restrict_category,
                last_edited_at, last_edited_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    conn.execute(
        """
        UPDATE act_spell_timers SET
            raid_encounter_id = ?, name = ?, name_lower = ?,
            checked = ?, timer_duration_s = ?,
            only_master_ticks = ?, restrict = ?, absolute_ = ?,
            start_wav = ?, warning_wav = ?, warning_value = ?,
            radial_display = ?, modable = ?, tooltip = ?, fill_color = ?,
            panel1 = ?, panel2 = ?, remove_value = ?,
            category = ?, restrict_category = ?,
            last_edited_at = ?, last_edited_by = ?
        WHERE id = ?
        """,
        params + (timer_id,),
    )
    conn.commit()
    return timer_id


def delete_act_spell_timer(conn: sqlite3.Connection, timer_id: int) -> bool:
    """Delete a spell-timer by id. Returns True if a row was removed."""
    cur = conn.execute("DELETE FROM act_spell_timers WHERE id = ?", (timer_id,))
    conn.commit()
    return cur.rowcount > 0
