"""Small SQL construction helpers shared across DB modules.

Keep each helper to a single concern — this is a utility module, not an ORM.
"""

from __future__ import annotations


def build_where(clauses: list[str]) -> str:
    """Return ``'WHERE c1 AND c2 AND ...'`` or ``''`` for an empty list.

    Saves every caller from the repetitive ``'WHERE ' + ' AND '.join(...)
    if ... else ''`` ternary.  The caller still owns parameter binding —
    this helper only formats the clause text.

    Example::

        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        sql = f"SELECT * FROM foo {build_where(where)}"
        conn.execute(sql, params)
    """
    return "WHERE " + " AND ".join(clauses) if clauses else ""
