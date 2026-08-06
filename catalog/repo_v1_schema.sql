PRAGMA foreign_keys = ON;

-- The V1 development catalog is intentionally independent of catalog/schema.sql.
-- It contains only the foundation and immutable inventory slice.
CREATE TABLE catalog_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_token TEXT NOT NULL UNIQUE,
    catalog_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('building', 'validated', 'active')),
    source_revisions_json TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT
);

CREATE UNIQUE INDEX uq_repo_v1_active_build
    ON catalog_builds(status) WHERE status = 'active';

CREATE TABLE repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_key TEXT NOT NULL UNIQUE,
    name TEXT,
    kind TEXT,
    language TEXT,
    remote_url TEXT,
    local_root TEXT NOT NULL,
    tracked_branch TEXT NOT NULL,
    target_commit_sha TEXT NOT NULL,
    build_id INTEGER NOT NULL,
    FOREIGN KEY (build_id) REFERENCES catalog_builds(id) ON DELETE CASCADE
);

CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    blob_object_id TEXT NOT NULL,
    file_mode INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    language TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    UNIQUE (repo_id, path)
);

CREATE INDEX idx_repo_v1_files_repo_path ON files(repo_id, path);

CREATE TABLE symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL CHECK (name <> ''),
    kind TEXT NOT NULL CHECK (kind <> ''),
    parent_symbol TEXT,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    language TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE (file_id, stable_key)
);

CREATE INDEX idx_repo_v1_symbols_repo_file ON symbols(repo_id, file_id);

CREATE TABLE symbol_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    diagnostic_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL CHECK (severity = 'error'),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX idx_repo_v1_symbol_diagnostics_repo_file
    ON symbol_diagnostics(repo_id, file_id);
