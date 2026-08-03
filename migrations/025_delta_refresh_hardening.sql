-- Remove unsupported cross-repository link evidence.  Column additions for the
-- raw-diff/runtime contract are applied idempotently by catalog.migrations.
BEGIN;
DELETE FROM integration_links;
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('025_delta_refresh_hardening');
COMMIT;
