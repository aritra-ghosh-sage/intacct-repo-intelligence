#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
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
            df = pd.DataFrame(all_rows, schema=columns, orient="row", infer_schema_length=None)  # noqa: F841 - required by Ladybug COPY ... FROM df
            g.execute(f"COPY {target_table} FROM df")
            pbar.update(len(all_rows))


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
        "CREATE NODE TABLE IF NOT EXISTS Entity("
        "entity_id INT64 PRIMARY KEY, "
        "name STRING, "
        "entity_type STRING, "
        "module STRING, "
        "table_name STRING, "
        "ent_file STRING, "
        "dummy INT64)"
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
        "CREATE REL TABLE IF NOT EXISTS WORKFLOW_NODE_FILE("
        "FROM WorkflowNode TO File)"
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
        "CREATE REL TABLE IF NOT EXISTS OPENAPI_SPEC_FILE("
        "FROM OpenApiSpec TO File)"
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
        "CREATE REL TABLE IF NOT EXISTS SECURITY_MENU_FILE("
        "FROM SecurityMenu TO File)"
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

    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS DBTABLE_FILE("
        "FROM DbTable TO File)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS DBTABLE_HAS_FIELD("
        "FROM DbTable TO DbField)"
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
        "CREATE REL TABLE IF NOT EXISTS DOCUMENTS_ENTITY("
        "FROM OpenApiSpec TO Entity)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS POLICY_VALUE_GRANTS_OPERATION("
        "FROM SecurityPolicyValue TO SecurityOperation)"
    )

    # Defer these until you add deterministic unique resolution checks:
    # - security_operation_allowops -> SecurityOperation to SecurityOperation

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
        "SELECT id AS entity_id, name, entity_type, module, table_name, ent_file, dummy FROM entity_nodes ORDER BY id",
        "Entity",
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
        "SELECT id AS file_id, path, language FROM files ORDER BY id",
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
        with tqdm(desc=desc, unit="row") as pbar:
            while True:
                rows = cursor.fetchmany(EDGE_ROW_CHUNK_SIZE)
                if not rows:
                    break
                batch_queries = []
                for row in rows:
                    stmt = row_to_query(row)
                    if stmt:
                        batch_queries.append(stmt)
                execute_queries_in_batches(
                    g,
                    batch_queries,
                    batch_size=EDGE_STMT_BATCH,
                    desc=f"{desc} execute",
                )
                pbar.update(len(rows))

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
        "SELECT entity_id, symbol_id, mapping_type, confidence FROM entity_mappings WHERE symbol_id IS NOT NULL ORDER BY id",
        lambda r: (
            "MATCH (e:Entity {entity_id:%d}), (s:Symbol {symbol_id:%d}) "
            "CREATE (e)-[:ENTITY_MAPPING {mapping_type:'%s', confidence:%s}]->(s)"
            % (r[0], r[1], q(r[2] or ""), str(float(r[3] or 0.0)))
        ),
        "Loading ENTITY_MAPPING edges",
    )

    _process_edge_rows(
        "SELECT relationship_type, source_symbol_id, target_symbol_id FROM relationships WHERE source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL",
        lambda r: (
            "MATCH (a:Symbol {symbol_id:%d}), (b:Symbol {symbol_id:%d}) "
            "CREATE (a)-[:%s]->(b)" % (r[1], r[2], REL_TYPE_MAP[r[0]])
            if r[0] in REL_TYPE_MAP
            else None
        ),
        "Loading symbol relationships",
    )

    _process_edge_rows(
        "SELECT s.id, s.file_id FROM symbols s WHERE s.file_id IS NOT NULL",
        lambda r: (
            "MATCH (s:Symbol {symbol_id:%d}), (f:File {file_id:%d}) "
            "CREATE (s)-[:DECLARED_IN]->(f)" % (r[0], r[1])
        ),
        "Loading DECLARED_IN edges",
    )

    _process_edge_rows(
        "SELECT id, entity_id FROM workflows WHERE entity_id IS NOT NULL",
        lambda r: (
            "MATCH (e:Entity {entity_id:%d}), (w:Workflow {workflow_id:%d}) "
            "CREATE (e)-[:HAS_WORKFLOW]->(w)" % (r[1], r[0])
        ),
        "Loading HAS_WORKFLOW edges",
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
            ) % (r[1], r[0]),
            *(
                [
                    (
                        "MATCH (n:WorkflowNode {workflow_node_id:%d}), "
                        "(f:File {file_id:%d}) "
                        "CREATE (n)-[:WORKFLOW_NODE_FILE]->(f)"
                    ) % (r[0], r[2])
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
                    ) % (r[0], r[3])
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
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
            ) % (r[1], r[0])
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
            ) % (r[0], r[1])
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
            ) % (r[1], r[0])
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
            ) % (r[0], r[1], q(r[2] or ""), q(r[3] or ""))
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
            ) % (r[0], r[1])
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
            ) % (r[1], r[0])
        ],
        "Loading dbtable field edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT id, entity_id, evidence_file_id, evidence_symbol_id
        FROM entity_access_links
        ORDER BY id
        """,
        lambda r: [
            (
                "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                "(e:Entity {entity_id:%d}) "
                "CREATE (l)-[:ENTITY_ACCESS_LINK_ENTITY]->(e)"
            ) % (r[0], r[1]),
            *(
                [
                    (
                        "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                        "(f:File {file_id:%d}) "
                        "CREATE (l)-[:ENTITY_ACCESS_LINK_FILE]->(f)"
                    ) % (r[0], r[2])
                ]
                if r[2] is not None
                else []
            ),
            *(
                [
                    (
                        "MATCH (l:EntityAccessLink {entity_access_link_id:%d}), "
                        "(s:Symbol {symbol_id:%d}) "
                        "CREATE (l)-[:ENTITY_ACCESS_LINK_SYMBOL]->(s)"
                    ) % (r[0], r[3])
                ]
                if r[3] is not None
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
        ],
        "Loading entity access rest endpoint edges",
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
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
            ) % (r[0], r[1])
        ],
        "Loading entity access dbtable edges",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT o.id, e.id
        FROM openapispec_index o
        JOIN entity_nodes e ON e.name = o.x_mapped_to
        WHERE o.x_mapped_to IS NOT NULL AND TRIM(o.x_mapped_to) <> ''
        ORDER BY o.id
        """,
        lambda r: [
            (
                "MATCH (o:OpenApiSpec {openapi_id:%d}), "
                "(e:Entity {entity_id:%d}) "
                "CREATE (o)-[:DOCUMENTS_ENTITY]->(e)"
            ) % (r[0], r[1])
        ],
        "Loading DOCUMENTS_ENTITY edges (openapi to entity via x_mapped_to)",
    )

    process_edge_rows_many(
        sql,
        g,
        """
        SELECT DISTINCT spv.id, so.id, spv.policy_id
        FROM security_policy_values spv
        JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
        JOIN security_operations so ON so.op_key = spe.op_key
        WHERE spe.op_key IN (
            SELECT op_key FROM security_operations GROUP BY op_key HAVING COUNT(*) = 1
        )
        ORDER BY spv.id, so.id
        """,
        lambda r: [
            (
                "MATCH (pv:SecurityPolicyValue {security_policy_value_id:%d}), "
                "(o:SecurityOperation {security_operation_id:%d}) "
                "CREATE (pv)-[:POLICY_VALUE_GRANTS_OPERATION]->(o)"
            ) % (r[0], r[1])
        ],
        "Loading POLICY_VALUE_GRANTS_OPERATION edges (with dedup guard for unique op_keys)",
    )

def main() -> None:
    sql = db = g = None
    try:
        Path(GRAPH_DB).parent.mkdir(parents=True, exist_ok=True)
        sql = sqlite3.connect(SQLITE_DB)
        db = lb.Database(GRAPH_DB)
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


if __name__ == "__main__":
    main()
