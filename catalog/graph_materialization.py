"""Ladybug schema creation and SQLite-to-graph materialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import ladybug as lb
import polars as pd
from tqdm import tqdm

from catalog.graph_projection import (
    Projection,
    iter_available_node_projections,
    iter_available_relationship_projections,
)

NODE_CHUNK_SIZE = 50000
EDGE_ROW_CHUNK_SIZE = 50000


def _copy_projection(
    sql: sqlite3.Connection,
    g: lb.Connection,
    projection: Projection,
    *,
    chunk_size: int,
) -> None:
    cursor = sql.execute(projection.source_query)
    columns = (
        list(projection.property_keys)
        if projection.kind == "node"
        else ["FROM", "TO", *projection.property_keys]
    )
    with tqdm(desc=f"COPY {projection.ladybug_table}", unit="row") as pbar:
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            if any(len(row) != len(columns) for row in rows):
                raise RuntimeError(
                    f"projection column mismatch for {projection.ladybug_table}: "
                    f"expected {len(columns)}"
                )
            df = pd.DataFrame(
                rows,
                schema=columns,
                orient="row",
                infer_schema_length=None,
            )
            _ = df
            g.execute(f"COPY {projection.ladybug_table} FROM df")
            pbar.update(len(rows))


def load_node_projections(sql: sqlite3.Connection, g: lb.Connection) -> None:
    """Load every node from the canonical projection registry."""

    for projection in iter_available_node_projections(sql):
        _copy_projection(sql, g, projection, chunk_size=NODE_CHUNK_SIZE)


def load_relationship_projections(sql: sqlite3.Connection, g: lb.Connection) -> None:
    """Load every relationship from the same canonical registry used by deltas."""

    for projection in iter_available_relationship_projections(sql):
        _copy_projection(sql, g, projection, chunk_size=EDGE_ROW_CHUNK_SIZE)


def ensure_schema(conn: lb.Connection) -> None:
    # Base V1 node tables
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Repository("
        "repo_id INT64 PRIMARY KEY, repo_key STRING, tracked_branch STRING, "
        "indexed_commit_sha STRING, last_indexed_at STRING, index_status STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Entity("
        "entity_id INT64 PRIMARY KEY, "
        "name STRING, "
        "entity_type STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS EntityOccurrence("
        "entity_occurrence_id INT64 PRIMARY KEY, "
        "repo_id INT64, entity_id INT64, ent_file STRING, module STRING, "
        "table_name STRING, view_name STRING, dummy INT64, source_file_id INT64, "
        "extractor STRING, confidence DOUBLE, created_at STRING, updated_at STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Symbol("
        "symbol_id INT64 PRIMARY KEY, "
        "name STRING, "
        "kind STRING, "
        "start_line INT64, "
        "end_line INT64, "
        "signature STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS File("
        "file_id INT64 PRIMARY KEY, "
        "repo_id INT64, repo_key STRING, "
        "path STRING, "
        "language STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Workflow("
        "workflow_id INT64 PRIMARY KEY, "
        "name STRING, "
        "workflow_type STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS RestEndpoint("
        "rest_endpoint_id INT64 PRIMARY KEY, "
        "method STRING, "
        "path STRING)"
    )
    # Semantic ontology facts remain authoritative in SQLite.  These nodes are
    # a read-only traversal projection used only after an operator-built graph
    # is promoted alongside the matching catalog snapshot.
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS EntitySchemaComponent("
        "entity_schema_component_id INT64 PRIMARY KEY, occurrence_id INT64, "
        "component_kind STRING, component_path STRING, declared_name STRING, "
        "target_literal STRING, data_type STRING, cardinality STRING, "
        "writeability STRING, confidence DOUBLE)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS EntityRelationshipFact("
        "entity_relationship_fact_id INT64 PRIMARY KEY, source_occurrence_id INT64, "
        "target_occurrence_id INT64, axis STRING, relation_kind STRING, fact_key STRING, "
        "assertion_status STRING, target_entity_name STRING, target_literal STRING, "
        "cardinality STRING, confidence DOUBLE, source_path STRING, start_line INT64, end_line INT64)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS EntityOperationFact("
        "entity_operation_fact_id INT64 PRIMARY KEY, occurrence_id INT64, axis STRING, "
        "operation STRING, surface_kind STRING, availability STRING, "
        "invocation_context STRING, persistence_scope STRING, standalone STRING, confidence DOUBLE)"
    )

    # V2 node tables
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS WorkflowNode("
        "workflow_node_id INT64 PRIMARY KEY, "
        "workflow_id INT64, "
        "entity_id INT64, "
        "node_kind STRING, "
        "node_key STRING, "
        "name STRING, "
        "ordinal INT64, "
        "action STRING, "
        "source_kind STRING, "
        "file_id INT64, "
        "symbol_id INT64, "
        "metadata_json STRING, "
        "created_at STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS OpenApiSpec("
        "openapi_id INT64 PRIMARY KEY, "
        "file_id INT64, "
        "file_path STRING, "
        "module STRING, "
        "slug STRING, "
        "version STRING, "
        "kind STRING, "
        "canonical_name STRING, "
        "resource_path STRING, "
        "x_mapped_to STRING, "
        "title STRING, "
        "state STRING, "
        "last_seen_at STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS SecurityOperation("
        "security_operation_id INT64 PRIMARY KEY, "
        "op_key STRING, "
        "op_numeric_id INT64, "
        "title STRING, "
        "action STRING, "
        "script STRING, "
        "force_mode STRING, "
        "secure_only INT64, "
        "allow_dev_env_only INT64, "
        "source_file STRING, "
        "file_id INT64, "
        "source_line INT64, "
        "source_kind STRING, "
        "raw_hash STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS SecurityPolicy("
        "security_policy_id INT64 PRIMARY KEY, "
        "policy_name STRING, "
        "module STRING, "
        "label STRING, "
        "source_file STRING, "
        "file_id INT64, "
        "source_line INT64)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS SecurityPolicyValue("
        "security_policy_value_id INT64 PRIMARY KEY, "
        "policy_id INT64, "
        "value_key STRING, "
        "display STRING, "
        "value_label STRING, "
        "source_line INT64)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS SecurityMenu("
        "security_menu_id INT64 PRIMARY KEY, "
        "module STRING, "
        "menu_name STRING, "
        "source_file STRING, "
        "file_id INT64)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS SecurityMenuItem("
        "security_menu_item_id INT64 PRIMARY KEY, "
        "menu_id INT64, "
        "item_path STRING, "
        "item_name STRING, "
        "menu_item_id STRING, "
        "menu_script STRING, "
        "menu_key STRING, "
        "source_line INT64)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS DbTable("
        "dbschema_table_id INT64 PRIMARY KEY, "
        "table_name STRING, "
        "primary_keys STRING, "
        "source_file STRING, "
        "file_id INT64, "
        "source_line INT64, "
        "raw_hash STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS DbField("
        "dbschema_field_id INT64 PRIMARY KEY, "
        "dbschema_table_id INT64, "
        "field_name STRING, "
        "field_type STRING, "
        "source_line INT64)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS EntityAccessLink("
        "entity_access_link_id INT64 PRIMARY KEY, "
        "entity_id INT64, "
        "surface STRING, "
        "record_id INT64, "
        "link_type STRING, "
        "evidence_file_id INT64, "
        "evidence_symbol_id INT64, "
        "confidence_mode STRING, "
        "notes STRING, "
        "created_at STRING)"
    )

    # Base V1 relationship tables
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ROOT("
        "FROM Entity TO Symbol, "
        "role STRING, "
        "weight DOUBLE)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_MAPPING("
        "FROM Entity TO Symbol, "
        "mapping_type STRING, "
        "confidence DOUBLE)"
    )
    conn.execute("CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Symbol TO Symbol)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS IMPLEMENTS(FROM Symbol TO Symbol)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM Symbol TO Symbol)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS USES(FROM Symbol TO Symbol)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS REFERENCES(FROM Symbol TO Symbol)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS CALLS(FROM Symbol TO Symbol)")
    conn.execute("CREATE REL TABLE IF NOT EXISTS DECLARED_IN(FROM Symbol TO File)")
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS REPOSITORY_CONTAINS_FILE(FROM Repository TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS REPOSITORY_HAS_ENTITY_OCCURRENCE(FROM Repository TO EntityOccurrence)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_HAS_OCCURRENCE(FROM Entity TO EntityOccurrence)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_FILE(FROM EntityOccurrence TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_ROOT(FROM EntityOccurrence TO Symbol, role STRING, weight DOUBLE)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_MAPPING(FROM EntityOccurrence TO Symbol, mapping_type STRING, confidence DOUBLE)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_WORKFLOW(FROM EntityOccurrence TO Workflow)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_REST_ENDPOINT(FROM EntityOccurrence TO RestEndpoint)"
    )
    # Semantic facts are modeled as nodes so an unresolved target can remain
    # authoritative SQLite evidence without inventing a graph edge.  These
    # relations are only emitted for the deterministic IDs carried by the
    # source tables below.
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_HAS_COMPONENT("
        "FROM EntityOccurrence TO EntitySchemaComponent)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_HAS_SEMANTIC_FACT("
        "FROM EntityOccurrence TO EntityRelationshipFact)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SEMANTIC_FACT_TARGET_OCCURRENCE("
        "FROM EntityRelationshipFact TO EntityOccurrence)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_OCCURRENCE_HAS_OPERATION_FACT("
        "FROM EntityOccurrence TO EntityOperationFact)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_ENTITY_OCCURRENCE(FROM EntityAccessLink TO EntityOccurrence)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS CROSS_REPO_INTEGRATION("
        "FROM File TO File, integration_link_id INT64, relation_type STRING, "
        "confidence DOUBLE, resolution_status STRING)"
    )
    conn.execute("CREATE REL TABLE IF NOT EXISTS HAS_WORKFLOW(FROM Entity TO Workflow)")
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS EXPOSES_ENTITY(FROM RestEndpoint TO Entity)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS HANDLED_BY(FROM RestEndpoint TO Symbol)"
    )

    # V2 relationship tables backed by direct FK or deterministic resolved links
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS WORKFLOW_HAS_NODE("
        "FROM Workflow TO WorkflowNode)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS WORKFLOW_NODE_FILE(FROM WorkflowNode TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS WORKFLOW_NODE_SYMBOL("
        "FROM WorkflowNode TO Symbol)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS WORKFLOW_NODE_EDGE("
        "FROM WorkflowNode TO WorkflowNode, "
        "workflow_id INT64, "
        "edge_kind STRING, "
        "ordinal INT64, "
        "evidence STRING, "
        "confidence DOUBLE, "
        "file_id INT64, "
        "symbol_id INT64)"
    )

    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS OPENAPI_SPEC_FILE(FROM OpenApiSpec TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS OPENAPI_FILE_REF("
        "FROM File TO File, "
        "ref_value STRING, "
        "ref_path STRING, "
        "confidence DOUBLE)"
    )

    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SECURITY_OPERATION_FILE("
        "FROM SecurityOperation TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SECURITY_POLICY_FILE("
        "FROM SecurityPolicy TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SECURITY_POLICY_HAS_VALUE("
        "FROM SecurityPolicy TO SecurityPolicyValue)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SECURITY_MENU_FILE(FROM SecurityMenu TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SECURITY_MENU_HAS_ITEM("
        "FROM SecurityMenu TO SecurityMenuItem)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS SECURITY_MENU_ITEM_TO_OPERATION("
        "FROM SecurityMenuItem TO SecurityOperation, "
        "op_key STRING, "
        "resolution_reason STRING)"
    )

    conn.execute("CREATE REL TABLE IF NOT EXISTS DBTABLE_FILE(FROM DbTable TO File)")
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS DBTABLE_HAS_FIELD(FROM DbTable TO DbField)"
    )

    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_ENTITY("
        "FROM EntityAccessLink TO Entity)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_FILE("
        "FROM EntityAccessLink TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_SYMBOL("
        "FROM EntityAccessLink TO Symbol)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_WORKFLOW("
        "FROM EntityAccessLink TO Workflow)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_REST_ENDPOINT("
        "FROM EntityAccessLink TO RestEndpoint)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_SECURITY_OPERATION("
        "FROM EntityAccessLink TO SecurityOperation)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_SECURITY_RESOURCE("
        "FROM EntityAccessLink TO SecurityOperation)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_SECURITY_POLICY("
        "FROM EntityAccessLink TO SecurityPolicy)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_SECURITY_MENU("
        "FROM EntityAccessLink TO SecurityMenu)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM("
        "FROM EntityAccessLink TO SecurityMenuItem)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ACCESS_LINK_DBTABLE("
        "FROM EntityAccessLink TO DbTable)"
    )

    # Deferred edges now resolved with deterministic unique resolution checks
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS DOCUMENTS_ENTITY(FROM OpenApiSpec TO Entity)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS POLICY_VALUE_GRANTS_OPERATION("
        "FROM SecurityPolicyValue TO SecurityOperation)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ALLOWS_SECURITY_OPERATION("
        "FROM SecurityOperation TO SecurityOperation, "
        "allowed_op_key STRING, "
        "resolution_reason STRING)"
    )


def require_snapshot_integrity(conn: sqlite3.Connection, *, context: str) -> None:
    """Reject graph projection when authoritative SQLite evidence is invalid."""
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        sample = [tuple(row) for row in violations[:5]]
        raise RuntimeError(
            f"invalid SQLite snapshot before {context}: {len(violations)} "
            f"foreign-key violation(s); sample={sample}"
        )

    repo_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(repos)")}
    if "lifecycle_state" not in repo_columns:
        return
    archived_ids = [
        int(row[0])
        for row in conn.execute(
            "SELECT id FROM repos WHERE lifecycle_state='archived' ORDER BY id"
        )
    ]
    if not archived_ids:
        return
    # Direct repo ownership is the universal archive boundary.  The archive
    # candidate service owns the complete static purge registry; this narrow
    # projection-time assertion is a second line of defense that rejects any
    # target evidence left behind before Ladybug can cache it.
    direct_repo_tables = []
    for (table_name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        table = str(table_name)
        if table in {"repos", "catalog_builds", "graph_builds", "schema_migrations"}:
            continue
        columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if "repo_id" not in columns:
            continue
        placeholders = ",".join("?" for _ in archived_ids)
        count = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE repo_id IN ({placeholders})',
            archived_ids,
        ).fetchone()[0]
        if count:
            direct_repo_tables.append(f"{table}={count}")
    if direct_repo_tables:
        raise RuntimeError(
            "archived repository evidence remains before graph projection: "
            + ", ".join(direct_repo_tables)
        )


def build_graph(sqlite_path: str, graph_path: str) -> None:
    sql = db = g = None
    try:
        Path(graph_path).parent.mkdir(parents=True, exist_ok=True)
        sql = sqlite3.connect(sqlite_path)
        require_snapshot_integrity(sql, context="graph candidate build")
        db = lb.Database(graph_path)
        g = lb.Connection(db)
        ensure_schema(g)
        load_node_projections(sql, g)
        load_relationship_projections(sql, g)
        print("Ladybug graph build complete")
    finally:
        if g is not None:
            g.close()
        if db is not None:
            db.close()
        if sql is not None:
            sql.close()
