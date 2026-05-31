-- SQL for backend/server/api/recipes.py — recipe search with optional
-- query / tier / bench / class-name / craft-class filters.
--
-- Note on .format() placeholders in comments: avoid mentioning literal
-- placeholder names like the placeholders-token or where-clause-token
-- inside SQL comments on blocks that get .format()'d — the format pass
-- will substitute them in the comments too, and if the replacement is
-- multi-line (a subquery), the comment break can leak SQL keywords into
-- live SQL position. Rankings.sql had to undo this once already.

-- ---------------------------------------------------------------------------
-- items.db queries (items DB, sync path)
-- ---------------------------------------------------------------------------

-- :name items_by_class_label_like
-- Items whose class_label matches a fuzzy lowercased query — drives the
-- "filter recipes by character class" feature.
SELECT id FROM items WHERE LOWER(class_label) LIKE ?;

-- :name items_class_labels_by_id_chunk
-- For a chunk of item ids, fetch (id, class_label) so the recipe enrichment
-- pass can colour-code result rows. Caller composes the IN list and templates
-- it in via .format(); chunk size is bounded by SQLite's variable limit.
SELECT id, class_label FROM items WHERE id IN ({placeholders});

-- ---------------------------------------------------------------------------
-- recipes.db queries (aiosqlite path)
-- ---------------------------------------------------------------------------

-- :name count_recipes_where
-- Total result count for a recipe search. The where placeholder is composed
-- in Python ("name_lower LIKE ? AND bench = ? …") from whichever filters
-- the request actually carried.
SELECT COUNT(DISTINCT id) FROM recipes WHERE {where};

-- :name select_recipes_where
-- One page of recipe rows, ordered alphabetically. Same where composition
-- as count_recipes_where. LIMIT / OFFSET stay as Python f-string ints
-- because aiosqlite's parameter binder doesn't always accept ? for those
-- positions on every SQLite build and the values are server-controlled
-- (page param is validated upstream).
SELECT id, name, bench, crafted_tier,
       primary_comp, primary_qty, secondary_comps,
       fuel_comp, fuel_qty,
       out_formed_id, out_formed_count, out_elaborate_id
FROM recipes
WHERE {where}
GROUP BY id
ORDER BY name_lower ASC
LIMIT {limit} OFFSET {offset};

-- :name recipe_classes_for_recipes_chunk
-- For a chunk of recipe ids, list the tradeskill classes that teach each
-- recipe. Used to annotate result rows with the accurate class label (the
-- `bench` column is shared across classes).
SELECT recipe_id, class FROM recipe_classes
WHERE recipe_id IN ({placeholders}) ORDER BY class;
