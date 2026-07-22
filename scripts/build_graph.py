#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from config import CATALOG_DB as SQLITE_DB, GRAPH_DB
import ladybug as lb
import polars as pd
from tqdm import tqdm

REL_TYPE_MAP = {
    "INHERITS": "INHERITS",
    "IMPLEMENTS": "IMPLEMENTS",
    "IMPORTS": "IMPORTS",
    "USES": "USES",
    "REFERENCES": "REFERENCES",
    "CALLS": "CALLS",
    "STATIC_CALLS": "CALLS",
}

NODE_CHUNK_SIZE = 50000
EDGE_STMT_BATCH = 3000
EDGE_ROW_CHUNK_SIZE = 50000


def copy_table_from_sql(
    sql: sqlite3.Connection,
    g: lb.Connection,
    select_sql: str,
    target_table: str,
    chunk_size: int = NODE_CHUNK_SIZE,
) -> None:
    cursor = sql.execute(select_sql)
    columns = [col[0] for col in cursor.description]

    # Collect all rows first, then create one DataFrame
    all_rows = []
    with tqdm(desc=f"Fetching {target_table}", unit="row") as pbar:
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            all_rows.extend(rows)
            pbar.update(len(rows))

    # Create DataFrame once with all data
    with tqdm(desc=f"COPY {target_table}", unit="row") as pbar:
        if all_rows:
            df = pd.DataFrame(
                all_rows, schema=columns, orient="row", infer_schema_length=None
            )  # noqa: F841 - required by Ladybug COPY ... FROM df
            _ = df
            g.execute(f"COPY {target_table} FROM df")
            pbar.update(len(all_rows))


def copy_rel_table_from_sql(
    sql: sqlite3.Connection,
    g: lb.Connection,
    select_sql: str,
    rel_table: str,
    chunk_size: int = EDGE_ROW_CHUNK_SIZE,
) -> None:
    cursor = sql.execute(select_sql)
    columns = [col[0] for col in cursor.description]

    with tqdm(desc=f"COPY {rel_table}", unit="row") as pbar:
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break

            df = pd.DataFrame(
                rows,
                schema=columns,
                orient="row",
                infer_schema_length=None,
            )  # noqa: F841 - required by Ladybug COPY ... FROM df
            _ = df
            g.execute(f"COPY {rel_table} FROM df")
            pbar.update(len(rows))


def execute_queries_in_batches(
    g: lb.Connection,
    queries: list[str],
    batch_size: int = EDGE_STMT_BATCH,
    desc: str = "Executing batch",
) -> None:
    if not queries:
        return
    for i in tqdm(range(0, len(queries), batch_size), desc=desc):
        chunk = queries[i : i + batch_size]
        g.execute(";\n".join(chunk) + ";")


def q(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


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


def process_edge_rows_many(
    sql: sqlite3.Connection,
    g: lb.Connection,
    select_sql: str,
    row_to_statements,
    desc: str,
) -> None:
    cursor = sql.execute(select_sql)
    with tqdm(desc=desc, unit="row") as pbar:
        while True:
            rows = cursor.fetchmany(EDGE_ROW_CHUNK_SIZE)
            if not rows:
                break

            batch_queries: list[str] = []
            for row in rows:
                statements = row_to_statements(row)
                if statements:
                    batch_queries.extend(statements)

            execute_queries_in_batches(
                g,
                batch_queries,
                batch_size=EDGE_STMT_BATCH,
                desc=f"{desc} execute",
            )
            pbar.update(len(rows))


def load_v2_nodes(sql: sqlite3.Connection, g: lb.Connection) -> None:
    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS workflow_node_id,
            workflow_id,
            entity_id,
            node_kind,
            node_key,
            name,
            ordinal,
            action,
            source_kind,
            file_id,
            symbol_id,
            metadata_json,
            created_at
        FROM workflow_nodes
        ORDER BY id
        """,
        "WorkflowNode",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS openapi_id,
            file_id,
            file_path,
            module,
            slug,
            version,
            kind,
            canonical_name,
            resource_path,
            x_mapped_to,
            title,
            state,
            last_seen_at
        FROM openapispec_index
        ORDER BY id
        """,
        "OpenApiSpec",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS security_operation_id,
            op_key,
            op_numeric_id,
            title,
            action,
            script,
            force_mode,
            secure_only,
            allow_dev_env_only,
            source_file,
            file_id,
            source_line,
            source_kind,
            raw_hash
        FROM security_operations
        ORDER BY id
        """,
        "SecurityOperation",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS security_policy_id,
            policy_name,
            module,
            label,
            source_file,
            file_id,
            source_line
        FROM security_policies
        ORDER BY id
        """,
        "SecurityPolicy",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS security_policy_value_id,
            policy_id,
            value_key,
            display,
            value_label,
            source_line
        FROM security_policy_values
        ORDER BY id
        """,
        "SecurityPolicyValue",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS security_menu_id,
            module,
            menu_name,
            source_file,
            file_id
        FROM security_menus
        ORDER BY id
        """,
        "SecurityMenu",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS security_menu_item_id,
            menu_id,
            item_path,
            item_name,
            menu_item_id,
            menu_script,
            menu_key,
            source_line
        FROM security_menu_items
        ORDER BY id
        """,
        "SecurityMenuItem",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS dbschema_table_id,
            table_name,
            primary_keys,
            source_file,
            file_id,
            source_line,
            raw_hash
        FROM dbschema_tables
        ORDER BY id
        """,
        "DbTable",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS dbschema_field_id,
            dbschema_table_id,
            field_name,
            field_type,
            source_line
        FROM dbschema_fields
        ORDER BY id
        """,
        "DbField",
    )

    copy_table_from_sql(
        sql,
        g,
        """
        SELECT
            id AS entity_access_link_id,
            entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            evidence_symbol_id,
            confidence_mode,
            notes,
            created_at
        FROM entity_access_links
        ORDER BY id
        """,
        "EntityAccessLink",
    )


def load_nodes(sql: sqlite3.Connection, g: lb.Connection) -> None:
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS repo_id,repo_key,tracked_branch,indexed_commit_sha,"
        "COALESCE(last_built_at,last_scanned_at) AS last_indexed_at,index_status FROM repos ORDER BY id",
        "Repository",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS entity_id, name, entity_type FROM entity_nodes ORDER BY id",
        "Entity",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS entity_occurrence_id,repo_id,entity_id,ent_file,module,table_name,view_name,"
        "dummy,source_file_id,extractor,confidence,created_at,updated_at "
        "FROM entity_occurrences ORDER BY id",
        "EntityOccurrence",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS symbol_id, name, kind, start_line, end_line, signature FROM symbols ORDER BY id",
        "Symbol",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT f.id AS file_id,f.repo_id,r.repo_key,f.path,f.language "
        "FROM files f JOIN repos r ON r.id=f.repo_id ORDER BY f.id",
        "File",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS workflow_id, name, workflow_type FROM workflows ORDER BY id",
        "Workflow",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS rest_endpoint_id, method, path FROM rest_endpoints ORDER BY id",
        "RestEndpoint",
    )


def load_edges(sql: sqlite3.Connection, g: lb.Connection) -> None:
    def _process_edge_rows(
        select_sql: str,
        row_to_query,
        desc: str,
    ) -> None:
        cursor = sql.execute(select_sql)
        pending_queries: list[str] = []

        with tqdm(desc=desc, unit="row") as pbar:
            while True:
                rows = cursor.fetchmany(EDGE_ROW_CHUNK_SIZE)
                if not rows:
                    break

                for row in rows:
                    stmt = row_to_query(row)
                    if not stmt:
                        continue

                    pending_queries.append(stmt)

                    # Flush as soon as we hit the statement batch size.
                    if len(pending_queries) >= EDGE_STMT_BATCH:
                        g.execute(";\n".join(pending_queries) + ";")
                        pending_queries.clear()

                pbar.update(len(rows))

            # Flush tail statements.
            if pending_queries:
                g.execute(";\n".join(pending_queries) + ";")

    _process_edge_rows(
        "SELECT entity_id, symbol_id, role, weight FROM entity_roots ORDER BY id",
        lambda r: (
            "MATCH (e:Entity {entity_id:%d}), (s:Symbol {symbol_id:%d}) "
            "CREATE (e)-[:ENTITY_ROOT {role:'%s', weight:%s}]->(s)"
            % (r[0], r[1], q(r[2] or ""), str(float(r[3] or 0.0)))
        ),
        "Loading ENTITY_ROOT edges",
    )

    _process_edge_rows(
        """
        SELECT eo.id, er.symbol_id, er.role, er.weight
        FROM entity_roots er
        JOIN entity_occurrences eo ON eo.repo_id=er.repo_id AND eo.entity_id=er.entity_id
        ORDER BY er.id
        """,
        lambda r: (
            "MATCH (e:EntityOccurrence {entity_occurrence_id:%d}), (s:Symbol {symbol_id:%d}) "
            "CREATE (e)-[:ENTITY_OCCURRENCE_ROOT {role:'%s', weight:%s}]->(s)"
            % (r[0], r[1], q(r[2] or ""), str(float(r[3] or 0.0)))
        ),
        "Loading ENTITY_OCCURRENCE_ROOT edges",
    )

    _process_edge_rows(
        "SELECT entity_id, symbol_id, mapping_type, confidence FROM entity_mappings WHERE symbol_id IS NOT NULL ORDER BY id",
        lambda r: (
            "MATCH (e:Entity {entity_id:%d}), (s:Symbol {symbol_id:%d}) "
            "CREATE (e)-[:ENTITY_MAPPING {mapping_type:'%s', confidence:%s}]->(s)"
            % (r[0], r[1], q(r[2] or ""), str(float(r[3] or 0.0)))
        ),
        "Loading ENTITY_MAPPING edges",
    )

    _process_edge_rows(
        """
        SELECT eo.id, em.symbol_id, em.mapping_type, em.confidence
        FROM entity_mappings em
        JOIN entity_occurrences eo ON eo.repo_id=em.repo_id AND eo.entity_id=em.entity_id
        WHERE em.symbol_id IS NOT NULL
        ORDER BY em.id
        """,
        lambda r: (
            "MATCH (e:EntityOccurrence {entity_occurrence_id:%d}), (s:Symbol {symbol_id:%d}) "
            "CREATE (e)-[:ENTITY_OCCURRENCE_MAPPING {mapping_type:'%s', confidence:%s}]->(s)"
            % (r[0], r[1], q(r[2] or ""), str(float(r[3] or 0.0)))
        ),
        "Loading ENTITY_OCCURRENCE_MAPPING edges",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            source_symbol_id AS \"FROM\",
            target_symbol_id AS \"TO\"
        FROM relationships
        WHERE relationship_type = 'INHERITS'
          AND source_symbol_id IS NOT NULL
          AND target_symbol_id IS NOT NULL
        ORDER BY id
        """,
        "INHERITS",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            source_symbol_id AS \"FROM\",
            target_symbol_id AS \"TO\"
        FROM relationships
        WHERE relationship_type = 'IMPLEMENTS'
          AND source_symbol_id IS NOT NULL
          AND target_symbol_id IS NOT NULL
        ORDER BY id
        """,
        "IMPLEMENTS",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            source_symbol_id AS \"FROM\",
            target_symbol_id AS \"TO\"
        FROM relationships
        WHERE relationship_type = 'IMPORTS'
          AND source_symbol_id IS NOT NULL
          AND target_symbol_id IS NOT NULL
        ORDER BY id
        """,
        "IMPORTS",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            source_symbol_id AS \"FROM\",
            target_symbol_id AS \"TO\"
        FROM relationships
        WHERE relationship_type = 'USES'
          AND source_symbol_id IS NOT NULL
          AND target_symbol_id IS NOT NULL
        ORDER BY id
        """,
        "USES",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            source_symbol_id AS \"FROM\",
            target_symbol_id AS \"TO\"
        FROM relationships
        WHERE relationship_type = 'REFERENCES'
          AND source_symbol_id IS NOT NULL
          AND target_symbol_id IS NOT NULL
        ORDER BY id
        """,
        "REFERENCES",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            source_symbol_id AS \"FROM\",
            target_symbol_id AS \"TO\"
        FROM relationships
        WHERE relationship_type IN ('CALLS', 'STATIC_CALLS')
          AND source_symbol_id IS NOT NULL
          AND target_symbol_id IS NOT NULL
        ORDER BY id
        """,
        "CALLS",
    )

    copy_rel_table_from_sql(
        sql,
        g,
        """
        SELECT
            s.id AS \"FROM\",
            s.file_id AS \"TO\"
        FROM symbols s
        ORDER BY s.id
        """,
        "DECLARED_IN",
    )
    copy_rel_table_from_sql(
        sql,
        g,
        'SELECT repo_id AS "FROM",id AS "TO" FROM files ORDER BY repo_id,id',
        "REPOSITORY_CONTAINS_FILE",
    )
    copy_rel_table_from_sql(
        sql,
        g,
        'SELECT repo_id AS "FROM",id AS "TO" FROM entity_occurrences ORDER BY repo_id,id',
        "REPOSITORY_HAS_ENTITY_OCCURRENCE",
    )
    copy_rel_table_from_sql(
        sql,
        g,
        'SELECT entity_id AS "FROM",id AS "TO" FROM entity_occurrences ORDER BY entity_id,id',
        "ENTITY_HAS_OCCURRENCE",
    )
    copy_rel_table_from_sql(
        sql,
        g,
        'SELECT id AS "FROM",source_file_id AS "TO" FROM entity_occurrences WHERE source_file_id IS NOT NULL ORDER BY id',
        "ENTITY_OCCURRENCE_FILE",
    )
    if sql.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='integration_links'"
    ).fetchone():
        columns = {
            row[1] for row in sql.execute("PRAGMA table_info(integration_links)")
        }
        if {"id", "source_file_id", "target_file_id"} <= columns:
            relation_col = (
                "relation_type" if "relation_type" in columns else "'integration'"
            )
            confidence_col = "confidence" if "confidence" in columns else "0.0"
            status_col = (
                "resolution_status" if "resolution_status" in columns else "'resolved'"
            )
            status_filter = (
                " AND resolution_status IN ('resolved','validated')"
                if "resolution_status" in columns
                else ""
            )
            copy_rel_table_from_sql(
                sql,
                g,
                f"""SELECT source_file_id AS "FROM",target_file_id AS "TO",id AS integration_link_id,
                           {relation_col} AS relation_type,COALESCE({confidence_col},0.0) AS confidence,
                           COALESCE({status_col},'resolved') AS resolution_status
                    FROM integration_links WHERE source_file_id IS NOT NULL AND target_file_id IS NOT NULL
                    AND source_repo_id <> target_repo_id
                    {status_filter} ORDER BY id""",
                "CROSS_REPO_INTEGRATION",
            )

    _process_edge_rows(
        "SELECT id, entity_id FROM workflows WHERE entity_id IS NOT NULL",
        lambda r: (
            "MATCH (e:Entity {entity_id:%d}), (w:Workflow {workflow_id:%d}) "
            "CREATE (e)-[:HAS_WORKFLOW]->(w)" % (r[1], r[0])
        ),
        "Loading HAS_WORKFLOW edges",
    )
    _process_edge_rows(
        """
        SELECT eo.id, w.id
        FROM workflows w
        JOIN entity_occurrences eo ON eo.repo_id=w.repo_id AND eo.entity_id=w.entity_id
        WHERE w.entity_id IS NOT NULL
        ORDER BY w.id
        """,
        lambda r: (
            "MATCH (e:EntityOccurrence {entity_occurrence_id:%d}), (w:Workflow {workflow_id:%d}) "
            "CREATE (e)-[:ENTITY_OCCURRENCE_WORKFLOW]->(w)" % (r[0], r[1])
        ),
        "Loading ENTITY_OCCURRENCE_WORKFLOW edges",
    )

    # _process_edge_rows(
    #     "SELECT id, entity_id, handler_symbol_id FROM rest_endpoints",
    #     lambda r: "\n".join(
    #         stmt
    #         for stmt in [
    #             (
    #                 "MATCH (re:RestEndpoint {rest_endpoint_id:%d}), (e:Entity {entity_id:%d}) "
    #                 "CREATE (re)-[:EXPOSES_ENTITY]->(e)" % (r[0], r[1])
    #                 if r[1] is not None
    #                 else None
    #             ),
    #             (
    #                 "MATCH (re:RestEndpoint {rest_endpoint_id:%d}), (s:Symbol {symbol_id:%d}) "
    #                 "CREATE (re)-[:HANDLED_BY]->(s)" % (r[0], r[2])
    #                 if r[2] is not None
    #                 else None
    #             ),
    #         ]
    #         if stmt
    #     )
    #     or None,
    #     "Loading REST endpoint edges",
    # )
    _process_edge_rows(
        "SELECT id, entity_id FROM rest_endpoints WHERE entity_id IS NOT NULL",
        lambda r: (
            "MATCH (re:RestEndpoint {rest_endpoint_id:%d}), (e:Entity {entity_id:%d}) "
            "CREATE (re)-[:EXPOSES_ENTITY]->(e)" % (r[0], r[1])
        ),
        "Loading EXPOSES_ENTITY edges",
    )
    _process_edge_rows(
        """
        SELECT eo.id, ep.id
        FROM rest_endpoints ep
        JOIN entity_occurrences eo ON eo.repo_id=ep.repo_id AND eo.entity_id=ep.entity_id
        WHERE ep.entity_id IS NOT NULL
        ORDER BY ep.id
        """,
        lambda r: (
            "MATCH (e:EntityOccurrence {entity_occurrence_id:%d}), (re:RestEndpoint {rest_endpoint_id:%d}) "
            "CREATE (e)-[:ENTITY_OCCURRENCE_REST_ENDPOINT]->(re)" % (r[0], r[1])
        ),
        "Loading ENTITY_OCCURRENCE_REST_ENDPOINT edges",
    )

    _process_edge_rows(
        "SELECT id, handler_symbol_id FROM rest_endpoints WHERE handler_symbol_id IS NOT NULL",
        lambda r: (
            "MATCH (re:RestEndpoint {rest_endpoint_id:%d}), (s:Symbol {symbol_id:%d}) "
            "CREATE (re)-[:HANDLED_BY]->(s)" % (r[0], r[1])
        ),
        "Loading HANDLED_BY edges",
    )


def load_v2_edges(sql: sqlite3.Connection, g: lb.Connection) -> None:
    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, workflow_id, file_id, symbol_id
        FROM workflow_nodes
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (w:Workflow {workflow_id:%d}), "
                "(n:WorkflowNode {workflow_node_id:%d}) "
                "CREATE (w)-[:WORKFLOW_HAS_NODE]->(n)"
            )
            % (r[1], r[0]),
            *(
                [
                    (
                        "MATCH (n:WorkflowNode {workflow_node_id:%d}), "
                        "(f:File {file_id:%d}) "
                        "CREATE (n)-[:WORKFLOW_NODE_FILE]->(f)"
                    )
                    % (r[0], r[2])
                ]
                if r[2] is not None
                else []
            ),
            *(
                [
                    (
                        "MATCH (n:WorkflowNode {workflow_node_id:%d}), "
                        "(s:Symbol {symbol_id:%d}) "
                        "CREATE (n)-[:WORKFLOW_NODE_SYMBOL]->(s)"
                    )
                    % (r[0], r[3])
                ]
                if r[3] is not None
                else []
            ),
        ],
        "Loading workflow node edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence, confidence, file_id, symbol_id
        FROM workflow_edges
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (a:WorkflowNode {workflow_node_id:%d}), "
                "(b:WorkflowNode {workflow_node_id:%d}) "
                "CREATE (a)-[:WORKFLOW_NODE_EDGE {workflow_id:%d, edge_kind:'%s', ordinal:%d, evidence:'%s', confidence:%s, file_id:%s, symbol_id:%s}]->(b)"
            )
            % (
                r[1],
                r[2],
                r[0],
                q(r[3] or ""),
                int(r[4] or -1),
                q(r[5] or ""),
                str(float(r[6] or 0.0)),
                "NULL" if r[7] is None else str(int(r[7])),
                "NULL" if r[8] is None else str(int(r[8])),
            )
        ],
        "Loading workflow graph edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, file_id
        FROM openapispec_index
        WHERE file_id IS NOT NULL
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (o:OpenApiSpec {openapi_id:%d}), "
                "(f:File {file_id:%d}) "
                "CREATE (o)-[:OPENAPI_SPEC_FILE]->(f)"
            )
            % (r[0], r[1])
        ],
        "Loading openapi spec file edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT source_file_id, target_file_id, ref_value, ref_path, confidence
        FROM openapi_file_ref_edges
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (a:File {file_id:%d}), "
                "(b:File {file_id:%d}) "
                "CREATE (a)-[:OPENAPI_FILE_REF {ref_value:'%s', ref_path:'%s', confidence:%s}]->(b)"
            )
            % (
                r[0],
                r[1],
                q(r[2] or ""),
                q(r[3] or ""),
                str(float(r[4] or 0.0)),
            )
        ],
        "Loading openapi file ref edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, file_id
        FROM security_operations
        WHERE file_id IS NOT NULL
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (o:SecurityOperation {security_operation_id:%d}), "
                "(f:File {file_id:%d}) "
                "CREATE (o)-[:SECURITY_OPERATION_FILE]->(f)"
            )
            % (r[0], r[1])
        ],
        "Loading security operation file edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, file_id
        FROM security_policies
        WHERE file_id IS NOT NULL
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (p:SecurityPolicy {security_policy_id:%d}), "
                "(f:File {file_id:%d}) "
                "CREATE (p)-[:SECURITY_POLICY_FILE]->(f)"
            )
            % (r[0], r[1])
        ],
        "Loading security policy file edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, policy_id
        FROM security_policy_values
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (p:SecurityPolicy {security_policy_id:%d}), "
                "(v:SecurityPolicyValue {security_policy_value_id:%d}) "
                "CREATE (p)-[:SECURITY_POLICY_HAS_VALUE]->(v)"
            )
            % (r[1], r[0])
        ],
        "Loading security policy value edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, file_id
        FROM security_menus
        WHERE file_id IS NOT NULL
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (m:SecurityMenu {security_menu_id:%d}), "
                "(f:File {file_id:%d}) "
                "CREATE (m)-[:SECURITY_MENU_FILE]->(f)"
            )
            % (r[0], r[1])
        ],
        "Loading security menu file edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, menu_id
        FROM security_menu_items
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (m:SecurityMenu {security_menu_id:%d}), "
                "(i:SecurityMenuItem {security_menu_item_id:%d}) "
                "CREATE (m)-[:SECURITY_MENU_HAS_ITEM]->(i)"
            )
            % (r[1], r[0])
        ],
        "Loading security menu item edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT menu_item_id, operation_id, op_key, resolution_reason
        FROM security_menu_op_links
        WHERE operation_id IS NOT NULL
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (i:SecurityMenuItem {security_menu_item_id:%d}), "
                "(o:SecurityOperation {security_operation_id:%d}) "
                "CREATE (i)-[:SECURITY_MENU_ITEM_TO_OPERATION {op_key:'%s', resolution_reason:'%s'}]->(o)"
            )
            % (r[0], r[1], q(r[2] or ""), q(r[3] or ""))
        ],
        "Loading security menu operation edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, file_id
        FROM dbschema_tables
        WHERE file_id IS NOT NULL
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (t:DbTable {dbschema_table_id:%d}), "
                "(f:File {file_id:%d}) "
                "CREATE (t)-[:DBTABLE_FILE]->(f)"
            )
            % (r[0], r[1])
        ],
        "Loading dbtable file edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, dbschema_table_id
        FROM dbschema_fields
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (t:DbTable {dbschema_table_id:%d}), "
                "(f:DbField {dbschema_field_id:%d}) "
                "CREATE (t)-[:DBTABLE_HAS_FIELD]->(f)"
            )
            % (r[1], r[0])
        ],
        "Loading dbtable field edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT eal.id, eal.repo_id, eal.entity_id, eal.evidence_file_id,
               eal.evidence_symbol_id, eo.id AS occurrence_id
        FROM entity_access_links eal
        LEFT JOIN entity_occurrences eo
          ON eo.repo_id=eal.repo_id AND eo.entity_id=eal.entity_id
        ORDER BY eal.id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(e:Entity {entity_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_ENTITY]->(e)"
            )
            % (r[0], r[2]),
            *(
                [
                    (
                        "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                        "(e:EntityOccurrence {entity_occurrence_id:%d}) "
                        "CREATE (l)-[:ENTITY_ACCESS_LINK_ENTITY_OCCURRENCE]->(e)"
                    )
                    % (r[0], r[5])
                ]
                if r[5] is not None
                else []
            ),
            *(
                [
                    (
                        "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                        "(f:File {file_id:%d}) "
                        "CREATE (l)-[:ENTITY_ACCESS_LINK_FILE]->(f)"
                    )
                    % (r[0], r[3])
                ]
                if r[3] is not None
                else []
            ),
            *(
                [
                    (
                        "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                        "(s:Symbol {symbol_id:%d}) "
                        "CREATE (l)-[:ENTITY_ACCESS_LINK_SYMBOL]->(s)"
                    )
                    % (r[0], r[4])
                ]
                if r[4] is not None
                else []
            ),
        ],
        "Loading entity access evidence edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'workflow'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(w:Workflow {workflow_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_WORKFLOW]->(w)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access workflow edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'rest_endpoint'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(r:RestEndpoint {rest_endpoint_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_REST_ENDPOINT]->(r)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access rest endpoint edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'security_resource'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(o:SecurityOperation {security_operation_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_SECURITY_RESOURCE]->(o)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access security resource edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'security_operation'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(o:SecurityOperation {security_operation_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->(o)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access security operation edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'security_policy'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(p:SecurityPolicy {security_policy_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_SECURITY_POLICY]->(p)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access security policy edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'security_menu'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(m:SecurityMenu {security_menu_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_SECURITY_MENU]->(m)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access security menu edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'security_menu_item'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(i:SecurityMenuItem {security_menu_item_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM]->(i)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access security menu item edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, record_id
        FROM entity_access_links
        WHERE surface = 'dbschema_table'
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(t:DbTable {dbschema_table_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_DBTABLE]->(t)"
            )
            % (r[0], r[1])
        ],
        "Loading entity access dbtable edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT DISTINCT o.id, em.entity_id
        FROM openapispec_index o
        JOIN entity_mappings em
          ON em.file_id = o.file_id
         AND em.entity_id IS NOT NULL
         AND em.mapping_type LIKE 'openapispec_%'
        WHERE o.x_mapped_to IS NOT NULL
          AND TRIM(o.x_mapped_to) <> ''
        ORDER BY o.id, em.entity_id
        """,
        lambda r: [
            (
                "MATCH (o:OpenApiSpec {openapi_id:%d}), "
                "(e:Entity {entity_id:%d}) "
                "CREATE (o)-[:DOCUMENTS_ENTITY]->(e)"
            )
            % (r[0], r[1])
        ],
        "Loading DOCUMENTS_ENTITY edges (openapi to entity via x_mapped_to)",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT operation_id, allowed_operation_id, allowed_op_key, resolution_reason
        FROM security_operation_allowops
        WHERE allowed_operation_id IS NOT NULL
        ORDER BY operation_id, allowed_operation_id, id
        """,
        lambda r: [
            (
                "MATCH (a:SecurityOperation {security_operation_id:%d}), "
                "(b:SecurityOperation {security_operation_id:%d}) "
                "CREATE (a)-[:ALLOWS_SECURITY_OPERATION {allowed_op_key:'%s', resolution_reason:'%s'}]->(b)"
            )
            % (r[0], r[1], q(r[2] or ""), q(r[3] or ""))
        ],
        "Loading ALLOWS_SECURITY_OPERATION edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT DISTINCT spv.id, so.id, spv.policy_id
        FROM security_policy_values spv
        JOIN security_policies sp ON sp.id = spv.policy_id
        JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
        JOIN security_operations so ON so.repo_id = sp.repo_id AND so.op_key = spe.op_key
        WHERE (sp.repo_id, spe.op_key) IN (
            SELECT repo_id, op_key FROM security_operations GROUP BY repo_id, op_key HAVING COUNT(*) = 1
        )
        ORDER BY spv.id, so.id
        """,
        lambda r: [
            (
                "MATCH (pv:SecurityPolicyValue {security_policy_value_id:%d}), "
                "(o:SecurityOperation {security_operation_id:%d}) "
                "CREATE (pv)-[:POLICY_VALUE_GRANTS_OPERATION]->(o)"
            )
            % (r[0], r[1])
        ],
        "Loading POLICY_VALUE_GRANTS_OPERATION edges (with per-repository op_key guard)",
    )


def build_graph(sqlite_path: str, graph_path: str) -> None:
    sql = db = g = None
    try:
        Path(graph_path).parent.mkdir(parents=True, exist_ok=True)
        sql = sqlite3.connect(sqlite_path)
        db = lb.Database(graph_path)
        g = lb.Connection(db)
        ensure_schema(g)
        load_nodes(sql, g)
        load_v2_nodes(sql, g)
        load_edges(sql, g)
        load_v2_edges(sql, g)
        print("Ladybug graph build complete")
    finally:
        if g is not None:
            g.close()
        if db is not None:
            db.close()
        if sql is not None:
            sql.close()


def create_sqlite_snapshot(source_path: str, snapshot_path: Path) -> None:
    """Create one transactionally consistent SQLite backup for build and validation."""
    source = sqlite3.connect(f"file:{Path(source_path).resolve()}?mode=ro", uri=True)
    destination = sqlite3.connect(snapshot_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def source_fingerprint(snapshot_path: Path) -> str:
    digest = hashlib.sha256()
    with snapshot_path.open("rb") as snapshot:
        for chunk in iter(lambda: snapshot.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_graph_builds_migration(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_builds'"
    ).fetchone()
    if not exists:
        raise RuntimeError(
            "graph_builds is missing; apply migrations/017_graph_builds.sql first"
        )


def preserve_previous_graph(active: Path, previous: Path, build_token: str) -> None:
    """Prepare the rollback copy without moving or removing the active path."""
    if not active.exists():
        return
    temporary = previous.with_name(f"{previous.name}.tmp.{build_token}")
    temporary.unlink(missing_ok=True)
    try:
        try:
            os.link(active, temporary)
        except OSError:
            shutil.copy2(active, temporary)
        os.replace(temporary, previous)
    finally:
        temporary.unlink(missing_ok=True)


def promote_validated_graph(
    sqlite_path: str = SQLITE_DB, graph_path: str = GRAPH_DB
) -> None:
    active = Path(graph_path)
    active.parent.mkdir(parents=True, exist_ok=True)
    previous = active.with_name(active.name + ".previous")
    lock_path = active.with_name(active.name + ".build.lock")
    build_token = uuid.uuid4().hex
    candidate = active.with_name(f"{active.name}.candidate.{build_token}")
    snapshot = active.with_name(f"{active.name}.snapshot.{build_token}.db")
    build_id = None
    promoted = False

    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another graph build holds {lock_path}") from exc

        metadata = sqlite3.connect(sqlite_path)
        try:
            require_graph_builds_migration(metadata)
            create_sqlite_snapshot(sqlite_path, snapshot)
            # Validation must use the immutable snapshot, while the active
            # graph metadata must identify the catalog file that clients will
            # query after promotion.  A SQLite backup may differ byte-for-byte
            # from its source even when it contains the same facts.
            snapshot_fingerprint = source_fingerprint(snapshot)
            fingerprint = source_fingerprint(Path(sqlite_path))
            cur = metadata.execute(
                """
                INSERT INTO graph_builds(
                    graph_path, source_db, status, source_fingerprint
                ) VALUES (?, ?, 'building', ?)
                """,
                (str(active), str(Path(sqlite_path).resolve()), fingerprint),
            )
            build_id = int(cur.lastrowid)
            metadata.commit()

            build_graph(str(snapshot), str(candidate))
            from validation.validate_graph import validate_paths

            validation_summary = validate_paths(
                str(snapshot),
                str(candidate),
                expected_fingerprint=snapshot_fingerprint,
            )
            metadata.execute(
                """
                UPDATE graph_builds
                SET status='validated', validation_summary=?
                WHERE id=?
                """,
                (validation_summary, build_id),
            )
            metadata.commit()

            preserve_previous_graph(active, previous, build_token)
            os.replace(candidate, active)
            promoted = True

            try:
                metadata.execute(
                    """
                    UPDATE graph_builds
                    SET status='previous'
                    WHERE graph_path=? AND id<>? AND status='active'
                    """,
                    (str(active), build_id),
                )
                metadata.execute(
                    """
                    UPDATE graph_builds
                    SET status='active', completed_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (build_id,),
                )
                metadata.commit()
            except sqlite3.Error as exc:
                print(
                    f"Warning: graph promoted but metadata update failed for "
                    f"build {build_id}: {exc}"
                )
            print(f"Promoted validated graph to {active}")
        except Exception as exc:
            if build_id is not None and not promoted:
                try:
                    metadata.execute(
                        """
                        UPDATE graph_builds
                        SET status='failed', completed_at=CURRENT_TIMESTAMP, error=?
                        WHERE id=?
                        """,
                        (str(exc), build_id),
                    )
                    metadata.commit()
                except sqlite3.Error:
                    pass
            raise
        finally:
            metadata.close()
            candidate.unlink(missing_ok=True)
            snapshot.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and atomically promote the Ladybug graph."
    )
    parser.add_argument("--db", default=SQLITE_DB, help="SQLite catalog path")
    parser.add_argument("--graph", default=GRAPH_DB, help="Active Ladybug graph path")
    args = parser.parse_args()
    promote_validated_graph(args.db, args.graph)


if __name__ == "__main__":
    main()
