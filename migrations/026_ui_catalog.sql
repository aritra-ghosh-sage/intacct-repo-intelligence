-- UI catalog tables, composite ownership keys, and integrity triggers are
-- applied atomically by catalog.migrations.apply_multi_repo_migration or
-- apply_delta_refresh_migration.  This marker is retained for operator
-- visibility alongside the Python migration.
BEGIN;
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('026_ui_catalog');
COMMIT;
