BEGIN;

CREATE TABLE IF NOT EXISTS graph_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_path TEXT NOT NULL,
    source_db TEXT NOT NULL,
    status TEXT NOT NULL,
    source_fingerprint TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    error TEXT
);

DROP TABLE IF EXISTS graph_builds_017;
CREATE TABLE graph_builds_017 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_path TEXT NOT NULL,
    source_db TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('building', 'validated', 'active', 'failed')),
    source_fingerprint TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    error TEXT
);

INSERT INTO graph_builds_017 (
    id,
    graph_path,
    source_db,
    status,
    source_fingerprint,
    started_at,
    completed_at,
    validation_summary,
    error
)
SELECT
    id,
    graph_path,
    source_db,
    CASE
        WHEN status IN ('building', 'validated', 'active', 'failed') THEN status
        ELSE 'failed'
    END,
    COALESCE(source_fingerprint, 'legacy-unknown'),
    started_at,
    completed_at,
    validation_summary,
    error
FROM graph_builds;

DROP TABLE graph_builds;
ALTER TABLE graph_builds_017 RENAME TO graph_builds;

CREATE INDEX idx_graph_builds_status_started
    ON graph_builds(status, started_at);

COMMIT;
