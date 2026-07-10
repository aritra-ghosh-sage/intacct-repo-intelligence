ALTER TABLE dbschema_tables ADD COLUMN file_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_dbschema_tables_file_id
    ON dbschema_tables(file_id);

CREATE TABLE IF NOT EXISTS entity_access_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    surface TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    link_type TEXT NOT NULL,
    evidence_file_id INTEGER,
    evidence_symbol_id INTEGER,
    confidence_mode TEXT NOT NULL DEFAULT 'deterministic_exact',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(evidence_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    UNIQUE(entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_access_links_entity_surface
    ON entity_access_links(entity_id, surface);

CREATE INDEX IF NOT EXISTS idx_entity_access_links_surface_record
    ON entity_access_links(surface, record_id);

CREATE INDEX IF NOT EXISTS idx_entity_access_links_evidence_file
    ON entity_access_links(evidence_file_id);
