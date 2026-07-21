-- Versioned REST test-coverage foundation.
-- Apply once after 018_graph_build_status_previous.sql.
-- The files rebuild keeps existing ids so all provenance references remain valid.

PRAGMA foreign_keys = OFF;
PRAGMA legacy_alter_table = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS source_repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_id TEXT NOT NULL UNIQUE,
    repo_root TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'source',
    revision TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    object_mapping_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO source_repositories (
    id, suite_id, repo_root, kind, enabled
) VALUES (1, 'production-source', '/home/aritraghosh/projects/main', 'source', 1);

-- SQLite cannot drop the old global UNIQUE(path) constraint in-place.
ALTER TABLE files RENAME TO files_legacy_019;

CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL DEFAULT 1,
    path TEXT NOT NULL,
    language TEXT,
    size_bytes INTEGER,
    sha1 TEXT,
    last_modified TEXT,
    last_indexed TEXT,
    last_symbols_extracted TEXT,
    last_relationships_extracted TEXT,
    FOREIGN KEY(repository_id) REFERENCES source_repositories(id) ON DELETE CASCADE,
    UNIQUE(repository_id, path)
);

INSERT INTO files (
    id, repository_id, path, language, size_bytes, sha1, last_modified,
    last_indexed, last_symbols_extracted, last_relationships_extracted
)
SELECT
    id, 1, path, language, size_bytes, sha1, last_modified,
    last_indexed, last_symbols_extracted, last_relationships_extracted
FROM files_legacy_019;

DROP TABLE files_legacy_019;

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_repository_path ON files(repository_id, path);
CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);

-- Existing endpoint rows get their exact indexed OpenAPI version when it can
-- be proven from their source file.  Multiple candidate versions are retained
-- as unresolved rather than guessed.
ALTER TABLE rest_endpoints ADD COLUMN source_version TEXT;

UPDATE rest_endpoints
SET source_version = (
    SELECT MIN(oi.version)
    FROM openapispec_index oi
    WHERE oi.file_id = rest_endpoints.file_id
      AND COALESCE(TRIM(oi.version), '') <> ''
      AND 1 = (
          SELECT COUNT(DISTINCT oi2.version)
          FROM openapispec_index oi2
          WHERE oi2.file_id = rest_endpoints.file_id
            AND COALESCE(TRIM(oi2.version), '') <> ''
      )
)
WHERE source_version IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_rest_endpoints_versioned_identity
    ON rest_endpoints(file_id, method, path, source_version);
CREATE INDEX IF NOT EXISTS idx_rest_endpoints_versioned_route
    ON rest_endpoints(source_version, method, path);

CREATE TABLE IF NOT EXISTS api_version_compatibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_version TEXT NOT NULL,
    endpoint_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'deprecated', 'disabled')),
    rationale TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(test_version, endpoint_version)
);

CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    feature_name TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    case_name TEXT NOT NULL,
    example_row INTEGER,
    feature_line INTEGER NOT NULL,
    scenario_line INTEGER NOT NULL,
    eligibility TEXT NOT NULL DEFAULT 'active'
        CHECK(eligibility IN ('active', 'known_issue', 'ci_only', 'conditional')),
    tags_json TEXT NOT NULL DEFAULT '[]',
    jira_refs_json TEXT NOT NULL DEFAULT '[]',
    source_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repository_id) REFERENCES source_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(repository_id, file_id, scenario_line, example_row)
);
CREATE INDEX IF NOT EXISTS idx_test_cases_repository ON test_cases(repository_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_file ON test_cases(file_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_eligibility ON test_cases(eligibility);

CREATE TABLE IF NOT EXISTS test_case_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id INTEGER NOT NULL,
    version_label TEXT NOT NULL,
    source_kind TEXT NOT NULL
        CHECK(source_kind IN ('feature_tag', 'properties', 'request_override')),
    source_file_id INTEGER,
    source_line INTEGER,
    raw_value TEXT NOT NULL,
    FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(test_case_id, version_label, source_kind, source_file_id, source_line)
);
CREATE INDEX IF NOT EXISTS idx_test_case_versions_case ON test_case_versions(test_case_id);
CREATE INDEX IF NOT EXISTS idx_test_case_versions_label ON test_case_versions(version_label);

CREATE TABLE IF NOT EXISTS test_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    step_line INTEGER NOT NULL,
    method TEXT,
    object_token TEXT,
    raw_path TEXT,
    normalized_path TEXT,
    request_version TEXT,
    expected_status INTEGER,
    operation_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(operation_kind IN ('collection', 'item', 'child', 'workflow', 'custom', 'unknown')),
    FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    UNIQUE(test_case_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_test_requests_case ON test_requests(test_case_id);
CREATE INDEX IF NOT EXISTS idx_test_requests_route ON test_requests(method, normalized_path, request_version);

CREATE TABLE IF NOT EXISTS test_endpoint_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_request_id INTEGER NOT NULL,
    rest_endpoint_id INTEGER NOT NULL,
    compatibility_id INTEGER,
    resolution_kind TEXT NOT NULL
        CHECK(resolution_kind IN ('exact_version', 'compatible_version')),
    FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
    FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
    FOREIGN KEY(compatibility_id) REFERENCES api_version_compatibility(id) ON DELETE SET NULL,
    UNIQUE(test_request_id, rest_endpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_test_endpoint_links_endpoint ON test_endpoint_links(rest_endpoint_id);

CREATE TABLE IF NOT EXISTS test_entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_request_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    rest_endpoint_id INTEGER NOT NULL,
    FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
    UNIQUE(test_request_id, entity_id, rest_endpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_test_entity_links_entity ON test_entity_links(entity_id);

CREATE TABLE IF NOT EXISTS test_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    file_id INTEGER,
    test_case_id INTEGER,
    test_request_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    source_line INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repository_id) REFERENCES source_repositories(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_test_diagnostics_repository_kind
    ON test_diagnostics(repository_id, kind);

COMMIT;
PRAGMA legacy_alter_table = OFF;
PRAGMA foreign_keys = ON;
