-- Contract-V1 request provenance and source-input freshness state.  The
-- Python migration runner performs equivalent idempotent column checks for
-- SQLite catalog upgrades.
ALTER TABLE test_requests ADD COLUMN coverage_scope TEXT NOT NULL DEFAULT 'unknown'
    CHECK(coverage_scope IN ('endpoint', 'non_endpoint', 'unknown'));
ALTER TABLE test_requests ADD COLUMN mapping_provenance_json TEXT;
ALTER TABLE test_coverage_build_state ADD COLUMN coverage_contract_version INTEGER
    NOT NULL DEFAULT 0 CHECK(coverage_contract_version IN (0, 1));
ALTER TABLE test_coverage_build_state ADD COLUMN contract_input_hashes_json TEXT
    NOT NULL DEFAULT '[]';
INSERT OR IGNORE INTO schema_migrations(name) VALUES ('031_rest_automation_contract');
