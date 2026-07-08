-- Phase 2C.1 workflow discovery
-- Grounded in real .yaml files and AllowedOperationsHandler classes.

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


-- workflow_steps retired