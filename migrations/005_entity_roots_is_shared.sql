ALTER TABLE entity_roots
ADD COLUMN is_shared INTEGER DEFAULT 0;

UPDATE entity_roots
SET is_shared = 0;

UPDATE entity_roots
SET is_shared = 1
WHERE symbol_id IN (
    SELECT symbol_id
    FROM entity_roots
    GROUP BY symbol_id
    HAVING COUNT(DISTINCT entity_id) > 1
);

CREATE INDEX IF NOT EXISTS idx_entity_roots_is_shared
ON entity_roots(is_shared);
