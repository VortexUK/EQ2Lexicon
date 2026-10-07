-- SQL for backend/eq2db/recipes.py (Postgres `recipes` schema; unqualified —
-- the catalogue's connections set search_path). Schema DDL lives in
-- db/migrations/0009_recipes.sql; this file is DML only.

-- Column-list fragment shared by every find_* query. Not standalone SQL;
-- spliced via f-string in Python where needed.
-- :name select_cols
id, crc, name, name_lower, bench, version,
primary_comp, primary_qty, secondary_comps,
fuel_comp, fuel_qty,
out_unfinished_id, out_unfinished_count,
out_simple_id, out_simple_count,
out_worked_id, out_worked_count,
out_elaborate_id, out_elaborate_count,
out_formed_id, out_formed_count,
base_name_lower, crafted_tier, out_level,
last_update

-- out_level is deliberately absent from the upsert column list: it is
-- loader-owned (resolved from the items schema at build time), so a
-- re-download upsert never clobbers a filled value.
-- :name upsert
INSERT INTO recipes (
    id, crc, name, name_lower,
    bench, version,
    primary_comp, primary_qty,
    secondary_comps,
    fuel_comp, fuel_qty,
    out_unfinished_id, out_unfinished_count,
    out_simple_id,    out_simple_count,
    out_worked_id,    out_worked_count,
    out_elaborate_id, out_elaborate_count,
    out_formed_id,    out_formed_count,
    base_name_lower, crafted_tier,
    last_update
) VALUES (
    %(id)s, %(crc)s, %(name)s, %(name_lower)s,
    %(bench)s, %(version)s,
    %(primary_comp)s, %(primary_qty)s,
    %(secondary_comps)s,
    %(fuel_comp)s, %(fuel_qty)s,
    %(out_unfinished_id)s, %(out_unfinished_count)s,
    %(out_simple_id)s,     %(out_simple_count)s,
    %(out_worked_id)s,     %(out_worked_count)s,
    %(out_elaborate_id)s,  %(out_elaborate_count)s,
    %(out_formed_id)s,     %(out_formed_count)s,
    %(base_name_lower)s, %(crafted_tier)s,
    %(last_update)s
)
ON CONFLICT (id) DO UPDATE SET
    crc = excluded.crc,
    name = excluded.name, name_lower = excluded.name_lower,
    bench = excluded.bench, version = excluded.version,
    primary_comp = excluded.primary_comp, primary_qty = excluded.primary_qty,
    secondary_comps = excluded.secondary_comps,
    fuel_comp = excluded.fuel_comp, fuel_qty = excluded.fuel_qty,
    out_unfinished_id = excluded.out_unfinished_id,
    out_unfinished_count = excluded.out_unfinished_count,
    out_simple_id = excluded.out_simple_id,
    out_simple_count = excluded.out_simple_count,
    out_worked_id = excluded.out_worked_id,
    out_worked_count = excluded.out_worked_count,
    out_elaborate_id = excluded.out_elaborate_id,
    out_elaborate_count = excluded.out_elaborate_count,
    out_formed_id = excluded.out_formed_id,
    out_formed_count = excluded.out_formed_count,
    base_name_lower = excluded.base_name_lower,
    crafted_tier = excluded.crafted_tier,
    last_update = excluded.last_update;

-- :name count
SELECT COUNT(*) AS n FROM recipes;

-- :name find_by_id
SELECT {cols} FROM recipes WHERE id = %s LIMIT 1;

-- :name find_by_name_exact
SELECT {cols} FROM recipes WHERE name_lower = %s ORDER BY name;

-- :name find_by_name_like
SELECT {cols} FROM recipes WHERE name_lower LIKE %s ESCAPE '\' ORDER BY name;

-- :name find_by_spell
SELECT {cols} FROM recipes
WHERE base_name_lower = %s
  AND crafted_tier    = %s
ORDER BY name;

-- :name find_spells_by_tier
SELECT {cols} FROM recipes
WHERE base_name_lower = ANY(%s)
  AND crafted_tier    = %s
ORDER BY name;

-- :name find_by_output_id
SELECT {cols} FROM recipes
WHERE out_formed_id    = %(id)s
   OR out_elaborate_id = %(id)s
   OR out_worked_id    = %(id)s
   OR out_simple_id    = %(id)s
   OR out_unfinished_id = %(id)s
ORDER BY name;

-- :name classes_for_recipe
SELECT class FROM recipe_classes WHERE recipe_id = %(id)s ORDER BY class;
