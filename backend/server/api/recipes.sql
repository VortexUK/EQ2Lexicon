-- SQL for backend/server/api/recipes.py — recipe search with optional
-- query / tier / bench / class-name / craft-class filters. Runs on ONE
-- connection with search_path = recipes; the items schema is referenced
-- schema-qualified (items.items) — the old two-database split (sqlite
-- ATTACH avoidance, 900-id chunking) is gone.
--
-- Note on .format() placeholders in comments: avoid mentioning literal
-- placeholder names like the placeholders-token or where-clause-token
-- inside SQL comments on blocks that get .format()'d — the format pass
-- will substitute them in the comments too, and if the replacement is
-- multi-line (a subquery), the comment break can leak SQL keywords into
-- live SQL position. Rankings.sql had to undo this once already.

-- :name class_filter_subquery
-- WHERE-clause fragment: recipes whose named-quality scroll output
-- (out_elaborate_id — out_formed_id is the rare "perfect craft" bonus and
-- often points elsewhere) is usable by a class. ILIKE: class_label is
-- TitleCase ("All Fighters") and the query arrives lowercased.
out_elaborate_id IN (SELECT id FROM items.items WHERE class_label ILIKE %s)

-- :name items_class_labels_by_ids
-- For the result page's output-item ids, fetch (id, class_label) so the
-- enrichment pass can colour-code result rows.
SELECT id, class_label FROM items.items WHERE id = ANY(%s);

-- :name count_recipes_where
-- Total result count for a recipe search. The where placeholder is composed
-- in Python ("name_lower LIKE %s AND bench = %s …") from whichever filters
-- the request actually carried.
SELECT COUNT(DISTINCT id) AS n FROM recipes WHERE {where};

-- :name select_recipes_where
-- One page of recipe rows, ordered alphabetically. Same where composition
-- as count_recipes_where (id is the PK, so grouping by it lets PG resolve
-- every other column by functional dependency). LIMIT / OFFSET stay as
-- Python f-string ints — the values are server-controlled (page param is
-- validated upstream).
SELECT id, name, bench, crafted_tier, out_level,
       primary_comp, primary_qty, secondary_comps,
       fuel_comp, fuel_qty,
       out_formed_id, out_formed_count, out_elaborate_id
FROM recipes
WHERE {where}
GROUP BY id
ORDER BY name_lower ASC
LIMIT {limit} OFFSET {offset};

-- :name recipe_classes_for_recipes
-- For the result page's recipe ids, list the tradeskill classes that teach
-- each recipe. Used to annotate result rows with the accurate class label
-- (the `bench` column is shared across classes).
SELECT recipe_id, class FROM recipe_classes
WHERE recipe_id = ANY(%s) ORDER BY class;
