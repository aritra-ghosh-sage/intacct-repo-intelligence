CREATE TABLE IF NOT EXISTS openapispec_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER,
    file_path TEXT,
    module TEXT,
    slug TEXT,
    version TEXT,
    kind TEXT,
    canonical_name TEXT,
    resource_path TEXT,
    title TEXT,
    state TEXT,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_openapispec_file_id ON openapispec_index(file_id);
CREATE INDEX IF NOT EXISTS idx_openapispec_module ON openapispec_index(module);
CREATE INDEX IF NOT EXISTS idx_openapispec_slug ON openapispec_index(slug);
