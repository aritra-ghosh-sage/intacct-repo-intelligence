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
EDGE_STMT_BATCH = 1000
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

    with tqdm(desc=f"COPY {target_table}", unit="row") as pbar:
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            df = pd.DataFrame(rows, schema=columns, orient="row")  # noqa: F841 - required by Ladybug COPY ... FROM df
            pbar.update(len(rows))
            g.execute(f"COPY {target_table} FROM df")


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
    # Create node tables
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Entity(entity_id INT64 PRIMARY KEY, name STRING, entity_type STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Symbol(symbol_id INT64 PRIMARY KEY, name STRING, kind STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS File(file_id INT64 PRIMARY KEY, path STRING, language STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS Workflow(workflow_id INT64 PRIMARY KEY, name STRING, workflow_type STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE IF NOT EXISTS RestEndpoint(rest_endpoint_id INT64 PRIMARY KEY, method STRING, path STRING)"
    )
    # v2 NODE TABLES
    conn.execute(
        "CREATE NODE TABLE WorkflowNode(workflow_node_id INT64 PRIMARY KEY, node_kind STRING, node_key STRING, name STRING, ordinal INT64, action STRING, source_kind STRING, metadata_json STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE OpenApiSpec(openapi_id INT64 PRIMARY KEY, file_path STRING, module STRING, version STRING, kind STRING, canonical_name STRING, resource_path STRING, x_mapped_to STRING, title STRING, state STRING, last_seen_at STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE SecurityOperation(security_operation_id INT64 PRIMARY KEY, op_key STRING, op_numeric_id INT64, title STRING, action STRING, script STRING, source_file STRING, source_kind STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE SecurityPolicy(security_policy_id INT64 PRIMARY KEY, policy_name STRING, module STRING, label STRING, source_file STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE SecurityMenu(security_menu_id INT64 PRIMARY KEY, module STRING, menu_name STRING, source_file STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE SecurityMenuItem(security_menu_item_id INT64 PRIMARY KEY, item_path STRING, item_name STRING, menu_item_id STRING, menu_script STRING, menu_key STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE DbTable(dbschema_table_id INT64 PRIMARY KEY, table_name STRING, primary_keys STRING, source_file STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE DbField(dbschema_field_id INT64 PRIMARY KEY, field_name STRING, field_type STRING)"
    )
    conn.execute(
        "CREATE NODE TABLE AccessRecord(access_record_pk STRING PRIMARY KEY, surface STRING, record_id INT64, link_type STRING, confidence_mode STRING, notes STRING, evidence_file_id INT64, evidence_symbol_id INT64, created_at STRING)"
    )

    # Create relationship tables
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_ROOT(FROM Entity TO Symbol, role STRING, weight DOUBLE)"
    )
    conn.execute(
        "CREATE REL TABLE IF NOT EXISTS ENTITY_MAPPING(FROM Entity TO Symbol, mapping_type STRING, confidence DOUBLE)"
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
    # v2 REL TABLES
    # conn.execute("CREATE REL TABLE IF NOT EXISTS WORKFLOW_NODE(FROM Workflow TO WorkflowNode, ordinal INT64)")
    # conn.execute("CREATE REL TABLE IF NOT EXISTS WORKFLOW_NODE_TRANSITION


def load_nodes(sql: sqlite3.Connection, g: lb.Connection) -> None:
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS entity_id, name, entity_type FROM entity_nodes ORDER BY id",
        "Entity",
    )
    copy_table_from_sql(
        sql,
        g,
        "SELECT id AS symbol_id, name, kind FROM symbols ORDER BY id",
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


def main() -> None:
    sql = db = g = None
    try:
        Path(GRAPH_DB).parent.mkdir(parents=True, exist_ok=True)
        sql = sqlite3.connect(SQLITE_DB)
        db = lb.Database(GRAPH_DB)
        g = lb.Connection(db)

        ensure_schema(g)
        load_nodes(sql, g)
        load_edges(sql, g)

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
