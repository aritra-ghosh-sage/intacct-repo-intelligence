-- Truthful refresh-attempt metadata. Apply through
-- catalog.migrations.apply_multi_repo_migration so IDs and foreign keys are
-- validated transactionally.
BEGIN;
PRAGMA legacy_alter_table=ON;

DROP INDEX IF EXISTS uq_catalog_builds_active;
DROP INDEX IF EXISTS idx_catalog_builds_status_started;
ALTER TABLE catalog_builds RENAME TO catalog_builds_legacy_024;
CREATE TABLE catalog_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_token TEXT NOT NULL UNIQUE,
    parent_catalog_build_id INTEGER,
    catalog_path TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN ('full','auto','delta')),
    effective_mode TEXT NOT NULL CHECK(effective_mode IN ('not_started','full','delta','hybrid')),
    status TEXT NOT NULL CHECK(status IN ('building','validated','active','previous','failed')),
    source_revisions_json TEXT NOT NULL,
    manifest_hash TEXT,
    builder_plan_hash TEXT,
    delta_contract_version INTEGER NOT NULL,
    content_fingerprint TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    diagnostic_error TEXT,
    FOREIGN KEY(parent_catalog_build_id) REFERENCES catalog_builds(id) ON DELETE SET NULL
);
INSERT INTO catalog_builds SELECT * FROM catalog_builds_legacy_024;
UPDATE catalog_builds SET delta_contract_version=2
 WHERE status='active' AND manifest_hash IS NULL AND builder_plan_hash IS NULL
   AND validation_summary='{"baseline":true}';
DROP TABLE catalog_builds_legacy_024;
CREATE UNIQUE INDEX uq_catalog_builds_active
    ON catalog_builds(status) WHERE status='active';
CREATE INDEX idx_catalog_builds_status_started
    ON catalog_builds(status,started_at);

INSERT OR IGNORE INTO schema_migrations(name) VALUES ('024_refresh_contracts');
PRAGMA legacy_alter_table=OFF;
COMMIT;
