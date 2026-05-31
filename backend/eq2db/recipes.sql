-- SQL queries for backend/eq2db/recipes.py.
-- DDL stays in recipes.py next to init_db()'s migration block.

-- :name upsert
INSERT OR REPLACE INTO recipes (
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
    :id, :crc, :name, :name_lower,
    :bench, :version,
    :primary_comp, :primary_qty,
    :secondary_comps,
    :fuel_comp, :fuel_qty,
    :out_unfinished_id, :out_unfinished_count,
    :out_simple_id,     :out_simple_count,
    :out_worked_id,     :out_worked_count,
    :out_elaborate_id,  :out_elaborate_count,
    :out_formed_id,     :out_formed_count,
    :base_name_lower, :crafted_tier,
    :last_update
);

-- :name count
SELECT COUNT(*) FROM recipes;

-- :name select_unbackfilled_tiers
SELECT id, name FROM recipes WHERE crafted_tier IS NULL;

-- :name backfill_tier
UPDATE recipes SET base_name_lower = ?, crafted_tier = ? WHERE id = ?;

-- Column-list fragment shared by every find_* query. Not standalone SQL.
-- :name select_cols
id, crc, name, name_lower, bench, version,
primary_comp, primary_qty, secondary_comps,
fuel_comp, fuel_qty,
out_unfinished_id, out_unfinished_count,
out_simple_id, out_simple_count,
out_worked_id, out_worked_count,
out_elaborate_id, out_elaborate_count,
out_formed_id, out_formed_count,
base_name_lower, crafted_tier,
last_update

-- :name find_by_id
SELECT {cols} FROM recipes WHERE id = ? LIMIT 1;

-- :name find_by_name_exact
SELECT {cols} FROM recipes WHERE name_lower = ? ORDER BY name;

-- :name find_by_name_like
SELECT {cols} FROM recipes WHERE name_lower LIKE ? ESCAPE '\' ORDER BY name;

-- :name find_by_spell
SELECT {cols} FROM recipes
WHERE base_name_lower = ?
  AND crafted_tier    = ?
ORDER BY name;

-- :name find_spells_by_tier
-- {placeholders} = comma-joined "?,?,..." sized at call time.
SELECT {cols} FROM recipes
WHERE base_name_lower IN ({placeholders})
  AND crafted_tier    = ?
ORDER BY name;

-- :name find_by_output_id
SELECT {cols} FROM recipes
WHERE out_formed_id    = :id
   OR out_elaborate_id = :id
   OR out_worked_id    = :id
   OR out_simple_id    = :id
   OR out_unfinished_id = :id
ORDER BY name;
