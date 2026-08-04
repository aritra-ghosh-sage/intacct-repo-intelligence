-- Additive action-scoped REST coverage.  The Python migration runner uses
-- idempotent column/table checks for SQLite upgrades; this file records the
-- canonical fresh-schema DDL for operator review.
ALTER TABLE test_requests ADD COLUMN workflow_action TEXT;
CREATE INDEX IF NOT EXISTS idx_test_requests_workflow_action
    ON test_requests(workflow_action)
    WHERE workflow_action IS NOT NULL;
CREATE TABLE IF NOT EXISTS test_coverage_build_state (
    repo_id INTEGER PRIMARY KEY,
    extractor_version TEXT NOT NULL,
    candidate_build_token TEXT NOT NULL,
    indexed_suite_target_sha TEXT NOT NULL,
    dependency_revisions_json TEXT NOT NULL,
    entity_mapping_sha1 TEXT NOT NULL,
    coverage_dependency_fingerprint TEXT NOT NULL,
    built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
);
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('030_workflow_action');
