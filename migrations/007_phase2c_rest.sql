CREATE TABLE IF NOT EXISTS rest_endpoints (
    id INTEGER PRIMARY KEY,
    method TEXT,               -- GET | POST | PATCH | DELETE
    path TEXT,                 -- /services/v3/objects/ap-bill
    entity_id INTEGER,
    handler_symbol_id INTEGER,
    file_id INTEGER
);

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
