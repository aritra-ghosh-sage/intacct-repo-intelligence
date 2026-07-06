CREATE TABLE IF NOT EXISTS entity_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    entity_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,

    role TEXT,
    weight REAL,
    reason TEXT,

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