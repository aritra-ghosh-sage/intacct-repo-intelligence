-- Committed-SHA delta refresh metadata.  Existing catalogs should apply this
-- through catalog.migrations.apply_multi_repo_migration so stable keys and the
-- baseline logical fingerprint are populated transactionally.
BEGIN;

CREATE TABLE IF NOT EXISTS catalog_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_token TEXT NOT NULL UNIQUE,
    parent_catalog_build_id INTEGER,
    catalog_path TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN ('full','auto','delta')),
    effective_mode TEXT NOT NULL CHECK(effective_mode IN ('full','delta','hybrid')),
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_builds_active
    ON catalog_builds(status) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_catalog_builds_status_started
    ON catalog_builds(status,started_at);

CREATE TABLE IF NOT EXISTS repo_change_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_build_id INTEGER NOT NULL,
    repo_index_run_id INTEGER NOT NULL,
    repo_id INTEGER NOT NULL,
    base_commit_sha TEXT,
    target_commit_sha TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN ('full','auto','delta')),
    effective_mode TEXT NOT NULL CHECK(effective_mode IN ('full','delta','noop')),
    status TEXT NOT NULL CHECK(status IN ('planned','running','succeeded','failed')),
    fallback_reason TEXT,
    added_count INTEGER NOT NULL DEFAULT 0 CHECK(added_count >= 0),
    modified_count INTEGER NOT NULL DEFAULT 0 CHECK(modified_count >= 0),
    deleted_count INTEGER NOT NULL DEFAULT 0 CHECK(deleted_count >= 0),
    renamed_count INTEGER NOT NULL DEFAULT 0 CHECK(renamed_count >= 0),
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(catalog_build_id) REFERENCES catalog_builds(id) ON DELETE CASCADE,
    FOREIGN KEY(repo_index_run_id) REFERENCES repo_index_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_repo_change_sets_repo_target
    ON repo_change_sets(repo_id,target_commit_sha);
CREATE INDEX IF NOT EXISTS idx_repo_change_sets_build_repo
    ON repo_change_sets(catalog_build_id,repo_id);

CREATE TABLE IF NOT EXISTS repo_changed_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_set_id INTEGER NOT NULL,
    change_type TEXT NOT NULL CHECK(change_type IN ('added','modified','deleted','renamed')),
    old_path TEXT,
    new_path TEXT,
    old_blob_sha TEXT,
    new_blob_sha TEXT,
    FOREIGN KEY(change_set_id) REFERENCES repo_change_sets(id) ON DELETE CASCADE,
    CHECK(
        (change_type='added' AND old_path IS NULL AND new_path IS NOT NULL) OR
        (change_type='modified' AND old_path IS NOT NULL AND new_path IS NOT NULL) OR
        (change_type='deleted' AND old_path IS NOT NULL AND new_path IS NULL) OR
        (change_type='renamed' AND old_path IS NOT NULL AND new_path IS NOT NULL AND old_path<>new_path)
    )
);
CREATE INDEX IF NOT EXISTS idx_repo_changed_paths_change_set
    ON repo_changed_paths(change_set_id);

ALTER TABLE repo_index_stages ADD COLUMN execution_mode TEXT
    CHECK(execution_mode IN ('full','delta','skipped'));
ALTER TABLE repo_index_stages ADD COLUMN invalidation_reason TEXT;
ALTER TABLE repo_index_stages ADD COLUMN affected_record_count INTEGER
    CHECK(affected_record_count IS NULL OR affected_record_count >= 0);

ALTER TABLE symbols ADD COLUMN stable_key TEXT;
UPDATE symbols SET stable_key='legacy:' || id WHERE stable_key IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_symbols_file_stable_key
    ON symbols(file_id,stable_key);

DROP INDEX IF EXISTS idx_graph_builds_status_started;
ALTER TABLE graph_builds RENAME TO graph_builds_legacy_023;
CREATE TABLE graph_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_path TEXT NOT NULL,
    source_db TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('building','validated','active','previous','failed')),
    source_fingerprint TEXT NOT NULL,
    catalog_build_id INTEGER,
    base_graph_build_id INTEGER,
    build_mode TEXT NOT NULL DEFAULT 'full' CHECK(build_mode IN ('full','delta')),
    projection_version INTEGER,
    source_revisions_json TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    error TEXT,
    FOREIGN KEY(catalog_build_id) REFERENCES catalog_builds(id) ON DELETE SET NULL,
    FOREIGN KEY(base_graph_build_id) REFERENCES graph_builds(id) ON DELETE SET NULL
);
INSERT INTO graph_builds(
    id,graph_path,source_db,status,source_fingerprint,build_mode,started_at,
    completed_at,validation_summary,error
)
SELECT id,graph_path,source_db,status,source_fingerprint,'full',started_at,
       completed_at,validation_summary,error
FROM graph_builds_legacy_023;
DROP TABLE graph_builds_legacy_023;
CREATE INDEX idx_graph_builds_status_started ON graph_builds(status,started_at);
CREATE INDEX idx_graph_builds_catalog ON graph_builds(catalog_build_id,status);
CREATE UNIQUE INDEX uq_graph_builds_active_path
    ON graph_builds(graph_path) WHERE status='active';

COMMIT;
