# SQL sidecar files and the loader

Application SQL (DML) lives in `.sql` files next to the Python module that
uses it, not in Python string literals. `backend/sql_loader.py` reads those
files into a dict of named statements. Schema DDL is separate: it lives in
`db/migrations/NNNN_<family>.sql` and is applied by `backend/pg_migrate.py`.

## File format

A sidecar is a sequence of named blocks. Each block starts with a
`-- :name <identifier>` marker line and runs until the next marker:

```sql
-- Comments and blank lines above the first marker are allowed.

-- :name list_by_type
SELECT z.id, z.name
FROM zones z
JOIN zone_types t ON t.zone_id = z.id
WHERE t.type = %s;

-- :name count_by_type
SELECT COUNT(*) FROM zone_types WHERE type = %s;
```

Rules enforced by `parse_sql()`:

- A marker is `--`, `:name`, then one identifier matching
  `[a-z_][a-z0-9_]*` (case-insensitive), alone on its line.
- Names must be unique within a file. A duplicate raises `ValueError`, so a
  copy-pasted block can't silently shadow another.
- Any non-comment text before the first marker raises `ValueError`, so a
  file with no markers fails loudly instead of loading as empty.
- Each block is trimmed: leading/trailing blank lines and **trailing**
  comment lines are dropped. That is what lets a section-divider comment sit
  between two blocks without leaking into the end of the previous one.
- Trailing semicolons are kept.

## Using it

```python
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)   # loads the sibling zones.sql for zones.py

conn.execute(_SQL["list_by_type"], (zone_type,))
```

`load_sql(__file__)` swaps the module's `.py` suffix for `.sql`
(`backend/eq2db/zones.py` -> `backend/eq2db/zones.sql`) and raises
`FileNotFoundError` if it is missing. Modules call it at import time, so a
misnamed or missing file fails on import, not on first use.

The result is a plain `dict[str, str]`; there are no generated functions.
Callers pass the text straight to psycopg (`conn.execute(sql, params)` for
the sync catalogues, `await db.execute(sql, params)` for the async stores in
`backend/server/db/`). Parameters use psycopg's `%s` placeholders.

### Composing statements

Some blocks are templates or fragments. A block can hold a `{name}` field
that the caller fills with `str.format` for things placeholders can't bind:
column lists, `WHERE` clauses built from a fixed set of options, sort orders.

```sql
-- :name find_by_id
SELECT {cols} FROM recipes WHERE id = %s LIMIT 1;
```

```python
self._fetchone(_SQL["find_by_id"].format(cols=_SELECT_COLS), (recipe_id,))
```

Only ever format in trusted, code-defined text (another block, a constant,
a whitelisted identifier); user input always goes through `%s` parameters.
Because `str.format` is applied, a literal brace in a template block must be
doubled.

## Gotchas

- **Percent signs.** When parameters are passed, psycopg scans the whole
  statement text, including SQL comments, for placeholders. A stray percent
  sign in a comment inside a block is a placeholder error at run time. Write
  "percent" in comments; use `%%` for a literal percent in SQL.
- **No schema qualification.** Statements are unqualified (`FROM zones`,
  not `FROM zones.zones`). The connection's `search_path` is set to the
  family schema at checkout (`backend/pg.py`, `backend/db_catalogue.py`),
  which is also how tests point the same SQL at scratch schemas.
- **DML only.** Sidecars never create or alter tables; stores assume the
  migrations have run before the app serves.
- **Keep the marker lines byte-identical** when editing a sidecar. Renaming a
  block means renaming every `_SQL["..."]` lookup that uses it.

## Conventions

- One `.sql` file per Python module that has DML, with a mirrored path.
- Block names are the key the Python side looks up, so name them for what
  the statement does (`select_character`, `upsert_guild_history`).
- A comment that explains a block goes above its marker line, where it is
  outside every statement.
