CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_key TEXT NOT NULL UNIQUE,
    name TEXT,
    kind TEXT,
    language TEXT,
    remote_url TEXT,
    local_root TEXT NOT NULL,
    tracked_branch TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    profile TEXT,
    effective_builders_json TEXT NOT NULL DEFAULT '[]',
    indexed_commit_sha TEXT,
    last_scanned_at TEXT,
    last_built_at TEXT,
    index_status TEXT NOT NULL DEFAULT 'never_indexed',
    diagnostic_error TEXT,
    -- These describe the latest attempted refresh.  They deliberately do not
    -- replace the active catalog status when a candidate fails.
    last_attempt_status TEXT NOT NULL DEFAULT 'never_attempted',
    last_attempted_at TEXT,
    last_attempt_error TEXT,
    -- Lifecycle controls whether this repository may contribute evidence.
    -- ``enabled`` remains an operator scheduling setting.
    lifecycle_state TEXT NOT NULL DEFAULT 'active'
        CHECK(lifecycle_state IN ('active', 'archived')),
    archive_source TEXT CHECK(archive_source IN ('manual', 'github') OR archive_source IS NULL),
    archive_reason TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    size_bytes INTEGER,
    sha1 TEXT,
    last_modified TEXT,
    last_indexed TEXT,
    last_symbols_extracted TEXT,
    last_relationships_extracted TEXT,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    UNIQUE(repo_id, path)
);

CREATE INDEX IF NOT EXISTS idx_files_repo_path
    ON files(repo_id, path);

CREATE INDEX IF NOT EXISTS idx_files_repo_language
    ON files(repo_id, language);

-- Composite UI ownership FKs reference a file and its repository together.
-- ``id`` is globally allocated, but this explicit parent key lets SQLite
-- reject a file from another repository at write time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_files_id_repo
    ON files(id, repo_id);

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
    stable_key TEXT,
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_symbols_file_stable_key
    ON symbols(file_id, stable_key);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,

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

    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    -- Names and evidence remain useful after a symbol/file is refreshed;
    -- these are optional retained provenance, never ownership cascades.
    FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(target_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE (
        repo_id,
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

-- ``entity_nodes`` is canonical identity only.  All facts extracted from a
-- particular checkout belong to its occurrence, not to the shared name.
CREATE TABLE IF NOT EXISTS entity_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    ent_file TEXT,
    module TEXT,
    table_name TEXT,
    view_name TEXT,
    dummy INTEGER,
    source_file_id INTEGER,
    extractor TEXT,
    confidence REAL DEFAULT 1.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, entity_id),
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_occurrences_entity
    ON entity_occurrences(entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_occurrences_id_repo
    ON entity_occurrences(id, repo_id);

CREATE TABLE IF NOT EXISTS entity_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,

    entity_id INTEGER NOT NULL,

    symbol_id INTEGER,
    file_id INTEGER,

    mapping_type TEXT,
    confidence REAL,

    source_text TEXT,

    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id)
        REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entity_roots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,

    entity_id INTEGER NOT NULL,
    symbol_id INTEGER NOT NULL,

    role TEXT,
    weight REAL,
    reason TEXT,
    is_shared INTEGER DEFAULT 0,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(repo_id, entity_id, symbol_id),

    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE CASCADE
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
    repo_id INTEGER NOT NULL,

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

    UNIQUE(repo_id, entity_id, name, workflow_type, source_file),

    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_workflows_entity  ON workflows(entity_id);
CREATE INDEX IF NOT EXISTS idx_workflows_type    ON workflows(workflow_type);
CREATE INDEX IF NOT EXISTS idx_workflows_source  ON workflows(source_kind);
CREATE INDEX IF NOT EXISTS idx_workflows_file_id ON workflows(file_id);

CREATE TABLE IF NOT EXISTS workflow_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    node_kind TEXT NOT NULL,                 -- workflow | action | class | file | symbol | endpoint | openapi_ref
    node_key TEXT NOT NULL,                  -- deterministic key within workflow
    name TEXT,
    ordinal INTEGER,                         -- sequence position when applicable
    action TEXT,                             -- normalized action token when node_kind='action'
    source_kind TEXT,                        -- yaml | class | inference
    file_id INTEGER,
    symbol_id INTEGER,
    metadata_json TEXT,                      -- optional structured evidence
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    UNIQUE(workflow_id, node_kind, node_key)
);

CREATE INDEX IF NOT EXISTS idx_workflow_nodes_workflow
    ON workflow_nodes(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_entity
    ON workflow_nodes(entity_id);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_kind
    ON workflow_nodes(node_kind);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_ordinal
    ON workflow_nodes(workflow_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_file
    ON workflow_nodes(file_id);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_symbol
    ON workflow_nodes(symbol_id);


CREATE TABLE IF NOT EXISTS workflow_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    from_node_id INTEGER NOT NULL,
    to_node_id INTEGER NOT NULL,
    edge_kind TEXT NOT NULL,                 -- workflow_contains | step_next | step_uses_file | step_uses_symbol | step_exposes_endpoint | step_references_openapi_ref
    ordinal INTEGER NOT NULL DEFAULT -1,     -- sequence marker for ordered edges
    evidence TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    file_id INTEGER,
    symbol_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
    FOREIGN KEY(from_node_id) REFERENCES workflow_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(to_node_id) REFERENCES workflow_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    UNIQUE(workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence)
);

CREATE INDEX IF NOT EXISTS idx_workflow_edges_workflow
    ON workflow_edges(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_edges_from
    ON workflow_edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_workflow_edges_to
    ON workflow_edges(to_node_id);
CREATE INDEX IF NOT EXISTS idx_workflow_edges_kind
    ON workflow_edges(edge_kind);
CREATE INDEX IF NOT EXISTS idx_workflow_edges_ordinal
    ON workflow_edges(workflow_id, ordinal);


-- Shared OpenAPI file-level graph edges for reusable refs.
CREATE TABLE IF NOT EXISTS openapi_file_ref_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL,
    target_file_id INTEGER NOT NULL,
    ref_value TEXT NOT NULL,                 -- raw $ref
    ref_path TEXT,                           -- YAML location evidence
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE CASCADE,
    FOREIGN KEY(target_file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(repo_id, source_file_id, target_file_id, ref_value, ref_path)
);

CREATE INDEX IF NOT EXISTS idx_openapi_ref_source
    ON openapi_file_ref_edges(source_file_id);
CREATE INDEX IF NOT EXISTS idx_openapi_ref_target
    ON openapi_file_ref_edges(target_file_id);

CREATE TABLE IF NOT EXISTS rest_endpoints (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL,
    method TEXT,               -- GET | POST | PATCH | DELETE
    path TEXT,                 -- /services/v3/objects/ap-bill
    entity_id INTEGER,
    handler_symbol_id INTEGER,
    file_id INTEGER,
    source_version TEXT,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE SET NULL,
    FOREIGN KEY(handler_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
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
    repo_id INTEGER NOT NULL,
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
    last_seen_at TEXT,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_openapispec_file_id ON openapispec_index(file_id);

CREATE TABLE IF NOT EXISTS api_version_compatibility (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    test_version TEXT NOT NULL,
    endpoint_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'deprecated', 'disabled')),
    rationale TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    UNIQUE(repo_id, test_version, endpoint_version)
);
CREATE INDEX IF NOT EXISTS idx_api_version_compatibility_repo
    ON api_version_compatibility(repo_id);
CREATE INDEX IF NOT EXISTS idx_openapispec_module ON openapispec_index(module);
CREATE INDEX IF NOT EXISTS idx_openapispec_slug ON openapispec_index(slug);

CREATE INDEX IF NOT EXISTS idx_openapispec_x_mapped_to ON openapispec_index(x_mapped_to);

CREATE TABLE IF NOT EXISTS security_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
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
    UNIQUE(repo_id, op_key, op_numeric_id, source_file, source_kind),
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_security_operations_key ON security_operations(op_key);
CREATE INDEX IF NOT EXISTS idx_security_operations_id ON security_operations(op_numeric_id);
CREATE INDEX IF NOT EXISTS idx_security_operations_file_id ON security_operations(file_id);

CREATE TABLE IF NOT EXISTS security_operation_allowops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id INTEGER NOT NULL,
    allowed_op_key TEXT NOT NULL,
    allowed_operation_id INTEGER,
    resolution_reason TEXT,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    FOREIGN KEY(operation_id) REFERENCES security_operations(id) ON DELETE CASCADE,
    FOREIGN KEY(allowed_operation_id) REFERENCES security_operations(id) ON DELETE SET NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(operation_id, allowed_op_key, source_file)
);

CREATE INDEX IF NOT EXISTS idx_security_allowops_allowed_key
    ON security_operation_allowops(allowed_op_key);
CREATE INDEX IF NOT EXISTS idx_security_allowops_file_id
    ON security_operation_allowops(file_id);
CREATE INDEX IF NOT EXISTS idx_security_allowops_operation_id
    ON security_operation_allowops(allowed_operation_id);

CREATE TABLE IF NOT EXISTS security_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    policy_name TEXT NOT NULL,
    module TEXT,
    label TEXT,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    UNIQUE(repo_id, policy_name, source_file),
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
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
    repo_id INTEGER NOT NULL,
    module TEXT,
    menu_name TEXT,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    UNIQUE(repo_id, source_file),
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
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
    repo_id INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    primary_keys TEXT,
    source_file TEXT NOT NULL,
    file_id INTEGER,
    source_line INTEGER,
    raw_hash TEXT,
    UNIQUE(repo_id, table_name, source_file),
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL
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
    repo_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    surface TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    link_type TEXT NOT NULL,
    evidence_file_id INTEGER,
    evidence_symbol_id INTEGER,
    confidence_mode TEXT NOT NULL DEFAULT 'deterministic_exact',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(evidence_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    UNIQUE(repo_id, entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_access_links_entity_surface
    ON entity_access_links(entity_id, surface);
CREATE INDEX IF NOT EXISTS idx_entity_access_links_surface_record
    ON entity_access_links(surface, record_id);
CREATE INDEX IF NOT EXISTS idx_entity_access_links_evidence_file
    ON entity_access_links(evidence_file_id);

CREATE TABLE IF NOT EXISTS repo_index_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    tracked_branch TEXT NOT NULL,
    commit_sha TEXT,
    manifest_hash TEXT,
    builder_plan_hash TEXT,
    catalog_fingerprint TEXT,
    status TEXT NOT NULL CHECK(status IN ('building', 'validated', 'active', 'failed')),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    diagnostic_error TEXT,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_repo_index_runs_repo_started
    ON repo_index_runs(repo_id, started_at DESC);

CREATE TABLE IF NOT EXISTS repo_index_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    builder_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
    started_at TEXT,
    completed_at TEXT,
    record_count INTEGER,
    execution_mode TEXT CHECK(execution_mode IN ('full', 'delta', 'skipped')),
    invalidation_reason TEXT,
    affected_record_count INTEGER CHECK(affected_record_count IS NULL OR affected_record_count >= 0),
    result_summary TEXT,
    diagnostic_error TEXT,
    UNIQUE(run_id, builder_name),
    FOREIGN KEY(run_id) REFERENCES repo_index_runs(id) ON DELETE CASCADE
);

-- Cross-repository traversal is permitted only for an explicitly extracted,
-- evidence-backed link.  Unresolved identifiers are retained for triage.
CREATE TABLE IF NOT EXISTS integration_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_repo_id INTEGER NOT NULL,
    target_repo_id INTEGER,
    source_file_id INTEGER,
    target_file_id INTEGER,
    source_symbol_id INTEGER,
    target_symbol_id INTEGER,
    relation_type TEXT NOT NULL,
    resolution_status TEXT NOT NULL CHECK(resolution_status IN ('resolved', 'unresolved', 'ambiguous', 'invalid')),
    external_identifier TEXT,
    evidence TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source_commit_sha TEXT,
    target_commit_sha TEXT,
    extractor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at TEXT,
    FOREIGN KEY(source_repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(target_repo_id) REFERENCES repos(id) ON DELETE SET NULL,
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(target_file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(source_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(target_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_integration_links_source_repo
    ON integration_links(source_repo_id, resolution_status);
CREATE INDEX IF NOT EXISTS idx_integration_links_target_repo
    ON integration_links(target_repo_id, resolution_status);

-- REST automation coverage belongs to the same repository registry as every
-- other extracted fact.  Test-suite configuration remains operator-local in
-- the workspace manifest; these tables retain only source-backed evidence.
CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    feature_name TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    case_name TEXT NOT NULL,
    example_row INTEGER,
    feature_line INTEGER NOT NULL,
    scenario_line INTEGER NOT NULL,
    eligibility TEXT NOT NULL DEFAULT 'active'
        CHECK(eligibility IN ('active', 'known_issue', 'ci_only', 'conditional')),
    tags_json TEXT NOT NULL DEFAULT '[]',
    jira_refs_json TEXT NOT NULL DEFAULT '[]',
    source_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(repo_id, file_id, scenario_line, example_row)
);
CREATE INDEX IF NOT EXISTS idx_test_cases_repo ON test_cases(repo_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_file ON test_cases(file_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_eligibility ON test_cases(eligibility);

CREATE TABLE IF NOT EXISTS test_case_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id INTEGER NOT NULL,
    version_label TEXT NOT NULL,
    source_kind TEXT NOT NULL
        CHECK(source_kind IN ('feature_tag', 'properties', 'request_override')),
    source_file_id INTEGER,
    source_line INTEGER,
    raw_value TEXT NOT NULL,
    FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(test_case_id, version_label, source_kind, source_file_id, source_line)
);
CREATE INDEX IF NOT EXISTS idx_test_case_versions_case ON test_case_versions(test_case_id);
CREATE INDEX IF NOT EXISTS idx_test_case_versions_label ON test_case_versions(version_label);

CREATE TABLE IF NOT EXISTS test_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    step_line INTEGER NOT NULL,
    method TEXT,
    object_token TEXT,
    raw_path TEXT,
    normalized_path TEXT,
    request_version TEXT,
    expected_status INTEGER,
    operation_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK(operation_kind IN ('collection', 'item', 'child', 'workflow', 'custom', 'unknown')),
    FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    UNIQUE(test_case_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_test_requests_case ON test_requests(test_case_id);
CREATE INDEX IF NOT EXISTS idx_test_requests_route ON test_requests(method, normalized_path, request_version);

CREATE TABLE IF NOT EXISTS test_endpoint_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_request_id INTEGER NOT NULL,
    rest_endpoint_id INTEGER NOT NULL,
    compatibility_id INTEGER,
    resolution_kind TEXT NOT NULL
        CHECK(resolution_kind IN ('exact_version', 'compatible_version')),
    FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
    FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
    FOREIGN KEY(compatibility_id) REFERENCES api_version_compatibility(id) ON DELETE SET NULL,
    UNIQUE(test_request_id, rest_endpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_test_endpoint_links_endpoint ON test_endpoint_links(rest_endpoint_id);

CREATE TABLE IF NOT EXISTS test_entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_request_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    rest_endpoint_id INTEGER NOT NULL,
    FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
    UNIQUE(test_request_id, entity_id, rest_endpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_test_entity_links_entity ON test_entity_links(entity_id);

CREATE TABLE IF NOT EXISTS test_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER,
    test_case_id INTEGER,
    test_request_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    source_line INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE SET NULL,
    FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_test_diagnostics_repo_kind ON test_diagnostics(repo_id, kind);

-- Authoritative, repository-scoped semantic facts extracted from .ent files.
-- Ladybug projects these rows for traversal; it is not a second source of truth.
CREATE TABLE IF NOT EXISTS entity_schema_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    occurrence_id INTEGER NOT NULL,
    component_kind TEXT NOT NULL,
    component_path TEXT NOT NULL,
    declared_name TEXT,
    target_literal TEXT,
    data_type TEXT,
    cardinality TEXT,
    writeability TEXT,
    properties_json TEXT NOT NULL DEFAULT '{}',
    source_file_id INTEGER,
    source_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    evidence_text TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id, repo_id) REFERENCES entity_occurrences(id, repo_id),
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(repo_id, occurrence_id, component_kind, component_path, evidence_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_schema_components_id_repo
    ON entity_schema_components(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_entity_schema_components_occurrence
    ON entity_schema_components(repo_id, occurrence_id, component_kind);
CREATE INDEX IF NOT EXISTS idx_entity_schema_components_source
    ON entity_schema_components(repo_id, source_path);

CREATE TABLE IF NOT EXISTS entity_relationship_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    source_occurrence_id INTEGER NOT NULL,
    source_component_id INTEGER,
    axis TEXT NOT NULL CHECK(axis IN ('A','B','C','D','E')),
    relation_kind TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    target_occurrence_id INTEGER,
    target_entity_name TEXT,
    target_component_id INTEGER,
    target_literal TEXT,
    cardinality TEXT,
    assertion_status TEXT NOT NULL CHECK(assertion_status IN
        ('VERIFIED','CORROBORATED','UNRESOLVED','CONFLICTING')),
    qualifiers_json TEXT NOT NULL DEFAULT '{}',
    source_file_id INTEGER,
    source_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    evidence_text TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(source_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
    FOREIGN KEY(source_occurrence_id, repo_id) REFERENCES entity_occurrences(id, repo_id),
    FOREIGN KEY(source_component_id) REFERENCES entity_schema_components(id) ON DELETE SET NULL,
    FOREIGN KEY(source_component_id, repo_id) REFERENCES entity_schema_components(id, repo_id),
    FOREIGN KEY(target_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL,
    FOREIGN KEY(target_occurrence_id, repo_id) REFERENCES entity_occurrences(id, repo_id),
    FOREIGN KEY(target_component_id) REFERENCES entity_schema_components(id) ON DELETE SET NULL,
    FOREIGN KEY(target_component_id, repo_id) REFERENCES entity_schema_components(id, repo_id),
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(repo_id, fact_key, evidence_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_relationship_facts_id_repo
    ON entity_relationship_facts(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_occurrence
    ON entity_relationship_facts(repo_id, source_occurrence_id, axis);
CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_target
    ON entity_relationship_facts(repo_id, target_occurrence_id, axis);
CREATE INDEX IF NOT EXISTS idx_entity_relationship_facts_status
    ON entity_relationship_facts(repo_id, assertion_status);

CREATE TABLE IF NOT EXISTS entity_operation_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    occurrence_id INTEGER NOT NULL,
    axis TEXT NOT NULL CHECK(axis IN ('A','B','C','D','E')),
    operation TEXT NOT NULL CHECK(operation IN
        ('create','read','update','delete','approve','submit','decline')),
    surface_kind TEXT NOT NULL,
    rest_endpoint_id INTEGER,
    security_operation_id INTEGER,
    symbol_id INTEGER,
    availability TEXT NOT NULL CHECK(availability IN
        ('allowed','denied','not_declared','unresolved')),
    invocation_context TEXT NOT NULL CHECK(invocation_context IN
        ('root','child','both','any','unresolved')),
    persistence_scope TEXT NOT NULL CHECK(persistence_scope IN
        ('root','entity','shared','entity_override','unresolved')),
    standalone INTEGER CHECK(standalone IN (0,1)),
    parent_occurrence_id INTEGER,
    qualifiers_json TEXT NOT NULL DEFAULT '{}',
    source_file_id INTEGER,
    source_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    evidence_text TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id, repo_id) REFERENCES entity_occurrences(id, repo_id),
    FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE SET NULL,
    FOREIGN KEY(security_operation_id) REFERENCES security_operations(id) ON DELETE SET NULL,
    FOREIGN KEY(symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
    FOREIGN KEY(parent_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL,
    FOREIGN KEY(parent_occurrence_id, repo_id) REFERENCES entity_occurrences(id, repo_id),
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(repo_id, occurrence_id, operation, surface_kind, evidence_hash)
);
CREATE INDEX IF NOT EXISTS idx_entity_operation_facts_occurrence
    ON entity_operation_facts(repo_id, occurrence_id, operation);

CREATE TABLE IF NOT EXISTS entity_extraction_coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    occurrence_id INTEGER NOT NULL,
    source_file_id INTEGER,
    source_path TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    declaration_family TEXT NOT NULL CHECK(declaration_family IN ('A','B','C','D','E','components','operations')),
    source_hash TEXT,
    status TEXT NOT NULL CHECK(status IN ('complete','partial','failed','not_applicable')),
    component_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0,
    diagnostic TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
    FOREIGN KEY(occurrence_id, repo_id) REFERENCES entity_occurrences(id, repo_id),
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(repo_id, occurrence_id, extractor, extractor_version, declaration_family, source_hash)
);
CREATE INDEX IF NOT EXISTS idx_entity_extraction_coverage_occurrence
    ON entity_extraction_coverage(repo_id, occurrence_id, declaration_family);

CREATE TABLE IF NOT EXISTS entity_semantic_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    fact_key TEXT NOT NULL,
    left_fact_id INTEGER NOT NULL,
    right_fact_id INTEGER NOT NULL,
    conflict_kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','resolved','accepted_unresolved')),
    reason TEXT NOT NULL,
    resolution_evidence TEXT,
    source_file_id INTEGER,
    source_path TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(left_fact_id) REFERENCES entity_relationship_facts(id) ON DELETE CASCADE,
    FOREIGN KEY(left_fact_id, repo_id) REFERENCES entity_relationship_facts(id, repo_id),
    FOREIGN KEY(right_fact_id) REFERENCES entity_relationship_facts(id) ON DELETE CASCADE,
    FOREIGN KEY(right_fact_id, repo_id) REFERENCES entity_relationship_facts(id, repo_id),
    FOREIGN KEY(source_file_id) REFERENCES files(id) ON DELETE SET NULL,
    UNIQUE(repo_id, fact_key, left_fact_id, right_fact_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_semantic_conflicts_fact_key
    ON entity_semantic_conflicts(repo_id, fact_key, status);

-- Authoritative UI evidence.  A surface is a user-visible actionUI form or a
-- NextGen family.  Its source files, entity links, parsed controls, events,
-- script dependencies, and resolution diagnostics are retained separately so
-- callers can distinguish direct evidence from unresolved behavior.
CREATE TABLE IF NOT EXISTS ui_surfaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    surface_key TEXT NOT NULL,
    surface_kind TEXT NOT NULL CHECK(surface_kind IN ('actionui_form', 'nextgen')),
    display_name TEXT,
    source_file_id INTEGER,
    source_path TEXT,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    source_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(source_file_id, repo_id) REFERENCES files(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, surface_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ui_surfaces_id_repo
    ON ui_surfaces(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_ui_surfaces_repo_kind
    ON ui_surfaces(repo_id, surface_kind, surface_key);

CREATE TABLE IF NOT EXISTS ui_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    surface_id INTEGER NOT NULL,
    artifact_key TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    file_id INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    evidence_text TEXT,
    source_hash TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(surface_id, repo_id) REFERENCES ui_surfaces(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(file_id, repo_id) REFERENCES files(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, surface_id, artifact_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ui_artifacts_id_repo
    ON ui_artifacts(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_ui_artifacts_surface_kind
    ON ui_artifacts(repo_id, surface_id, artifact_kind);
CREATE INDEX IF NOT EXISTS idx_ui_artifacts_file
    ON ui_artifacts(repo_id, file_id);

CREATE TABLE IF NOT EXISTS ui_entity_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    surface_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    entity_occurrence_id INTEGER NOT NULL,
    evidence_artifact_id INTEGER NOT NULL,
    reference_kind TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    evidence_text TEXT NOT NULL,
    source_line INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(surface_id, repo_id) REFERENCES ui_surfaces(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_occurrence_id, repo_id)
        REFERENCES entity_occurrences(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(evidence_artifact_id, repo_id)
        REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, surface_id, entity_occurrence_id, evidence_artifact_id, reference_kind)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ui_entity_references_id_repo
    ON ui_entity_references(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_ui_entity_references_entity
    ON ui_entity_references(repo_id, entity_id, reference_kind);
CREATE INDEX IF NOT EXISTS idx_ui_entity_references_surface
    ON ui_entity_references(repo_id, surface_id);

CREATE TABLE IF NOT EXISTS ui_artifact_includes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    source_artifact_id INTEGER NOT NULL,
    target_artifact_id INTEGER,
    include_key TEXT NOT NULL,
    raw_include_path TEXT NOT NULL,
    resolved_path TEXT,
    resolution_status TEXT NOT NULL CHECK(resolution_status IN
        ('resolved', 'unresolved', 'invalid')),
    source_line INTEGER,
    evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(source_artifact_id, repo_id)
        REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(target_artifact_id, repo_id)
        REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, source_artifact_id, include_key)
);
CREATE INDEX IF NOT EXISTS idx_ui_artifact_includes_target
    ON ui_artifact_includes(repo_id, target_artifact_id);

CREATE TABLE IF NOT EXISTS ui_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,
    field_key TEXT NOT NULL,
    field_name TEXT,
    field_path TEXT,
    label TEXT,
    field_type TEXT,
    ordinal INTEGER,
    source_line INTEGER,
    evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(artifact_id, repo_id) REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, artifact_id, field_key)
);
CREATE INDEX IF NOT EXISTS idx_ui_fields_artifact
    ON ui_fields(repo_id, artifact_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_ui_fields_name
    ON ui_fields(repo_id, field_name);

CREATE TABLE IF NOT EXISTS ui_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    handler_name TEXT,
    handler_expression TEXT,
    source_line INTEGER,
    evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(artifact_id, repo_id) REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, artifact_id, event_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ui_events_id_repo
    ON ui_events(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_ui_events_artifact_type
    ON ui_events(repo_id, artifact_id, event_type);

CREATE TABLE IF NOT EXISTS ui_script_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    surface_id INTEGER NOT NULL,
    source_artifact_id INTEGER NOT NULL,
    dependency_key TEXT NOT NULL,
    script_path TEXT,
    target_file_id INTEGER,
    load_scope TEXT NOT NULL CHECK(load_scope IN ('active', 'conditional', 'unresolved')),
    resolution_status TEXT NOT NULL CHECK(resolution_status IN
        ('resolved', 'unresolved', 'invalid')),
    evidence_text TEXT NOT NULL,
    source_line INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(surface_id, repo_id) REFERENCES ui_surfaces(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(source_artifact_id, repo_id)
        REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(target_file_id, repo_id) REFERENCES files(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, surface_id, source_artifact_id, dependency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ui_script_dependencies_id_repo
    ON ui_script_dependencies(id, repo_id);
CREATE INDEX IF NOT EXISTS idx_ui_script_dependencies_surface
    ON ui_script_dependencies(repo_id, surface_id, load_scope);
CREATE INDEX IF NOT EXISTS idx_ui_script_dependencies_target
    ON ui_script_dependencies(repo_id, target_file_id);

CREATE TABLE IF NOT EXISTS ui_event_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    -- A negative handler-resolution outcome has an XML event but no single
    -- proving dependency. Retain that evidence rather than manufacturing one.
    dependency_id INTEGER,
    call_key TEXT NOT NULL,
    handler_name TEXT NOT NULL,
    handler_symbol_id INTEGER,
    resolution_status TEXT NOT NULL CHECK(resolution_status IN
        ('resolved', 'unresolved', 'ambiguous', 'conditional', 'unsupported')),
    resolution_reason TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(event_id, repo_id) REFERENCES ui_events(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(dependency_id, repo_id)
        REFERENCES ui_script_dependencies(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(handler_symbol_id) REFERENCES symbols(id) ON DELETE CASCADE,
    UNIQUE(repo_id, event_id, dependency_id, call_key)
);
CREATE INDEX IF NOT EXISTS idx_ui_event_calls_event
    ON ui_event_calls(repo_id, event_id, resolution_status);
CREATE INDEX IF NOT EXISTS idx_ui_event_calls_handler
    ON ui_event_calls(handler_symbol_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ui_event_calls_without_dependency
    ON ui_event_calls(repo_id, event_id, call_key)
    WHERE dependency_id IS NULL;

CREATE TABLE IF NOT EXISTS ui_resolution_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    surface_id INTEGER NOT NULL,
    artifact_id INTEGER,
    event_id INTEGER,
    dependency_id INTEGER,
    issue_key TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('warning', 'error')),
    issue_code TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY(surface_id, repo_id) REFERENCES ui_surfaces(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(artifact_id, repo_id) REFERENCES ui_artifacts(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(event_id, repo_id) REFERENCES ui_events(id, repo_id) ON DELETE CASCADE,
    FOREIGN KEY(dependency_id, repo_id)
        REFERENCES ui_script_dependencies(id, repo_id) ON DELETE CASCADE,
    UNIQUE(repo_id, surface_id, issue_key)
);
CREATE INDEX IF NOT EXISTS idx_ui_resolution_issues_surface
    ON ui_resolution_issues(repo_id, surface_id, severity);

-- SQLite cannot derive a symbol's repository through ``symbols.file_id`` in a
-- declarative FK.  These triggers make that ownership check explicit.
CREATE TRIGGER IF NOT EXISTS trg_ui_entity_references_entity_occurrence_insert
BEFORE INSERT ON ui_entity_references
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM entity_occurrences
    WHERE id = NEW.entity_occurrence_id
      AND repo_id = NEW.repo_id
      AND entity_id = NEW.entity_id
)
BEGIN
    SELECT RAISE(ABORT, 'ui entity reference must use an occurrence for the same repository and entity');
END;

CREATE TRIGGER IF NOT EXISTS trg_ui_entity_references_entity_occurrence_update
BEFORE UPDATE OF repo_id, entity_id, entity_occurrence_id ON ui_entity_references
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM entity_occurrences
    WHERE id = NEW.entity_occurrence_id
      AND repo_id = NEW.repo_id
      AND entity_id = NEW.entity_id
)
BEGIN
    SELECT RAISE(ABORT, 'ui entity reference must use an occurrence for the same repository and entity');
END;

CREATE TRIGGER IF NOT EXISTS trg_ui_event_calls_symbol_repo_insert
BEFORE INSERT ON ui_event_calls
FOR EACH ROW
WHEN NEW.handler_symbol_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM symbols
    JOIN files ON files.id = symbols.file_id
    WHERE symbols.id = NEW.handler_symbol_id AND files.repo_id = NEW.repo_id
)
BEGIN
    SELECT RAISE(ABORT, 'ui event call handler symbol belongs to another repository');
END;

CREATE TRIGGER IF NOT EXISTS trg_ui_event_calls_symbol_repo_update
BEFORE UPDATE OF repo_id, handler_symbol_id ON ui_event_calls
FOR EACH ROW
WHEN NEW.handler_symbol_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM symbols
    JOIN files ON files.id = symbols.file_id
    WHERE symbols.id = NEW.handler_symbol_id AND files.repo_id = NEW.repo_id
)
BEGIN
    SELECT RAISE(ABORT, 'ui event call handler symbol belongs to another repository');
END;

CREATE TRIGGER IF NOT EXISTS trg_ui_event_calls_surface_match_insert
BEFORE INSERT ON ui_event_calls
FOR EACH ROW
WHEN NEW.dependency_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM ui_events event
    JOIN ui_artifacts artifact ON artifact.id = event.artifact_id
    JOIN ui_script_dependencies dependency ON dependency.id = NEW.dependency_id
    WHERE event.id = NEW.event_id
      AND artifact.surface_id = dependency.surface_id
      AND event.repo_id = NEW.repo_id
      AND dependency.repo_id = NEW.repo_id
)
BEGIN
    SELECT RAISE(ABORT, 'ui event call event and dependency must belong to one surface');
END;

CREATE TRIGGER IF NOT EXISTS trg_ui_event_calls_surface_match_update
BEFORE UPDATE OF repo_id, event_id, dependency_id ON ui_event_calls
FOR EACH ROW
WHEN NEW.dependency_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM ui_events event
    JOIN ui_artifacts artifact ON artifact.id = event.artifact_id
    JOIN ui_script_dependencies dependency ON dependency.id = NEW.dependency_id
    WHERE event.id = NEW.event_id
      AND artifact.surface_id = dependency.surface_id
      AND event.repo_id = NEW.repo_id
      AND dependency.repo_id = NEW.repo_id
)
BEGIN
    SELECT RAISE(ABORT, 'ui event call event and dependency must belong to one surface');
END;


-- Advisory quality/triage view only. It intentionally excludes entities without
-- roots at or above the confidence threshold and must not filter authoritative
-- catalog queries or the complete Ladybug projection.
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
  sr.strong_root_count,
  sr.max_root_weight,
  sr.non_shared_strong_root_count,
  sr.shared_strong_root_count,
  sr.strong_roles
FROM entity_nodes en
JOIN strong_roots sr
  ON sr.entity_id = en.id;


CREATE TABLE IF NOT EXISTS catalog_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_token TEXT NOT NULL UNIQUE,
    parent_catalog_build_id INTEGER,
    catalog_path TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN ('full', 'auto', 'delta', 'archive')),
    effective_mode TEXT NOT NULL CHECK(effective_mode IN ('not_started', 'full', 'delta', 'hybrid', 'archive')),
    status TEXT NOT NULL CHECK(status IN ('building', 'validated', 'active', 'previous', 'failed')),
    source_revisions_json TEXT NOT NULL,
    manifest_hash TEXT,
    builder_plan_hash TEXT,
    delta_contract_version INTEGER NOT NULL,
    runtime_fingerprint TEXT,
    content_fingerprint TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    diagnostic_error TEXT,
    FOREIGN KEY(parent_catalog_build_id) REFERENCES catalog_builds(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_builds_active
    ON catalog_builds(status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_catalog_builds_status_started
    ON catalog_builds(status, started_at);

CREATE TABLE IF NOT EXISTS repo_change_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_build_id INTEGER NOT NULL,
    repo_index_run_id INTEGER NOT NULL,
    repo_id INTEGER NOT NULL,
    base_commit_sha TEXT,
    target_commit_sha TEXT NOT NULL,
    requested_mode TEXT NOT NULL CHECK(requested_mode IN ('full', 'auto', 'delta')),
    effective_mode TEXT NOT NULL CHECK(effective_mode IN ('full', 'delta', 'noop')),
    status TEXT NOT NULL CHECK(status IN ('planned', 'running', 'succeeded', 'failed')),
    fallback_reason TEXT,
    added_count INTEGER NOT NULL DEFAULT 0 CHECK(added_count >= 0),
    modified_count INTEGER NOT NULL DEFAULT 0 CHECK(modified_count >= 0),
    deleted_count INTEGER NOT NULL DEFAULT 0 CHECK(deleted_count >= 0),
    renamed_count INTEGER NOT NULL DEFAULT 0 CHECK(renamed_count >= 0),
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(catalog_build_id) REFERENCES catalog_builds(id) ON DELETE CASCADE,
    FOREIGN KEY(repo_index_run_id) REFERENCES repo_index_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_repo_change_sets_repo_target
    ON repo_change_sets(repo_id, target_commit_sha);
CREATE INDEX IF NOT EXISTS idx_repo_change_sets_build_repo
    ON repo_change_sets(catalog_build_id, repo_id);

CREATE TABLE IF NOT EXISTS repo_changed_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_set_id INTEGER NOT NULL,
    change_type TEXT NOT NULL CHECK(change_type IN ('added', 'modified', 'deleted', 'renamed')),
    old_path TEXT,
    new_path TEXT,
    old_mode INTEGER,
    new_mode INTEGER,
    old_blob_sha TEXT,
    new_blob_sha TEXT,
    rename_score INTEGER CHECK(rename_score IS NULL OR (rename_score >= 0 AND rename_score <= 100)),
    FOREIGN KEY(change_set_id) REFERENCES repo_change_sets(id) ON DELETE CASCADE,
    CHECK(
        (change_type = 'added' AND old_path IS NULL AND new_path IS NOT NULL) OR
        (change_type = 'modified' AND old_path IS NOT NULL AND new_path IS NOT NULL) OR
        (change_type = 'deleted' AND old_path IS NOT NULL AND new_path IS NULL) OR
        (change_type = 'renamed' AND old_path IS NOT NULL AND new_path IS NOT NULL AND old_path <> new_path)
    )
);
CREATE INDEX IF NOT EXISTS idx_repo_changed_paths_change_set
    ON repo_changed_paths(change_set_id);

CREATE TABLE IF NOT EXISTS graph_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_path TEXT NOT NULL,
    source_db TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('building', 'validated', 'active', 'previous', 'failed')),
    source_fingerprint TEXT NOT NULL,
    catalog_build_id INTEGER,
    base_graph_build_id INTEGER,
    build_mode TEXT NOT NULL DEFAULT 'full' CHECK(build_mode IN ('full', 'delta')),
    projection_version INTEGER,
    source_revisions_json TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT,
    error TEXT,
    FOREIGN KEY(catalog_build_id) REFERENCES catalog_builds(id) ON DELETE SET NULL,
    FOREIGN KEY(base_graph_build_id) REFERENCES graph_builds(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_builds_status_started
    ON graph_builds(status, started_at);
CREATE INDEX IF NOT EXISTS idx_graph_builds_catalog
    ON graph_builds(catalog_build_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_builds_active_path
    ON graph_builds(graph_path) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO schema_migrations(name) VALUES
    ('019_multi_repo'),
    ('020_rest_automation_coverage'),
    ('021_entity_semantics'),
    ('022_entity_semantics_repo_scope'),
    ('023_delta_refresh'),
    ('024_refresh_contracts'),
    ('025_delta_refresh_hardening'),
    ('026_ui_catalog'),
    ('027_ui_negative_event_calls'),
    ('029_repository_archival');
