#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from itertools import zip_longest
from pathlib import Path

import ladybug as lb

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.graph_projection import (
    GRAPH_PROJECTION_VERSION,
    RELATIONSHIP_PROJECTIONS,
    iter_available_node_projections,
    iter_available_projections,
    iter_available_relationship_projections,
)
from config import CATALOG_DB as SQLITE_DB
from config import GRAPH_DB


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
    checks: list[tuple[str, str]],
) -> None:
    for name, prerequisite_sql, graph_query in checks:
        if scalar_sql(sqlite_conn, prerequisite_sql) == 0:
            continue
        assert_positive(name, scalar_g(graph_conn, graph_query))


def _canonical_value(value):
    if isinstance(value, float):
        return {"float": format(value, ".17g")}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    return value


def _canonical_row(row) -> bytes:
    normalized = [_canonical_value(value) for value in tuple(row)]
    return (
        json.dumps(normalized, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode()


def _sqlite_file_fingerprint(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ordered_graph_rows(conn: lb.Connection, query: str):
    result = conn.execute(query)
    try:
        while result.has_next():
            yield tuple(result.get_next())
    finally:
        result.close()


def _compare_exact_check(
    name: str,
    sqlite_conn: sqlite3.Connection,
    graph_conn: lb.Connection,
    sqlite_query: str,
    graph_query: str,
) -> dict[str, object]:
    sql_cursor = sqlite_conn.execute(sqlite_query)
    sql_digest = hashlib.sha256()
    graph_digest = hashlib.sha256()
    sql_count = 0
    graph_count = 0
    differences: list[dict[str, object]] = []
    missing = object()

    for ordinal, pair in enumerate(
        zip_longest(
            sql_cursor, _ordered_graph_rows(graph_conn, graph_query), fillvalue=missing
        )
    ):
        sql_row, graph_row = pair
        if sql_row is not missing:
            sql_count += 1
            sql_bytes = _canonical_row(sql_row)
            sql_digest.update(sql_bytes)
        else:
            sql_bytes = None
        if graph_row is not missing:
            graph_count += 1
            graph_bytes = _canonical_row(graph_row)
            graph_digest.update(graph_bytes)
        else:
            graph_bytes = None
        if sql_bytes != graph_bytes and len(differences) < 5:
            differences.append(
                {
                    "ordinal": ordinal,
                    "sqlite": None if sql_row is missing else tuple(sql_row),
                    "graph": None if graph_row is missing else tuple(graph_row),
                }
            )

    sql_hex = sql_digest.hexdigest()
    graph_hex = graph_digest.hexdigest()
    if sql_count != graph_count or sql_hex != graph_hex:
        raise RuntimeError(
            f"{name} exact parity mismatch: sqlite_count={sql_count} "
            f"graph_count={graph_count} sqlite_digest={sql_hex} "
            f"graph_digest={graph_hex} differences={differences}"
        )
    return {"name": name, "count": sql_count, "digest": sql_hex}


def validate_exact_parity(
    sqlite_conn: sqlite3.Connection, graph_conn: lb.Connection
) -> list[dict[str, object]]:
    checks: list[tuple[str, str, str]] = []
    for projection in iter_available_projections(sqlite_conn):
        checks.append(
            (
                projection.ladybug_table,
                projection.source_query,
                projection.graph_query,
            )
        )
    return [
        _compare_exact_check(name, sqlite_conn, graph_conn, sql_query, graph_query)
        for name, sql_query, graph_query in checks
    ]


def validate_paths(
    sqlite_path: str = SQLITE_DB,
    graph_path: str = GRAPH_DB,
    expected_fingerprint: str | None = None,
    expected_catalog_build_id: int | None = None,
    expected_projection_version: int | None = None,
) -> str:
    sqlite_conn = None
    graph_db = None
    graph_conn = None

    try:
        if (
            expected_projection_version is not None
            and expected_projection_version != GRAPH_PROJECTION_VERSION
        ):
            raise RuntimeError(
                "unsupported graph projection version: "
                f"expected={expected_projection_version} "
                f"supported={GRAPH_PROJECTION_VERSION}"
            )
        sqlite_conn = sqlite3.connect(
            f"file:{Path(sqlite_path).resolve()}?mode=ro", uri=True
        )
        violations = sqlite_conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            sample = [tuple(row) for row in violations[:5]]
            raise RuntimeError(
                f"invalid SQLite snapshot before graph validation: {len(violations)} "
                f"foreign-key violation(s); sample={sample}"
            )
        integrity = sqlite_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"invalid SQLite snapshot integrity: {integrity}")
        active_catalog = None
        if sqlite_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_builds'"
        ).fetchone():
            active_catalog = sqlite_conn.execute(
                "SELECT id,content_fingerprint FROM catalog_builds "
                "WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if expected_catalog_build_id is not None and (
            active_catalog is None
            or int(active_catalog[0]) != expected_catalog_build_id
        ):
            actual = None if active_catalog is None else int(active_catalog[0])
            raise RuntimeError(
                f"catalog generation mismatch: expected={expected_catalog_build_id} actual={actual}"
            )
        graph_db = lb.Database(graph_path, read_only=True)
        graph_conn = lb.Connection(graph_db)

        node_checks = []
        for projection in iter_available_node_projections(sqlite_conn):
            node_checks.append(
                (
                    projection.ladybug_table,
                    f"SELECT COUNT(*) FROM ({projection.source_query}) AS projection",
                    f"MATCH (n:{projection.ladybug_table}) RETURN count(n)",
                )
            )

        rel_checks = []
        for projection in iter_available_relationship_projections(sqlite_conn):
            rel_checks.append(
                (
                    projection.ladybug_table,
                    f"SELECT COUNT(*) FROM ({projection.source_query}) AS projection",
                    f"MATCH ()-[r:{projection.ladybug_table}]->() RETURN count(r)",
                )
            )

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

        starting_file_fingerprint = _sqlite_file_fingerprint(sqlite_path)
        starting_fingerprint = logical_content_fingerprint(sqlite_conn)
        if (
            expected_fingerprint is not None
            and starting_fingerprint != expected_fingerprint
        ):
            raise RuntimeError(
                f"snapshot fingerprint mismatch before validation: "
                f"expected={expected_fingerprint} actual={starting_fingerprint}"
            )

        validate_counts(sqlite_conn, graph_conn, node_checks)
        validate_counts(sqlite_conn, graph_conn, rel_checks)
        parity_results = validate_exact_parity(sqlite_conn, graph_conn)
        validate_counts(sqlite_conn, graph_conn, workflow_edge_kind_checks)

        validate_sql_zero_checks(sqlite_conn, sql_zero_checks)
        validate_graph_zero_checks(graph_conn, graph_zero_checks)
        validate_conditional_graph_positive_checks(
            sqlite_conn, graph_conn, graph_positive_checks
        )

        ending_file_fingerprint = _sqlite_file_fingerprint(sqlite_path)
        if ending_file_fingerprint != starting_file_fingerprint:
            raise RuntimeError("SQLite snapshot changed during graph validation")
        summary = json.dumps(
            {
                "source_fingerprint": starting_fingerprint,
                "catalog_build_id": expected_catalog_build_id,
                "projection_version": expected_projection_version,
                "exact_check_count": len(parity_results),
                "relationship_family_count": len(RELATIONSHIP_PROJECTIONS),
                "projection_gap_count": 0,
                "invalid_endpoint_count": 0,
                "projected_row_count": sum(
                    int(item["count"]) for item in parity_results
                ),
            },
            sort_keys=True,
        )
        print("Ladybug graph parity validation passed")
        return summary
    finally:
        if graph_conn is not None:
            graph_conn.close()
        if graph_db is not None:
            graph_db.close()
        if sqlite_conn is not None:
            sqlite_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate SQLite/Ladybug graph parity."
    )
    parser.add_argument("--db", default=SQLITE_DB, help="SQLite catalog path")
    parser.add_argument("--graph", default=GRAPH_DB, help="Ladybug graph path")
    parser.add_argument("--expected-catalog-build-id", type=int)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--expected-projection-version", type=int)
    args = parser.parse_args()
    validate_paths(
        args.db,
        args.graph,
        expected_fingerprint=args.expected_fingerprint,
        expected_catalog_build_id=args.expected_catalog_build_id,
        expected_projection_version=args.expected_projection_version,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Validation failed: {exc}")
        sys.exit(1)
