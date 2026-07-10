CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    language TEXT,
    size_bytes INTEGER,
    sha1 TEXT,
    last_modified TEXT,
    last_indexed TEXT,
    last_symbols_extracted TEXT,
    last_relationships_extracted TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_path
    ON files(path);

CREATE INDEX IF NOT EXISTS idx_files_language
    ON files(language);

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
    file_id INTEGER,
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
CREATE INDEX IF NOT EXISTS idx_workflows_file_id ON workflows(file_id);

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

CREATE TABLE repos (
    id INTEGER PRIMARY KEY,
    name TEXT,                 -- ia-app | ia-core | ia-restapi-automation-tests | vendor-domain-service
    kind TEXT,                 -- monorepo | domain_service | test_suite
    language TEXT              -- php | java | ts
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
    x_mapped_to TEXT,
    title TEXT,
    state TEXT,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_openapispec_file_id ON openapispec_index(file_id);
CREATE INDEX IF NOT EXISTS idx_openapispec_module ON openapispec_index(module);
CREATE INDEX IF NOT EXISTS idx_openapispec_slug ON openapispec_index(slug);

CREATE INDEX IF NOT EXISTS idx_openapispec_x_mapped_to ON openapispec_index(x_mapped_to);

CREATE TABLE IF NOT EXISTS security_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_key TEXT NOT NULL,
    op_numeric_id INTEGER,
    title TEXT,
    action TEXT,
    script TEXT,
    force_mode TEXT,
    secure_only INTEGER,
    allow_dev_env_only INTEGER,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    source_kind TEXT NOT NULL,
    raw_hash TEXT,
    UNIQUE(op_key, op_numeric_id, source_file, source_kind)
);

CREATE INDEX IF NOT EXISTS idx_security_operations_key ON security_operations(op_key);
CREATE INDEX IF NOT EXISTS idx_security_operations_id ON security_operations(op_numeric_id);
CREATE INDEX IF NOT EXISTS idx_security_operations_file_id ON security_operations(file_id);

CREATE TABLE IF NOT EXISTS security_operation_allowops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    allowed_op_key TEXT NOT NULL,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    FOREIGN KEY(operation_id) REFERENCES security_operations(id) ON DELETE CASCADE,
    UNIQUE(operation_id, allowed_op_key, source_file)
);

CREATE INDEX IF NOT EXISTS idx_security_allowops_allowed_key
    ON security_operation_allowops(allowed_op_key);
CREATE INDEX IF NOT EXISTS idx_security_allowops_file_id
    ON security_operation_allowops(file_id);

CREATE TABLE IF NOT EXISTS security_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_name TEXT NOT NULL,
    module TEXT,
    label TEXT,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    UNIQUE(policy_name, source_file)
);

CREATE INDEX IF NOT EXISTS idx_security_policies_name ON security_policies(policy_name);
CREATE INDEX IF NOT EXISTS idx_security_policies_module ON security_policies(module);
CREATE INDEX IF NOT EXISTS idx_security_policies_file_id ON security_policies(file_id);

CREATE TABLE IF NOT EXISTS security_policy_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL,
    value_key TEXT NOT NULL,
    display TEXT,
    value_label TEXT,
    source_line INTEGER,
    FOREIGN KEY(policy_id) REFERENCES security_policies(id) ON DELETE CASCADE,
    UNIQUE(policy_id, value_key)
);

CREATE TABLE IF NOT EXISTS security_policy_eops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_value_id INTEGER NOT NULL,
    op_key TEXT NOT NULL,
    source_line INTEGER,
    FOREIGN KEY(policy_value_id) REFERENCES security_policy_values(id) ON DELETE CASCADE,
    UNIQUE(policy_value_id, op_key)
);

CREATE INDEX IF NOT EXISTS idx_security_policy_eops_key ON security_policy_eops(op_key);

CREATE TABLE IF NOT EXISTS security_menus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT,
    menu_name TEXT,
    source_file TEXT NOT NULL UNIQUE,
    file_id INTEGER
);

CREATE TABLE IF NOT EXISTS security_menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_id INTEGER NOT NULL,
    item_path TEXT NOT NULL,
    item_name TEXT NOT NULL,
    menu_item_id TEXT,
    menu_script TEXT,
    menu_key TEXT,
    source_line INTEGER,
    FOREIGN KEY(menu_id) REFERENCES security_menus(id) ON DELETE CASCADE,
    UNIQUE(menu_id, item_path)
);

CREATE INDEX IF NOT EXISTS idx_security_menu_items_key ON security_menu_items(menu_key);
CREATE INDEX IF NOT EXISTS idx_security_menus_file_id ON security_menus(file_id);

CREATE TABLE IF NOT EXISTS security_menu_op_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_item_id INTEGER NOT NULL,
    op_key TEXT NOT NULL,
    operation_id INTEGER,
    resolution_reason TEXT NOT NULL,
    FOREIGN KEY(menu_item_id) REFERENCES security_menu_items(id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES security_operations(id) ON DELETE SET NULL,
    UNIQUE(menu_item_id, op_key)
);

CREATE INDEX IF NOT EXISTS idx_security_menu_op_links_op_key
    ON security_menu_op_links(op_key);

CREATE TABLE IF NOT EXISTS dbschema_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    primary_keys TEXT,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    raw_hash TEXT,
    UNIQUE(table_name, source_file)
);

CREATE INDEX IF NOT EXISTS idx_dbschema_tables_name ON dbschema_tables(table_name);
CREATE INDEX IF NOT EXISTS idx_dbschema_tables_file_id ON dbschema_tables(file_id);

CREATE TABLE IF NOT EXISTS dbschema_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dbschema_table_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    field_type TEXT,
    source_line INTEGER,
    FOREIGN KEY(dbschema_table_id) REFERENCES dbschema_tables(id) ON DELETE CASCADE,
    UNIQUE(dbschema_table_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_dbschema_fields_name ON dbschema_fields(field_name);

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


DROP VIEW IF EXISTS graph_ready_entities;

CREATE VIEW graph_ready_entities AS
WITH strong_roots AS (
  SELECT
    er.entity_id,
    COUNT(*) AS strong_root_count,
    MAX(er.weight) AS max_root_weight,
    SUM(CASE WHEN er.is_shared = 0 THEN 1 ELSE 0 END) AS non_shared_strong_root_count,
    SUM(CASE WHEN er.is_shared = 1 THEN 1 ELSE 0 END) AS shared_strong_root_count,
    GROUP_CONCAT(DISTINCT er.role) AS strong_roles
  FROM entity_roots er
  WHERE er.weight >= 0.75
  GROUP BY er.entity_id
)
SELECT
  en.id,
  en.name,
  en.entity_type,
  en.confidence,
  en.created_at,
  en.ent_file,
  en.module,
  en.table_name,
  en.view_name,
  en.dummy,
  sr.strong_root_count,
  sr.max_root_weight,
  sr.non_shared_strong_root_count,
  sr.shared_strong_root_count,
  sr.strong_roles
FROM entity_nodes en
JOIN strong_roots sr
  ON sr.entity_id = en.id;
