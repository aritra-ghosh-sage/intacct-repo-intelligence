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