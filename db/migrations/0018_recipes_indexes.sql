create schema if not exists recipes;
set search_path to recipes, public;

-- Recipes-by-output (recipes.sql) probes three output-id columns; only the
-- formed/elaborate/simple ones were indexed, so the worked/unfinished
-- branches seq-scanned 66k rows on every call (~1.5k calls a day).
CREATE INDEX IF NOT EXISTS idx_recipes_out_worked     ON recipes (out_worked_id);
CREATE INDEX IF NOT EXISTS idx_recipes_out_unfinished ON recipes (out_unfinished_id);
