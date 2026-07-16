#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys

import ladybug as lb

from config import CATALOG_DB as SQLITE_DB, GRAPH_DB


def scalar_sql(conn: sqlite3.Connection, query: str) -> int:
    return int(conn.execute(query).fetchone()[0])


def scalar_g(conn: lb.Connection, query: str) -> int:
    res = conn.execute(query)
    return int(res.get_all()[0][0])


def assert_eq(name: str, sqlite_count: int, graph_count: int) -> None:
    if sqlite_count != graph_count:
        raise RuntimeError(f"{name} mismatch sqlite={sqlite_count} graph={graph_count}")


def assert_zero(name: str, count: int) -> None:
    if count != 0:
        raise RuntimeError(f"{name} expected 0 but found {count}")


def assert_positive(name: str, count: int) -> None:
    if count <= 0:
        raise RuntimeError(f"{name} expected > 0 but found {count}")


def validate_counts(
    sqlite_conn: sqlite3.Connection,
    graph_conn: lb.Connection,
    checks: list[tuple[str, str, str]],
) -> None:
    for name, sqlite_query, graph_query in checks:
        assert_eq(
            name,
            scalar_sql(sqlite_conn, sqlite_query),
            scalar_g(graph_conn, graph_query),
        )


def validate_sql_zero_checks(
    sqlite_conn: sqlite3.Connection,
    checks: list[tuple[str, str]],
) -> None:
    for name, sqlite_query in checks:
        assert_zero(name, scalar_sql(sqlite_conn, sqlite_query))


def validate_graph_zero_checks(
    graph_conn: lb.Connection,
    checks: list[tuple[str, str]],
) -> None:
    for name, graph_query in checks:
        assert_zero(name, scalar_g(graph_conn, graph_query))


def validate_conditional_graph_positive_checks(
    sqlite_conn: sqlite3.Connection,
    graph_conn: lb.Connection,
    checks: list[tuple[str, str]]
) -> None:
    for name, prerequisite_sql, graph_query in checks:
        if scalar_sql(sqlite_conn, prerequisite_sql) == 0:
            continue
        assert_positive(name, scalar_g(graph_conn, graph_query))


def main() -> None:
    sqlite_conn = None
    graph_db = None
    graph_conn = None

    try:
        sqlite_conn = sqlite3.connect(SQLITE_DB)
        graph_db = lb.Database(GRAPH_DB)
        graph_conn = lb.Connection(graph_db)

        node_checks = [
            (
                "Entity",
                "SELECT COUNT(*) FROM entity_nodes",
                "MATCH (n:Entity) RETURN count(n)",
            ),
            (
                "Symbol",
                "SELECT COUNT(*) FROM symbols",
                "MATCH (n:Symbol) RETURN count(n)",
            ),
            ("File", "SELECT COUNT(*) FROM files", "MATCH (n:File) RETURN count(n)"),
            (
                "Workflow",
                "SELECT COUNT(*) FROM workflows",
                "MATCH (n:Workflow) RETURN count(n)",
            ),
            (
                "RestEndpoint",
                "SELECT COUNT(*) FROM rest_endpoints",
                "MATCH (n:RestEndpoint) RETURN count(n)",
            ),
            (
                "WorkflowNode",
                "SELECT COUNT(*) FROM workflow_nodes",
                "MATCH (n:WorkflowNode) RETURN count(n)",
            ),
            (
                "OpenApiSpec",
                "SELECT COUNT(*) FROM openapispec_index",
                "MATCH (n:OpenApiSpec) RETURN count(n)",
            ),
            (
                "SecurityOperation",
                "SELECT COUNT(*) FROM security_operations",
                "MATCH (n:SecurityOperation) RETURN count(n)",
            ),
            (
                "SecurityPolicy",
                "SELECT COUNT(*) FROM security_policies",
                "MATCH (n:SecurityPolicy) RETURN count(n)",
            ),
            (
                "SecurityPolicyValue",
                "SELECT COUNT(*) FROM security_policy_values",
                "MATCH (n:SecurityPolicyValue) RETURN count(n)",
            ),
            (
                "SecurityMenu",
                "SELECT COUNT(*) FROM security_menus",
                "MATCH (n:SecurityMenu) RETURN count(n)",
            ),
            (
                "SecurityMenuItem",
                "SELECT COUNT(*) FROM security_menu_items",
                "MATCH (n:SecurityMenuItem) RETURN count(n)",
            ),
            (
                "DbTable",
                "SELECT COUNT(*) FROM dbschema_tables",
                "MATCH (n:DbTable) RETURN count(n)",
            ),
            (
                "DbField",
                "SELECT COUNT(*) FROM dbschema_fields",
                "MATCH (n:DbField) RETURN count(n)",
            ),
            (
                "EntityAccessLink",
                "SELECT COUNT(*) FROM entity_access_links",
                "MATCH (n:EntityAccessLink) RETURN count(n)",
            ),
        ]

        rel_checks = [
            (
                "ENTITY_ROOT",
                "SELECT COUNT(*) FROM entity_roots",
                "MATCH ()-[r:ENTITY_ROOT]->() RETURN count(r)",
            ),
            (
                "ENTITY_MAPPING",
                "SELECT COUNT(*) FROM entity_mappings WHERE symbol_id IS NOT NULL",
                "MATCH ()-[r:ENTITY_MAPPING]->() RETURN count(r)",
            ),
            (
                "INHERITS",
                """
                SELECT COUNT(*)
                FROM relationships
                WHERE relationship_type = 'INHERITS'
                  AND source_symbol_id IS NOT NULL
                  AND target_symbol_id IS NOT NULL
                """,
                "MATCH ()-[r:INHERITS]->() RETURN count(r)",
            ),
            (
                "IMPLEMENTS",
                """
                SELECT COUNT(*)
                FROM relationships
                WHERE relationship_type = 'IMPLEMENTS'
                  AND source_symbol_id IS NOT NULL
                  AND target_symbol_id IS NOT NULL
                """,
                "MATCH ()-[r:IMPLEMENTS]->() RETURN count(r)",
            ),
            (
                "IMPORTS",
                """
                SELECT COUNT(*)
                FROM relationships
                WHERE relationship_type = 'IMPORTS'
                  AND source_symbol_id IS NOT NULL
                  AND target_symbol_id IS NOT NULL
                """,
                "MATCH ()-[r:IMPORTS]->() RETURN count(r)",
            ),
            (
                "USES",
                """
                SELECT COUNT(*)
                FROM relationships
                WHERE relationship_type = 'USES'
                  AND source_symbol_id IS NOT NULL
                  AND target_symbol_id IS NOT NULL
                """,
                "MATCH ()-[r:USES]->() RETURN count(r)",
            ),
            (
                "REFERENCES",
                """
                SELECT COUNT(*)
                FROM relationships
                WHERE relationship_type = 'REFERENCES'
                  AND source_symbol_id IS NOT NULL
                  AND target_symbol_id IS NOT NULL
                """,
                "MATCH ()-[r:REFERENCES]->() RETURN count(r)",
            ),
            (
                "CALLS",
                """
                SELECT COUNT(*)
                FROM relationships
                WHERE relationship_type IN ('CALLS', 'STATIC_CALLS')
                  AND source_symbol_id IS NOT NULL
                  AND target_symbol_id IS NOT NULL
                """,
                "MATCH ()-[r:CALLS]->() RETURN count(r)",
            ),
            (
                "DECLARED_IN",
                "SELECT COUNT(*) FROM symbols WHERE file_id IS NOT NULL",
                "MATCH ()-[r:DECLARED_IN]->() RETURN count(r)",
            ),
            (
                "HAS_WORKFLOW",
                "SELECT COUNT(*) FROM workflows WHERE entity_id IS NOT NULL",
                "MATCH ()-[r:HAS_WORKFLOW]->() RETURN count(r)",
            ),
            (
                "EXPOSES_ENTITY",
                "SELECT COUNT(*) FROM rest_endpoints WHERE entity_id IS NOT NULL",
                "MATCH ()-[r:EXPOSES_ENTITY]->() RETURN count(r)",
            ),
            (
                "HANDLED_BY",
                "SELECT COUNT(*) FROM rest_endpoints WHERE handler_symbol_id IS NOT NULL",
                "MATCH ()-[r:HANDLED_BY]->() RETURN count(r)",
            ),
            (
                "WORKFLOW_HAS_NODE",
                "SELECT COUNT(*) FROM workflow_nodes",
                "MATCH ()-[r:WORKFLOW_HAS_NODE]->() RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_FILE",
                "SELECT COUNT(*) FROM workflow_nodes WHERE file_id IS NOT NULL",
                "MATCH ()-[r:WORKFLOW_NODE_FILE]->() RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_SYMBOL",
                "SELECT COUNT(*) FROM workflow_nodes WHERE symbol_id IS NOT NULL",
                "MATCH ()-[r:WORKFLOW_NODE_SYMBOL]->() RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_EDGE",
                "SELECT COUNT(*) FROM workflow_edges",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() RETURN count(r)",
            ),
            (
                "OPENAPI_SPEC_FILE",
                "SELECT COUNT(*) FROM openapispec_index WHERE file_id IS NOT NULL",
                "MATCH ()-[r:OPENAPI_SPEC_FILE]->() RETURN count(r)",
            ),
            (
                "OPENAPI_FILE_REF",
                "SELECT COUNT(*) FROM openapi_file_ref_edges",
                "MATCH ()-[r:OPENAPI_FILE_REF]->() RETURN count(r)",
            ),
            (
                "SECURITY_OPERATION_FILE",
                "SELECT COUNT(*) FROM security_operations WHERE file_id IS NOT NULL",
                "MATCH ()-[r:SECURITY_OPERATION_FILE]->() RETURN count(r)",
            ),
            (
                "SECURITY_POLICY_FILE",
                "SELECT COUNT(*) FROM security_policies WHERE file_id IS NOT NULL",
                "MATCH ()-[r:SECURITY_POLICY_FILE]->() RETURN count(r)",
            ),
            (
                "SECURITY_POLICY_HAS_VALUE",
                "SELECT COUNT(*) FROM security_policy_values",
                "MATCH ()-[r:SECURITY_POLICY_HAS_VALUE]->() RETURN count(r)",
            ),
            (
                "SECURITY_MENU_FILE",
                "SELECT COUNT(*) FROM security_menus WHERE file_id IS NOT NULL",
                "MATCH ()-[r:SECURITY_MENU_FILE]->() RETURN count(r)",
            ),
            (
                "SECURITY_MENU_HAS_ITEM",
                "SELECT COUNT(*) FROM security_menu_items",
                "MATCH ()-[r:SECURITY_MENU_HAS_ITEM]->() RETURN count(r)",
            ),
            (
                "SECURITY_MENU_ITEM_TO_OPERATION",
                "SELECT COUNT(*) FROM security_menu_op_links WHERE operation_id IS NOT NULL",
                "MATCH ()-[r:SECURITY_MENU_ITEM_TO_OPERATION]->() RETURN count(r)",
            ),
            (
                "DBTABLE_FILE",
                "SELECT COUNT(*) FROM dbschema_tables WHERE file_id IS NOT NULL",
                "MATCH ()-[r:DBTABLE_FILE]->() RETURN count(r)",
            ),
            (
                "DBTABLE_HAS_FIELD",
                "SELECT COUNT(*) FROM dbschema_fields",
                "MATCH ()-[r:DBTABLE_HAS_FIELD]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_ENTITY",
                "SELECT COUNT(*) FROM entity_access_links",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_ENTITY]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_FILE",
                "SELECT COUNT(*) FROM entity_access_links WHERE evidence_file_id IS NOT NULL",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_FILE]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_SYMBOL",
                "SELECT COUNT(*) FROM entity_access_links WHERE evidence_symbol_id IS NOT NULL",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_SYMBOL]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_WORKFLOW",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'workflow'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_WORKFLOW]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_REST_ENDPOINT",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'rest_endpoint'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_REST_ENDPOINT]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_SECURITY_OPERATION",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'security_operation'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_SECURITY_POLICY",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'security_policy'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_SECURITY_POLICY]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_SECURITY_MENU",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'security_menu'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_SECURITY_MENU]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'security_menu_item'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM]->() RETURN count(r)",
            ),
            (
                "ENTITY_ACCESS_LINK_DBTABLE",
                "SELECT COUNT(*) FROM entity_access_links WHERE surface = 'dbschema_table'",
                "MATCH ()-[r:ENTITY_ACCESS_LINK_DBTABLE]->() RETURN count(r)",
            ),
        ]

        workflow_edge_kind_checks = [
            (
                "WORKFLOW_NODE_EDGE:workflow_contains",
                "SELECT COUNT(*) FROM workflow_edges WHERE edge_kind = 'workflow_contains'",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() WHERE r.edge_kind = 'workflow_contains' RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_EDGE:step_next",
                "SELECT COUNT(*) FROM workflow_edges WHERE edge_kind = 'step_next'",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() WHERE r.edge_kind = 'step_next' RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_EDGE:step_uses_file",
                "SELECT COUNT(*) FROM workflow_edges WHERE edge_kind = 'step_uses_file'",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() WHERE r.edge_kind = 'step_uses_file' RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_EDGE:step_uses_symbol",
                "SELECT COUNT(*) FROM workflow_edges WHERE edge_kind = 'step_uses_symbol'",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() WHERE r.edge_kind = 'step_uses_symbol' RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_EDGE:step_exposes_endpoint",
                "SELECT COUNT(*) FROM workflow_edges WHERE edge_kind = 'step_exposes_endpoint'",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() WHERE r.edge_kind = 'step_exposes_endpoint' RETURN count(r)",
            ),
            (
                "WORKFLOW_NODE_EDGE:step_references_openapi_ref",
                "SELECT COUNT(*) FROM workflow_edges WHERE edge_kind = 'step_references_openapi_ref'",
                "MATCH ()-[r:WORKFLOW_NODE_EDGE]->() WHERE r.edge_kind = 'step_references_openapi_ref' RETURN count(r)",
            ),
        ]

        sql_zero_checks = [
            (
                "workflow_edges missing from_node",
                """
                SELECT COUNT(*)
                FROM workflow_edges we
                LEFT JOIN workflow_nodes wn ON wn.id = we.from_node_id
                WHERE wn.id IS NULL
                """,
            ),
            (
                "workflow_edges missing to_node",
                """
                SELECT COUNT(*)
                FROM workflow_edges we
                LEFT JOIN workflow_nodes wn ON wn.id = we.to_node_id
                WHERE wn.id IS NULL
                """,
            ),
            (
                "workflow_nodes missing workflow",
                """
                SELECT COUNT(*)
                FROM workflow_nodes wn
                LEFT JOIN workflows w ON w.id = wn.workflow_id
                WHERE w.id IS NULL
                """,
            ),
            (
                "workflow_nodes file_id missing file",
                """
                SELECT COUNT(*)
                FROM workflow_nodes wn
                LEFT JOIN files f ON f.id = wn.file_id
                WHERE wn.file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "workflow_nodes symbol_id missing symbol",
                """
                SELECT COUNT(*)
                FROM workflow_nodes wn
                LEFT JOIN symbols s ON s.id = wn.symbol_id
                WHERE wn.symbol_id IS NOT NULL
                  AND s.id IS NULL
                """,
            ),
            (
                "openapi_file_ref_edges missing source file",
                """
                SELECT COUNT(*)
                FROM openapi_file_ref_edges oe
                LEFT JOIN files f ON f.id = oe.source_file_id
                WHERE f.id IS NULL
                """,
            ),
            (
                "openapi_file_ref_edges missing target file",
                """
                SELECT COUNT(*)
                FROM openapi_file_ref_edges oe
                LEFT JOIN files f ON f.id = oe.target_file_id
                WHERE f.id IS NULL
                """,
            ),
            (
                "security_menu_op_links dangling menu item",
                """
                SELECT COUNT(*)
                FROM security_menu_op_links l
                LEFT JOIN security_menu_items i ON i.id = l.menu_item_id
                WHERE i.id IS NULL
                """,
            ),
            (
                "security_menu_op_links dangling operation",
                """
                SELECT COUNT(*)
                FROM security_menu_op_links l
                LEFT JOIN security_operations o ON o.id = l.operation_id
                WHERE l.operation_id IS NOT NULL
                  AND o.id IS NULL
                """,
            ),
            (
                "dbschema_fields missing dbschema_table",
                """
                SELECT COUNT(*)
                FROM dbschema_fields df
                LEFT JOIN dbschema_tables dt ON dt.id = df.dbschema_table_id
                WHERE dt.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid workflow record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN workflows w ON w.id = e.record_id
                WHERE e.surface = 'workflow'
                  AND w.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid rest_endpoint record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN rest_endpoints r ON r.id = e.record_id
                WHERE e.surface = 'rest_endpoint'
                  AND r.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid security_operation record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN security_operations so ON so.id = e.record_id
                WHERE e.surface = 'security_operation'
                  AND so.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid security_policy record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN security_policies sp ON sp.id = e.record_id
                WHERE e.surface = 'security_policy'
                  AND sp.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid security_menu record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN security_menus sm ON sm.id = e.record_id
                WHERE e.surface = 'security_menu'
                  AND sm.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid security_menu_item record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN security_menu_items smi ON smi.id = e.record_id
                WHERE e.surface = 'security_menu_item'
                  AND smi.id IS NULL
                """,
            ),
            (
                "entity_access_links invalid dbschema_table record_id",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN dbschema_tables dt ON dt.id = e.record_id
                WHERE e.surface = 'dbschema_table'
                  AND dt.id IS NULL
                """,
            ),
                        (
                "openapispec_index file_id missing file",
                """
                SELECT COUNT(*)
                FROM openapispec_index oi
                LEFT JOIN files f ON f.id = oi.file_id
                WHERE oi.file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "security_operations file_id missing file",
                """
                SELECT COUNT(*)
                FROM security_operations so
                LEFT JOIN files f ON f.id = so.file_id
                WHERE so.file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "security_policies file_id missing file",
                """
                SELECT COUNT(*)
                FROM security_policies sp
                LEFT JOIN files f ON f.id = sp.file_id
                WHERE sp.file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "security_menus file_id missing file",
                """
                SELECT COUNT(*)
                FROM security_menus sm
                LEFT JOIN files f ON f.id = sm.file_id
                WHERE sm.file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "dbschema_tables file_id missing file",
                """
                SELECT COUNT(*)
                FROM dbschema_tables dt
                LEFT JOIN files f ON f.id = dt.file_id
                WHERE dt.file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "entity_access_links evidence_file_id missing file",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN files f ON f.id = e.evidence_file_id
                WHERE e.evidence_file_id IS NOT NULL
                  AND f.id IS NULL
                """,
            ),
            (
                "entity_access_links evidence_symbol_id missing symbol",
                """
                SELECT COUNT(*)
                FROM entity_access_links e
                LEFT JOIN symbols s ON s.id = e.evidence_symbol_id
                WHERE e.evidence_symbol_id IS NOT NULL
                  AND s.id IS NULL
                """,
            ),
            (
                "workflow_edges unexpected edge_kind",
                """
                SELECT COUNT(*)
                FROM workflow_edges
                WHERE edge_kind NOT IN (
                    'workflow_contains',
                    'step_next',
                    'step_uses_file',
                    'step_uses_symbol',
                    'step_exposes_endpoint',
                    'step_references_openapi_ref'
                )
                """,
            ),
        ]

        graph_zero_checks = [
            (
                "duplicate ENTITY_ROOT edges",
                """
                MATCH (e:Entity)-[r:ENTITY_ROOT]->(s:Symbol)
                WITH e.entity_id AS entity_id, s.symbol_id AS symbol_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate WORKFLOW_HAS_NODE edges",
                """
                MATCH (w:Workflow)-[r:WORKFLOW_HAS_NODE]->(n:WorkflowNode)
                WITH w.workflow_id AS workflow_id, n.workflow_node_id AS workflow_node_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate WORKFLOW_NODE_FILE edges",
                """
                MATCH (n:WorkflowNode)-[r:WORKFLOW_NODE_FILE]->(f:File)
                WITH n.workflow_node_id AS workflow_node_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate WORKFLOW_NODE_SYMBOL edges",
                """
                MATCH (n:WorkflowNode)-[r:WORKFLOW_NODE_SYMBOL]->(s:Symbol)
                WITH n.workflow_node_id AS workflow_node_id, s.symbol_id AS symbol_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate WORKFLOW_NODE_EDGE edges",
                """
                MATCH (a:WorkflowNode)-[r:WORKFLOW_NODE_EDGE]->(b:WorkflowNode)
                WITH
                    a.workflow_node_id AS from_id,
                    b.workflow_node_id AS to_id,
                    r.workflow_id AS workflow_id,
                    r.edge_kind AS edge_kind,
                    r.ordinal AS ordinal,
                    r.evidence AS evidence,
                    count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate OPENAPI_FILE_REF edges",
                """
                MATCH (a:File)-[r:OPENAPI_FILE_REF]->(b:File)
                WITH
                    a.file_id AS source_file_id,
                    b.file_id AS target_file_id,
                    r.ref_value AS ref_value,
                    r.ref_path AS ref_path,
                    count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate SECURITY_POLICY_HAS_VALUE edges",
                """
                MATCH (p:SecurityPolicy)-[r:SECURITY_POLICY_HAS_VALUE]->(v:SecurityPolicyValue)
                WITH p.security_policy_id AS policy_id, v.security_policy_value_id AS value_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate SECURITY_MENU_HAS_ITEM edges",
                """
                MATCH (m:SecurityMenu)-[r:SECURITY_MENU_HAS_ITEM]->(i:SecurityMenuItem)
                WITH m.security_menu_id AS menu_id, i.security_menu_item_id AS item_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate DBTABLE_HAS_FIELD edges",
                """
                MATCH (t:DbTable)-[r:DBTABLE_HAS_FIELD]->(f:DbField)
                WITH t.dbschema_table_id AS table_id, f.dbschema_field_id AS field_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_ENTITY edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_ENTITY]->(e:Entity)
                WITH l.entity_access_link_id AS link_id, e.entity_id AS entity_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
                        (
                "duplicate DECLARED_IN edges",
                """
                MATCH (s:Symbol)-[r:DECLARED_IN]->(f:File)
                WITH s.symbol_id AS symbol_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate OPENAPI_SPEC_FILE edges",
                """
                MATCH (o:OpenApiSpec)-[r:OPENAPI_SPEC_FILE]->(f:File)
                WITH o.openapi_id AS openapi_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate SECURITY_OPERATION_FILE edges",
                """
                MATCH (o:SecurityOperation)-[r:SECURITY_OPERATION_FILE]->(f:File)
                WITH o.security_operation_id AS operation_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate SECURITY_POLICY_FILE edges",
                """
                MATCH (p:SecurityPolicy)-[r:SECURITY_POLICY_FILE]->(f:File)
                WITH p.security_policy_id AS policy_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate SECURITY_MENU_FILE edges",
                """
                MATCH (m:SecurityMenu)-[r:SECURITY_MENU_FILE]->(f:File)
                WITH m.security_menu_id AS menu_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate SECURITY_MENU_ITEM_TO_OPERATION edges",
                """
                MATCH (i:SecurityMenuItem)-[r:SECURITY_MENU_ITEM_TO_OPERATION]->(o:SecurityOperation)
                WITH
                    i.security_menu_item_id AS menu_item_id,
                    o.security_operation_id AS operation_id,
                    r.op_key AS op_key,
                    r.resolution_reason AS resolution_reason,
                    count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_FILE edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_FILE]->(f:File)
                WITH l.entity_access_link_id AS link_id, f.file_id AS file_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_SYMBOL edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_SYMBOL]->(s:Symbol)
                WITH l.entity_access_link_id AS link_id, s.symbol_id AS symbol_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_WORKFLOW edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_WORKFLOW]->(w:Workflow)
                WITH l.entity_access_link_id AS link_id, w.workflow_id AS workflow_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_REST_ENDPOINT edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_REST_ENDPOINT]->(re:RestEndpoint)
                WITH l.entity_access_link_id AS link_id, re.rest_endpoint_id AS rest_endpoint_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_SECURITY_OPERATION edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->(o:SecurityOperation)
                WITH l.entity_access_link_id AS link_id, o.security_operation_id AS operation_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_SECURITY_POLICY edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_SECURITY_POLICY]->(p:SecurityPolicy)
                WITH l.entity_access_link_id AS link_id, p.security_policy_id AS policy_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_SECURITY_MENU edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_SECURITY_MENU]->(m:SecurityMenu)
                WITH l.entity_access_link_id AS link_id, m.security_menu_id AS menu_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM]->(i:SecurityMenuItem)
                WITH l.entity_access_link_id AS link_id, i.security_menu_item_id AS menu_item_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
            (
                "duplicate ENTITY_ACCESS_LINK_DBTABLE edges",
                """
                MATCH (l:EntityAccessLink)-[r:ENTITY_ACCESS_LINK_DBTABLE]->(t:DbTable)
                WITH l.entity_access_link_id AS link_id, t.dbschema_table_id AS table_id, count(r) AS cnt
                WHERE cnt > 1
                RETURN count(*)
                """,
            ),
        ]

        graph_positive_checks = [
            (
                "Entity -> Workflow -> WorkflowNode traversal",
                """
        SELECT COUNT(*)
        FROM workflows w
        JOIN workflow_nodes wn ON wn.workflow_id = w.id
        WHERE w.entity_id IS NOT NULL
        """,
                """
        MATCH (:Entity)-[:HAS_WORKFLOW]->(:Workflow)-[:WORKFLOW_HAS_NODE]->(:WorkflowNode)
        RETURN count(*)
        """,
            ),
            (
                "EntityAccessLink -> SecurityMenu -> SecurityMenuItem traversal",
                """
        SELECT COUNT(*)
        FROM entity_access_links e
        JOIN security_menus sm
            ON sm.id = e.record_id
        JOIN security_menu_items smi
            ON smi.menu_id = sm.id
        WHERE e.surface = 'security_menu'
        """,
                """
        MATCH (:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_MENU]->(:SecurityMenu)-[:SECURITY_MENU_HAS_ITEM]->(:SecurityMenuItem)
        RETURN count(*)
        """,
            ),
            (
                "OpenApiSpec -> File traversal",
                """
        SELECT COUNT(*)
        FROM openapispec_index
        WHERE file_id IS NOT NULL
        """,
                """
        MATCH (:OpenApiSpec)-[:OPENAPI_SPEC_FILE]->(:File)
        RETURN count(*)
        """,
            ),
        ]

        validate_counts(sqlite_conn, graph_conn, node_checks)
        validate_counts(sqlite_conn, graph_conn, rel_checks)
        validate_counts(sqlite_conn, graph_conn, workflow_edge_kind_checks)

        validate_sql_zero_checks(sqlite_conn, sql_zero_checks)
        validate_graph_zero_checks(graph_conn, graph_zero_checks)
        validate_conditional_graph_positive_checks(sqlite_conn, graph_conn, graph_positive_checks)

        print("Ladybug graph parity validation passed")
    finally:
        if graph_conn is not None:
            graph_conn.close()
        if graph_db is not None:
            graph_db.close()
        if sqlite_conn is not None:
            sqlite_conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Validation failed: {exc}")
        sys.exit(1)
