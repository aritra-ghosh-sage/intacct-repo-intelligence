-- Registry evidence and source-only UI diagnostics are created atomically by
-- catalog.migrations so composite repository/file ownership remains enforced.
BEGIN;
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('028_api_registry');
COMMIT;
