-- Migration 012: Remove sql mappings sourced from teardown scripts
-- Rationale: DROP TABLE matches are naming collisions, not entity
-- provenance. Phase 2D.1 ISSUE-D1I.

DELETE FROM entity_mappings
WHERE mapping_type = 'sql'
  AND (
    source_text LIKE '%drop_all.sql'
    OR source_text LIKE '%drop_all_%.sql'
    OR source_text LIKE '%/teardown/%'
  );
