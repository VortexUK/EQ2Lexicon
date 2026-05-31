"""Authoritative server-side boss detection.

EQ2 trash mobs are named with a lowercase article ("a krait warrior",
"an ancient guard"); bosses have a proper capitalised name. First-character
uppercase is the simplest reliable signal. The frontend keeps a matching copy
in ParsesPage.tsx; this server version is authoritative for rankings + deletes.
"""

from __future__ import annotations


def is_boss(title: str | None) -> bool:
    return bool(title) and "A" <= title[0] <= "Z"
