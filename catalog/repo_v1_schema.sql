PRAGMA foreign_keys = ON;

-- The V1 development catalog is intentionally independent of catalog/schema.sql.
-- It contains only immutable repo-v1 facts and their source diagnostics.
CREATE TABLE catalog_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_token TEXT NOT NULL UNIQUE,
    catalog_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('building', 'validated', 'active')),
    source_revisions_json TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    validation_summary TEXT
);

CREATE UNIQUE INDEX uq_repo_v1_active_build
    ON catalog_builds(status) WHERE status = 'active';

CREATE TABLE repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_key TEXT NOT NULL UNIQUE,
    name TEXT,
    kind TEXT,
    language TEXT,
    remote_url TEXT,
    local_root TEXT NOT NULL,
    tracked_branch TEXT NOT NULL,
    target_commit_sha TEXT NOT NULL,
    build_id INTEGER NOT NULL,
    FOREIGN KEY (build_id) REFERENCES catalog_builds(id) ON DELETE CASCADE
);

CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    blob_object_id TEXT NOT NULL,
    file_mode INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    language TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    UNIQUE (repo_id, path)
);

CREATE INDEX idx_repo_v1_files_repo_path ON files(repo_id, path);
CREATE UNIQUE INDEX uq_repo_v1_files_repo_id_id ON files(repo_id, id);

CREATE TABLE entity_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE CHECK(name <> '')
);

CREATE TABLE entity_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL,
    source_key TEXT NOT NULL CHECK(source_key <> ''),
    module TEXT,
    table_name TEXT,
    view_name TEXT,
    dummy INTEGER CHECK(dummy IS NULL OR dummy IN (0,1)),
    source_commit_sha TEXT NOT NULL CHECK(source_commit_sha <> ''),
    evidence TEXT NOT NULL CHECK(evidence <> ''),
    extractor TEXT NOT NULL CHECK(extractor <> ''),
    UNIQUE(repo_id, source_file_id, source_key),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (source_file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX idx_repo_v1_entity_occurrences_repo_file
    ON entity_occurrences(repo_id, source_file_id);
CREATE INDEX idx_repo_v1_entity_occurrences_entity
    ON entity_occurrences(entity_id);
CREATE INDEX idx_repo_v1_entity_occurrences_source_key
    ON entity_occurrences(source_key);

CREATE TABLE entity_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    source_key TEXT NULL,
    occurrence_id INTEGER NULL,
    diagnostic_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL CHECK(severity = 'error'),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL CHECK(source_commit_sha <> ''),
    evidence TEXT NOT NULL CHECK(evidence <> ''),
    extractor TEXT NOT NULL CHECK(extractor <> ''),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    FOREIGN KEY (occurrence_id) REFERENCES entity_occurrences(id) ON DELETE SET NULL
);

CREATE INDEX idx_repo_v1_entity_diagnostics_repo_file
    ON entity_diagnostics(repo_id, file_id);
CREATE INDEX idx_repo_v1_entity_diagnostics_occurrence
    ON entity_diagnostics(occurrence_id);
CREATE INDEX idx_repo_v1_entity_diagnostics_source_key
    ON entity_diagnostics(source_key);
CREATE INDEX idx_repo_v1_entity_diagnostics_lookup
    ON entity_diagnostics(repo_id, code, diagnostic_key);

CREATE TABLE symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL CHECK (name <> ''),
    kind TEXT NOT NULL CHECK (kind <> ''),
    parent_symbol TEXT,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,
    language TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE (file_id, stable_key)
);

CREATE INDEX idx_repo_v1_symbols_repo_file ON symbols(repo_id, file_id);

CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    source_symbol_id INTEGER,
    source_name TEXT,
    source_kind TEXT,
    target_symbol_id INTEGER,
    target_name TEXT NOT NULL CHECK (target_name <> ''),
    target_kind TEXT,
    relationship_type TEXT NOT NULL CHECK (relationship_type <> ''),
    file_id INTEGER NOT NULL,
    file_path TEXT NOT NULL CHECK (file_path <> ''),
    language TEXT NOT NULL CHECK (language <> ''),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence TEXT NOT NULL CHECK (evidence <> ''),
    resolution_class TEXT NOT NULL CHECK (resolution_class <> ''),
    resolution_reason TEXT NOT NULL CHECK (resolution_reason <> ''),
    extractor TEXT NOT NULL CHECK (extractor <> ''),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (source_symbol_id) REFERENCES symbols(id) ON DELETE CASCADE,
    FOREIGN KEY (target_symbol_id) REFERENCES symbols(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE (
        repo_id,source_symbol_id,source_name,target_symbol_id,target_name,
        relationship_type,file_path,evidence
    )
);

CREATE INDEX idx_repo_v1_relationships_repo_file
    ON relationships(repo_id, file_id);
CREATE INDEX idx_repo_v1_relationships_source_symbol
    ON relationships(source_symbol_id);
CREATE INDEX idx_repo_v1_relationships_target_symbol
    ON relationships(target_symbol_id);
CREATE INDEX idx_repo_v1_relationships_resolution
    ON relationships(resolution_class);

CREATE TABLE symbol_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    diagnostic_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL CHECK (severity = 'error'),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL,
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX idx_repo_v1_symbol_diagnostics_repo_file
    ON symbol_diagnostics(repo_id, file_id);

CREATE TABLE openapi_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN (
        'history','schema','operations','view','uimeta','viewmeta','paths',
        'components','security','resource','actions','events','unknown'
    )),
    document_key TEXT NOT NULL UNIQUE,
    source_commit_sha TEXT NOT NULL CHECK(source_commit_sha <> ''),
    evidence TEXT NOT NULL CHECK(evidence <> ''),
    extractor TEXT NOT NULL CHECK(extractor <> ''),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    UNIQUE(repo_id, file_id),
    UNIQUE(repo_id, path)
);

CREATE INDEX idx_repo_v1_openapi_documents_repo_path
    ON openapi_documents(repo_id, path);

CREATE TABLE openapi_entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    entity_occurrence_id INTEGER NOT NULL,
    mapped_value TEXT NOT NULL CHECK(mapped_value <> ''),
    match_key TEXT NOT NULL CHECK(match_key <> ''),
    link_key TEXT NOT NULL UNIQUE,
    source_commit_sha TEXT NOT NULL CHECK(source_commit_sha <> ''),
    evidence TEXT NOT NULL CHECK(evidence <> ''),
    extractor TEXT NOT NULL CHECK(extractor <> ''),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES openapi_documents(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_occurrence_id) REFERENCES entity_occurrences(id) ON DELETE CASCADE,
    UNIQUE(repo_id, document_id, entity_occurrence_id)
);

CREATE INDEX idx_repo_v1_openapi_entity_links_repo_document
    ON openapi_entity_links(repo_id, document_id);
CREATE INDEX idx_repo_v1_openapi_entity_links_occurrence
    ON openapi_entity_links(entity_occurrence_id);

CREATE TABLE rest_endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    endpoint_key TEXT NOT NULL UNIQUE,
    path_template TEXT NOT NULL CHECK(path_template LIKE '/%'),
    http_method TEXT NOT NULL CHECK(http_method IN (
        'get','post','put','patch','delete','head','options','trace'
    )),
    operation_id TEXT,
    source_pointer TEXT NOT NULL CHECK(source_pointer LIKE '/%'),
    source_commit_sha TEXT NOT NULL CHECK(source_commit_sha <> ''),
    evidence TEXT NOT NULL CHECK(evidence <> ''),
    extractor TEXT NOT NULL CHECK(extractor <> ''),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES openapi_documents(id) ON DELETE CASCADE,
    UNIQUE(repo_id, document_id, path_template, http_method)
);

CREATE INDEX idx_repo_v1_rest_endpoints_repo_document
    ON rest_endpoints(repo_id, document_id);
CREATE INDEX idx_repo_v1_rest_endpoints_route
    ON rest_endpoints(path_template, http_method);

CREATE TABLE openapi_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    document_id INTEGER,
    phase TEXT NOT NULL CHECK(phase IN ('6A','6B','6C')),
    diagnostic_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL CHECK(severity = 'error'),
    code TEXT NOT NULL CHECK(code <> ''),
    message TEXT NOT NULL CHECK(message <> ''),
    source_commit_sha TEXT NOT NULL CHECK(source_commit_sha <> ''),
    evidence TEXT NOT NULL CHECK(evidence <> ''),
    extractor TEXT NOT NULL CHECK(extractor <> ''),
    FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES openapi_documents(id) ON DELETE CASCADE
);

CREATE INDEX idx_repo_v1_openapi_diagnostics_repo_file
    ON openapi_diagnostics(repo_id, file_id);
CREATE INDEX idx_repo_v1_openapi_diagnostics_document
    ON openapi_diagnostics(document_id);
CREATE INDEX idx_repo_v1_openapi_diagnostics_lookup
    ON openapi_diagnostics(repo_id, code, diagnostic_key);

CREATE TABLE ui_surfaces (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    surface_key TEXT NOT NULL CHECK(TRIM(surface_key) <> ''),
    surface_kind TEXT NOT NULL CHECK(surface_kind = 'actionui_form'),
    display_name TEXT NOT NULL CHECK(TRIM(display_name) <> ''),
    source_file_id INTEGER NOT NULL REFERENCES files(id),
    source_path TEXT NOT NULL CHECK(TRIM(source_path) <> ''),
    source_commit_sha TEXT NOT NULL CHECK(TRIM(source_commit_sha) <> ''),
    extractor TEXT NOT NULL CHECK(TRIM(extractor) <> ''),
    extractor_version TEXT NOT NULL CHECK(TRIM(extractor_version) <> ''),
    source_hash TEXT NOT NULL CHECK(TRIM(source_hash) <> ''),
    UNIQUE(repo_id, surface_key)
);

CREATE INDEX idx_repo_v1_ui_surfaces_repo_file
    ON ui_surfaces(repo_id, source_file_id);
CREATE INDEX idx_repo_v1_ui_surfaces_repo_path
    ON ui_surfaces(repo_id, source_path);

CREATE TABLE ui_artifacts (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    surface_id INTEGER NOT NULL REFERENCES ui_surfaces(id),
    artifact_key TEXT NOT NULL CHECK(TRIM(artifact_key) <> ''),
    artifact_kind TEXT NOT NULL CHECK(artifact_kind = 'actionui_form'),
    file_id INTEGER NOT NULL REFERENCES files(id),
    source_path TEXT NOT NULL CHECK(TRIM(source_path) <> ''),
    source_commit_sha TEXT NOT NULL CHECK(TRIM(source_commit_sha) <> ''),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    UNIQUE(repo_id, surface_id, artifact_key)
);

CREATE INDEX idx_repo_v1_ui_artifacts_repo_surface
    ON ui_artifacts(repo_id, surface_id);
CREATE INDEX idx_repo_v1_ui_artifacts_repo_file
    ON ui_artifacts(repo_id, file_id);
CREATE INDEX idx_repo_v1_ui_artifacts_repo_path
    ON ui_artifacts(repo_id, source_path);

CREATE TABLE ui_fields (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    artifact_id INTEGER NOT NULL REFERENCES ui_artifacts(id),
    field_key TEXT NOT NULL CHECK(TRIM(field_key) <> ''),
    field_name TEXT NOT NULL CHECK(TRIM(field_name) <> ''),
    field_path TEXT,
    source_line INTEGER NOT NULL CHECK(source_line >= 1),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    UNIQUE(repo_id, artifact_id, field_key)
);

CREATE INDEX idx_repo_v1_ui_fields_repo_artifact
    ON ui_fields(repo_id, artifact_id);

CREATE TABLE ui_events (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    artifact_id INTEGER NOT NULL REFERENCES ui_artifacts(id),
    event_key TEXT NOT NULL CHECK(TRIM(event_key) <> ''),
    event_name TEXT NOT NULL CHECK(TRIM(event_name) <> ''),
    source_line INTEGER NOT NULL CHECK(source_line >= 1),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    UNIQUE(repo_id, artifact_id, event_key)
);

CREATE INDEX idx_repo_v1_ui_events_repo_artifact
    ON ui_events(repo_id, artifact_id);

CREATE TABLE ui_includes (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    artifact_id INTEGER NOT NULL REFERENCES ui_artifacts(id),
    include_key TEXT NOT NULL CHECK(TRIM(include_key) <> ''),
    raw_include_path TEXT NOT NULL CHECK(TRIM(raw_include_path) <> ''),
    resolved_path TEXT,
    resolution_status TEXT NOT NULL CHECK(
        resolution_status IN ('resolved', 'unresolved', 'invalid')
    ),
    source_line INTEGER NOT NULL CHECK(source_line >= 1),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    UNIQUE(repo_id, artifact_id, include_key),
    CHECK(
        (resolution_status = 'invalid' AND resolved_path IS NULL)
        OR
        (resolution_status IN ('resolved', 'unresolved')
         AND resolved_path IS NOT NULL
         AND TRIM(resolved_path) <> '')
    )
);

CREATE INDEX idx_repo_v1_ui_includes_repo_artifact
    ON ui_includes(repo_id, artifact_id);

CREATE TABLE ui_diagnostics (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    file_id INTEGER NOT NULL REFERENCES files(id),
    surface_id INTEGER REFERENCES ui_surfaces(id),
    diagnostic_key TEXT NOT NULL CHECK(TRIM(diagnostic_key) <> ''),
    severity TEXT NOT NULL CHECK(severity IN ('warning', 'error')),
    code TEXT NOT NULL CHECK(TRIM(code) <> ''),
    message TEXT NOT NULL CHECK(TRIM(message) <> ''),
    source_commit_sha TEXT NOT NULL CHECK(TRIM(source_commit_sha) <> ''),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    extractor TEXT NOT NULL CHECK(TRIM(extractor) <> ''),
    UNIQUE(repo_id, diagnostic_key)
);

CREATE INDEX idx_repo_v1_ui_diagnostics_repo_file
    ON ui_diagnostics(repo_id, file_id);
CREATE INDEX idx_repo_v1_ui_diagnostics_lookup
    ON ui_diagnostics(repo_id, code, diagnostic_key);

CREATE TABLE nextgen_families (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    family_key TEXT NOT NULL CHECK(TRIM(family_key) <> ''),
    source_file_id INTEGER NOT NULL,
    source_path TEXT NOT NULL CHECK(TRIM(source_path) <> ''),
    source_commit_sha TEXT NOT NULL CHECK(TRIM(source_commit_sha) <> ''),
    source_hash TEXT NOT NULL CHECK(TRIM(source_hash) <> ''),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    extractor TEXT NOT NULL CHECK(TRIM(extractor) <> ''),
    extractor_version TEXT NOT NULL CHECK(TRIM(extractor_version) <> ''),
    UNIQUE(repo_id, family_key),
    UNIQUE(repo_id, id),
    FOREIGN KEY (repo_id, source_file_id) REFERENCES files(repo_id, id)
);

CREATE INDEX idx_repo_v1_nextgen_families_repo_file
    ON nextgen_families(repo_id, source_file_id);
CREATE INDEX idx_repo_v1_nextgen_families_repo_path
    ON nextgen_families(repo_id, source_path);

CREATE TABLE nextgen_artifacts (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    family_id INTEGER NOT NULL,
    artifact_key TEXT NOT NULL CHECK(TRIM(artifact_key) <> ''),
    artifact_kind TEXT NOT NULL CHECK(artifact_kind IN ('uimeta', 'viewmeta', 'view')),
    file_id INTEGER NOT NULL,
    source_path TEXT NOT NULL CHECK(TRIM(source_path) <> ''),
    source_commit_sha TEXT NOT NULL CHECK(TRIM(source_commit_sha) <> ''),
    source_hash TEXT NOT NULL CHECK(TRIM(source_hash) <> ''),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    extractor TEXT NOT NULL CHECK(TRIM(extractor) <> ''),
    extractor_version TEXT NOT NULL CHECK(TRIM(extractor_version) <> ''),
    UNIQUE(repo_id, family_id, artifact_key),
    FOREIGN KEY (repo_id, family_id) REFERENCES nextgen_families(repo_id, id),
    FOREIGN KEY (repo_id, file_id) REFERENCES files(repo_id, id)
);

CREATE INDEX idx_repo_v1_nextgen_artifacts_repo_family
    ON nextgen_artifacts(repo_id, family_id);
CREATE INDEX idx_repo_v1_nextgen_artifacts_repo_file
    ON nextgen_artifacts(repo_id, file_id);
CREATE INDEX idx_repo_v1_nextgen_artifacts_repo_path
    ON nextgen_artifacts(repo_id, source_path);

CREATE TABLE nextgen_diagnostics (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id),
    file_id INTEGER NOT NULL,
    diagnostic_key TEXT NOT NULL CHECK(TRIM(diagnostic_key) <> ''),
    severity TEXT NOT NULL CHECK(severity IN ('warning', 'error')),
    code TEXT NOT NULL CHECK(code IN (
        'nextgen.yaml.invalid',
        'nextgen.yaml.document_not_mapping',
        'nextgen.family.invalid_object',
        'nextgen.family.unresolved'
    )),
    message TEXT NOT NULL CHECK(TRIM(message) <> ''),
    source_commit_sha TEXT NOT NULL CHECK(TRIM(source_commit_sha) <> ''),
    source_hash TEXT NOT NULL CHECK(TRIM(source_hash) <> ''),
    start_line INTEGER NOT NULL CHECK(start_line >= 1),
    end_line INTEGER NOT NULL CHECK(end_line >= start_line),
    evidence TEXT NOT NULL CHECK(TRIM(evidence) <> ''),
    extractor TEXT NOT NULL CHECK(TRIM(extractor) <> ''),
    extractor_version TEXT NOT NULL CHECK(TRIM(extractor_version) <> ''),
    UNIQUE(repo_id, diagnostic_key),
    FOREIGN KEY (repo_id, file_id) REFERENCES files(repo_id, id)
);

CREATE INDEX idx_repo_v1_nextgen_diagnostics_repo_file
    ON nextgen_diagnostics(repo_id, file_id);
CREATE INDEX idx_repo_v1_nextgen_diagnostics_lookup
    ON nextgen_diagnostics(repo_id, code, diagnostic_key);
