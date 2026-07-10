-- Normalize relationship target_kind values to canonical labels.
-- Safe to run repeatedly.

UPDATE relationships
SET target_kind = NULL
WHERE target_kind IS NOT NULL
  AND LOWER(TRIM(target_kind)) IN ('', 'unknown');

UPDATE relationships
SET target_kind = 'query'
WHERE target_kind IS NOT NULL
  AND LOWER(TRIM(target_kind)) IN ('cqry', 'qry');

UPDATE relationships
SET target_kind = 'query_table'
WHERE target_kind IS NOT NULL
  AND LOWER(TRIM(target_kind)) = 'cqry_table';

UPDATE relationships
SET target_kind = 'query_field'
WHERE target_kind IS NOT NULL
  AND LOWER(TRIM(target_kind)) = 'cqry_field';
