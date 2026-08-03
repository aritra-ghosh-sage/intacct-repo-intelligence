-- Repository archival lifecycle and repository-scoped compatibility evidence
-- are applied atomically by catalog.migrations.  This marker is retained for
-- operator-visible migration inventory.  Migration 028 is deliberately not
-- assumed by this independent migration.
BEGIN;
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('029_repository_archival');
COMMIT;
