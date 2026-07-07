CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    language TEXT,
    size_bytes INTEGER,
    sha1 TEXT,
    last_modified TEXT,
    last_indexed TEXT,
    last_symbols_extracted TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_path
    ON files(path);

CREATE INDEX IF NOT EXISTS idx_files_language
    ON files(language);

CREATE TABLE IF NOT EXISTS index_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    completed_at TEXT,
    files_scanned INTEGER,
    files_added INTEGER,
    files_updated INTEGER,
    files_skipped INTEGER,
    git_commit TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_symbol TEXT,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    language TEXT,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_name
    ON symbols(name);

CREATE INDEX IF NOT EXISTS idx_symbols_kind
    ON symbols(kind);

CREATE INDEX IF NOT EXISTS idx_symbols_file
    ON symbols(file_id);

CREATE INDEX IF NOT EXISTS idx_symbols_language
    ON symbols(language);

CREATE TABLE IF NOT EXISTS symbol_extraction_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    completed_at TEXT,
    files_processed INTEGER,
    symbols_extracted INTEGER,
    errors INTEGER
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_symbol_id INTEGER,
    source_name TEXT,
    source_kind TEXT,

    target_symbol_id INTEGER,
    target_name TEXT,
    target_kind TEXT,

    relationship_type TEXT NOT NULL,

    file_id INTEGER,
    file_path TEXT,
    language TEXT,

    confidence REAL DEFAULT 0.7,
    evidence TEXT,
    resolution_class TEXT,
    resolution_reason TEXT,

    extractor TEXT DEFAULT 'phase2_regex_mvp',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (
        source_symbol_id,
        source_name,
        target_symbol_id,
        target_name,
        relationship_type,
        file_path,
        evidence
    )
);

CREATE INDEX IF NOT EXISTS idx_relationships_source_symbol
ON relationships(source_symbol_id);

CREATE INDEX IF NOT EXISTS idx_relationships_target_symbol
ON relationships(target_symbol_id);

CREATE INDEX IF NOT EXISTS idx_relationships_source_name
ON relationships(source_name);

CREATE INDEX IF NOT EXISTS idx_relationships_target_name
ON relationships(target_name);

CREATE INDEX IF NOT EXISTS idx_relationships_type
ON relationships(relationship_type);

CREATE INDEX IF NOT EXISTS idx_relationships_file_path
ON relationships(file_path);

CREATE INDEX IF NOT EXISTS idx_relationships_language
ON relationships(language);

CREATE INDEX IF NOT EXISTS idx_relationships_resolution_class
ON relationships(resolution_class);

CREATE TABLE IF NOT EXISTS entity_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    entity_type TEXT,
    confidence REAL DEFAULT 1.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entity_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    entity_id INTEGER NOT NULL,

    symbol_id INTEGER,
    file_id INTEGER,

    mapping_type TEXT,
    confidence REAL,

    source_text TEXT,

    FOREIGN KEY(entity_id)
        REFERENCES entity_nodes(id)
);

CREATE TABLE IF NOT EXISTS entity_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    entity_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,

    role TEXT,
    weight REAL,
    reason TEXT,
    is_shared INTEGER DEFAULT 0,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(entity_id, symbol_id),

    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id),
    FOREIGN KEY(symbol_id) REFERENCES symbols(id)
);

CREATE INDEX IF NOT EXISTS idx_entity_roots_entity
ON entity_roots(entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_roots_symbol
ON entity_roots(symbol_id);

CREATE INDEX IF NOT EXISTS idx_entity_roots_weight
ON entity_roots(weight);

CREATE INDEX IF NOT EXISTS idx_entity_roots_is_shared
ON entity_roots(is_shared);

CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    entity_id INTEGER NOT NULL,

    name TEXT NOT NULL,
    workflow_type TEXT NOT NULL,     -- allowed_operations | approval | posting | reverse | batch | item | ui | rest
    source_kind TEXT NOT NULL,       -- yaml | class | inference
    source_file TEXT,
    source_symbol_id INTEGER,

    confidence REAL DEFAULT 1.0,
    reason TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(entity_id, name, workflow_type, source_file),

    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_workflows_entity  ON workflows(entity_id);
CREATE INDEX IF NOT EXISTS idx_workflows_type    ON workflows(workflow_type);
CREATE INDEX IF NOT EXISTS idx_workflows_source  ON workflows(source_kind);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    workflow_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,

    name TEXT NOT NULL,
    action TEXT,                     -- e.g. submit | approve | reject | post | reverse | recall
    step_kind TEXT,                  -- rest_op | approval_step | post_step | reverse_step | ui_action

    symbol_id INTEGER,
    file_id INTEGER,
    file_path TEXT,

    confidence REAL DEFAULT 1.0,
    evidence TEXT,

    FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
    FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_workflow_steps_workflow ON workflow_steps(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_steps_action   ON workflow_steps(action);

CREATE TABLE IF NOT EXISTS workflow_nodes (
    id INTEGER PRIMARY KEY,
    name TEXT,
    workflow_type TEXT,        -- approval | posting | reverse | payment | sync
    entity_id INTEGER,
    source_file TEXT
);

CREATE TABLE IF NOT EXISTS workflow_edges (
    workflow_id INTEGER,
    ordinal INTEGER,
    step_name TEXT,
    symbol_id INTEGER,
    file_id INTEGER,
    action TEXT
);

CREATE TABLE IF NOT EXISTS rest_endpoints (
    id INTEGER PRIMARY KEY,
    method TEXT,               -- GET | POST | PATCH | DELETE
    path TEXT,                 -- /services/v3/objects/ap-bill
    entity_id INTEGER,
    handler_symbol_id INTEGER,
    file_id INTEGER
);

CREATE TABLE IF NOT EXISTS ui_companions (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER,
    kind TEXT,                 -- editor | lister | picker
    file_id INTEGER,
    language TEXT              -- javascript | typescript | xslt | phtml
);

CREATE TABLE repos (
    id INTEGER PRIMARY KEY,
    name TEXT,                 -- ia-app | ia-core | ia-restapi-automation-tests | vendor-domain-service
    kind TEXT,                 -- monorepo | domain_service | test_suite
    language TEXT              -- php | java | ts
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER,
    name TEXT,                 -- vendor-domain-service
    entity_id INTEGER          -- optional; when the service maps to one domain object
);

CREATE TABLE IF NOT EXISTS service_endpoints (
    id INTEGER PRIMARY KEY,
    service_id INTEGER,
    method TEXT,
    path TEXT,
    rest_endpoint_id INTEGER   -- optional link to ia-app REST endpoint
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id INTEGER PRIMARY KEY,
    source TEXT,               -- jira | confluence | slack | teams | kb | escalation
    external_id TEXT,
    title TEXT,
    url TEXT,
    entity_id INTEGER,         -- optional link
    workflow_id INTEGER        -- optional link
);

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