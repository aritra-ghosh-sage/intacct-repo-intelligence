CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    language TEXT,
    size_bytes INTEGER,
    sha1 TEXT,
    last_modified TEXT,
    last_indexed TEXT
, last_symbols_extracted TEXT);
CREATE TABLE sqlite_sequence(name,seq);
CREATE INDEX idx_files_path
    ON files(path);
CREATE INDEX idx_files_language
    ON files(language);
CREATE TABLE index_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    completed_at TEXT,
    files_scanned INTEGER,
    files_added INTEGER,
    files_updated INTEGER,
    files_skipped INTEGER,
    git_commit TEXT
);
CREATE TABLE symbols (
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
CREATE INDEX idx_symbols_name
    ON symbols(name);
CREATE INDEX idx_symbols_kind
    ON symbols(kind);
CREATE INDEX idx_symbols_file
    ON symbols(file_id);
CREATE INDEX idx_symbols_language
    ON symbols(language);
CREATE TABLE symbol_extraction_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    completed_at TEXT,
    files_processed INTEGER,
    symbols_extracted INTEGER,
    errors INTEGER
);
CREATE TABLE relationships (
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

    extractor TEXT DEFAULT 'phase2_regex_mvp',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, resolution_class TEXT, resolution_reason TEXT,

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
CREATE INDEX idx_relationships_source_symbol
ON relationships(source_symbol_id);
CREATE INDEX idx_relationships_target_symbol
ON relationships(target_symbol_id);
CREATE INDEX idx_relationships_source_name
ON relationships(source_name);
CREATE INDEX idx_relationships_target_name
ON relationships(target_name);
CREATE INDEX idx_relationships_type
ON relationships(relationship_type);
CREATE INDEX idx_relationships_file_path
ON relationships(file_path);
CREATE INDEX idx_relationships_language
ON relationships(language);
CREATE INDEX idx_relationships_resolution_class
        ON relationships(resolution_class)
    ;
CREATE TABLE entity_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    entity_type TEXT,
    confidence REAL DEFAULT 1.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
, ent_file TEXT, module TEXT, table_name TEXT, view_name TEXT, dummy INTEGER);
CREATE TABLE entity_mappings (
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
CREATE TABLE entity_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    entity_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,

    role TEXT,
    weight REAL,
    reason TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP, is_shared INTEGER DEFAULT 0,

    UNIQUE(entity_id, symbol_id),

    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id),
    FOREIGN KEY(symbol_id) REFERENCES symbols(id)
);
CREATE INDEX idx_entity_roots_entity
ON entity_roots(entity_id);
CREATE INDEX idx_entity_roots_symbol
ON entity_roots(symbol_id);
CREATE INDEX idx_entity_roots_weight
ON entity_roots(weight);
CREATE TABLE workflows (
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
CREATE INDEX idx_workflows_entity  ON workflows(entity_id);
CREATE INDEX idx_workflows_type    ON workflows(workflow_type);
CREATE INDEX idx_workflows_source  ON workflows(source_kind);
CREATE TABLE workflow_steps (
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
CREATE INDEX idx_workflow_steps_workflow ON workflow_steps(workflow_id);
CREATE INDEX idx_workflow_steps_action   ON workflow_steps(action);
