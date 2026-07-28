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
    checks = [
        (
            "Entity",
            "SELECT id,name,entity_type FROM entity_nodes ORDER BY id",
            "MATCH (n:Entity) RETURN n.entity_id,n.name,n.entity_type ORDER BY n.entity_id",
        ),
        (
            "EntityOccurrence",
            "SELECT id,repo_id,entity_id,ent_file,module,table_name,view_name,dummy,source_file_id,extractor,confidence,created_at,updated_at FROM entity_occurrences ORDER BY id",
            "MATCH (n:EntityOccurrence) RETURN n.entity_occurrence_id,n.repo_id,n.entity_id,n.ent_file,n.module,n.table_name,n.view_name,n.dummy,n.source_file_id,n.extractor,n.confidence,n.created_at,n.updated_at ORDER BY n.entity_occurrence_id",
        ),
        (
            "Symbol",
            "SELECT id,name,kind,start_line,end_line,signature FROM symbols ORDER BY id",
            "MATCH (n:Symbol) RETURN n.symbol_id,n.name,n.kind,n.start_line,n.end_line,n.signature ORDER BY n.symbol_id",
        ),
        (
            "Repository",
            "SELECT id,repo_key,tracked_branch,indexed_commit_sha,COALESCE(last_built_at,last_scanned_at),index_status FROM repos ORDER BY id",
            "MATCH (n:Repository) RETURN n.repo_id,n.repo_key,n.tracked_branch,n.indexed_commit_sha,n.last_indexed_at,n.index_status ORDER BY n.repo_id",
        ),
        (
            "File",
            "SELECT f.id,f.repo_id,r.repo_key,f.path,f.language FROM files f JOIN repos r ON r.id=f.repo_id ORDER BY f.id",
            "MATCH (n:File) RETURN n.file_id,n.repo_id,n.repo_key,n.path,n.language ORDER BY n.file_id",
        ),
        (
            "Workflow",
            "SELECT id,name,workflow_type FROM workflows ORDER BY id",
            "MATCH (n:Workflow) RETURN n.workflow_id,n.name,n.workflow_type ORDER BY n.workflow_id",
        ),
        (
            "RestEndpoint",
            "SELECT id,method,path FROM rest_endpoints ORDER BY id",
            "MATCH (n:RestEndpoint) RETURN n.rest_endpoint_id,n.method,n.path ORDER BY n.rest_endpoint_id",
        ),
        (
            "WorkflowNode",
            "SELECT id,workflow_id,entity_id,node_kind,node_key,name,ordinal,action,source_kind,file_id,symbol_id,metadata_json,created_at FROM workflow_nodes ORDER BY id",
            "MATCH (n:WorkflowNode) RETURN n.workflow_node_id,n.workflow_id,n.entity_id,n.node_kind,n.node_key,n.name,n.ordinal,n.action,n.source_kind,n.file_id,n.symbol_id,n.metadata_json,n.created_at ORDER BY n.workflow_node_id",
        ),
        (
            "OpenApiSpec",
            "SELECT id,file_id,file_path,module,slug,version,kind,canonical_name,resource_path,x_mapped_to,title,state,last_seen_at FROM openapispec_index ORDER BY id",
            "MATCH (n:OpenApiSpec) RETURN n.openapi_id,n.file_id,n.file_path,n.module,n.slug,n.version,n.kind,n.canonical_name,n.resource_path,n.x_mapped_to,n.title,n.state,n.last_seen_at ORDER BY n.openapi_id",
        ),
        (
            "SecurityOperation",
            "SELECT id,op_key,op_numeric_id,title,action,script,force_mode,secure_only,allow_dev_env_only,source_file,file_id,source_line,source_kind,raw_hash FROM security_operations ORDER BY id",
            "MATCH (n:SecurityOperation) RETURN n.security_operation_id,n.op_key,n.op_numeric_id,n.title,n.action,n.script,n.force_mode,n.secure_only,n.allow_dev_env_only,n.source_file,n.file_id,n.source_line,n.source_kind,n.raw_hash ORDER BY n.security_operation_id",
        ),
        (
            "SecurityPolicy",
            "SELECT id,policy_name,module,label,source_file,file_id,source_line FROM security_policies ORDER BY id",
            "MATCH (n:SecurityPolicy) RETURN n.security_policy_id,n.policy_name,n.module,n.label,n.source_file,n.file_id,n.source_line ORDER BY n.security_policy_id",
        ),
        (
            "SecurityPolicyValue",
            "SELECT id,policy_id,value_key,display,value_label,source_line FROM security_policy_values ORDER BY id",
            "MATCH (n:SecurityPolicyValue) RETURN n.security_policy_value_id,n.policy_id,n.value_key,n.display,n.value_label,n.source_line ORDER BY n.security_policy_value_id",
        ),
        (
            "SecurityMenu",
            "SELECT id,module,menu_name,source_file,file_id FROM security_menus ORDER BY id",
            "MATCH (n:SecurityMenu) RETURN n.security_menu_id,n.module,n.menu_name,n.source_file,n.file_id ORDER BY n.security_menu_id",
        ),
        (
            "SecurityMenuItem",
            "SELECT id,menu_id,item_path,item_name,menu_item_id,menu_script,menu_key,source_line FROM security_menu_items ORDER BY id",
            "MATCH (n:SecurityMenuItem) RETURN n.security_menu_item_id,n.menu_id,n.item_path,n.item_name,n.menu_item_id,n.menu_script,n.menu_key,n.source_line ORDER BY n.security_menu_item_id",
        ),
        (
            "DbTable",
            "SELECT id,table_name,primary_keys,source_file,file_id,source_line,raw_hash FROM dbschema_tables ORDER BY id",
            "MATCH (n:DbTable) RETURN n.dbschema_table_id,n.table_name,n.primary_keys,n.source_file,n.file_id,n.source_line,n.raw_hash ORDER BY n.dbschema_table_id",
        ),
        (
            "DbField",
            "SELECT id,dbschema_table_id,field_name,field_type,source_line FROM dbschema_fields ORDER BY id",
            "MATCH (n:DbField) RETURN n.dbschema_field_id,n.dbschema_table_id,n.field_name,n.field_type,n.source_line ORDER BY n.dbschema_field_id",
        ),
        (
            "EntityAccessLink",
            "SELECT id,entity_id,surface,record_id,link_type,evidence_file_id,evidence_symbol_id,confidence_mode,notes,created_at FROM entity_access_links ORDER BY id",
            "MATCH (n:EntityAccessLink) RETURN n.entity_access_link_id,n.entity_id,n.surface,n.record_id,n.link_type,n.evidence_file_id,n.evidence_symbol_id,n.confidence_mode,n.notes,n.created_at ORDER BY n.entity_access_link_id",
        ),
        (
            "EntitySchemaComponent",
            "SELECT id,occurrence_id,component_kind,component_path,declared_name,target_literal,data_type,cardinality,writeability,confidence FROM entity_schema_components ORDER BY id",
            "MATCH (n:EntitySchemaComponent) RETURN n.entity_schema_component_id,n.occurrence_id,n.component_kind,n.component_path,n.declared_name,n.target_literal,n.data_type,n.cardinality,n.writeability,n.confidence ORDER BY n.entity_schema_component_id",
        ),
        (
            "EntityRelationshipFact",
            "SELECT id,source_occurrence_id,target_occurrence_id,axis,relation_kind,fact_key,assertion_status,target_entity_name,target_literal,cardinality,confidence,source_path,start_line,end_line FROM entity_relationship_facts ORDER BY id",
            "MATCH (n:EntityRelationshipFact) RETURN n.entity_relationship_fact_id,n.source_occurrence_id,n.target_occurrence_id,n.axis,n.relation_kind,n.fact_key,n.assertion_status,n.target_entity_name,n.target_literal,n.cardinality,n.confidence,n.source_path,n.start_line,n.end_line ORDER BY n.entity_relationship_fact_id",
        ),
        (
            "EntityOperationFact",
            "SELECT id,occurrence_id,axis,operation,surface_kind,availability,invocation_context,persistence_scope,standalone,confidence FROM entity_operation_facts ORDER BY id",
            "MATCH (n:EntityOperationFact) RETURN n.entity_operation_fact_id,n.occurrence_id,n.axis,n.operation,n.surface_kind,n.availability,n.invocation_context,n.persistence_scope,n.standalone,n.confidence ORDER BY n.entity_operation_fact_id",
        ),
        (
            "ENTITY_ROOT",
            "SELECT entity_id,symbol_id,role,weight FROM entity_roots ORDER BY entity_id,symbol_id,role,weight",
            "MATCH (a:Entity)-[r:ENTITY_ROOT]->(b:Symbol) RETURN a.entity_id,b.symbol_id,r.role,r.weight ORDER BY a.entity_id,b.symbol_id,r.role,r.weight",
        ),
        (
            "ENTITY_MAPPING",
            "SELECT entity_id,symbol_id,mapping_type,confidence FROM entity_mappings WHERE symbol_id IS NOT NULL ORDER BY entity_id,symbol_id,mapping_type,confidence",
            "MATCH (a:Entity)-[r:ENTITY_MAPPING]->(b:Symbol) RETURN a.entity_id,b.symbol_id,r.mapping_type,r.confidence ORDER BY a.entity_id,b.symbol_id,r.mapping_type,r.confidence",
        ),
        (
            "REPOSITORY_HAS_ENTITY_OCCURRENCE",
            "SELECT repo_id,id FROM entity_occurrences ORDER BY repo_id,id",
            "MATCH (a:Repository)-[:REPOSITORY_HAS_ENTITY_OCCURRENCE]->(b:EntityOccurrence) RETURN a.repo_id,b.entity_occurrence_id ORDER BY a.repo_id,b.entity_occurrence_id",
        ),
        (
            "ENTITY_HAS_OCCURRENCE",
            "SELECT entity_id,id FROM entity_occurrences ORDER BY entity_id,id",
            "MATCH (a:Entity)-[:ENTITY_HAS_OCCURRENCE]->(b:EntityOccurrence) RETURN a.entity_id,b.entity_occurrence_id ORDER BY a.entity_id,b.entity_occurrence_id",
        ),
        (
            "ENTITY_OCCURRENCE_FILE",
            "SELECT id,source_file_id FROM entity_occurrences WHERE source_file_id IS NOT NULL ORDER BY id",
            "MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_FILE]->(b:File) RETURN a.entity_occurrence_id,b.file_id ORDER BY a.entity_occurrence_id,b.file_id",
        ),
        (
            "ENTITY_OCCURRENCE_ROOT",
            "SELECT eo.id,er.symbol_id,er.role,er.weight FROM entity_roots er JOIN entity_occurrences eo ON eo.repo_id=er.repo_id AND eo.entity_id=er.entity_id ORDER BY eo.id,er.symbol_id,er.role,er.weight",
            "MATCH (a:EntityOccurrence)-[r:ENTITY_OCCURRENCE_ROOT]->(b:Symbol) RETURN a.entity_occurrence_id,b.symbol_id,r.role,r.weight ORDER BY a.entity_occurrence_id,b.symbol_id,r.role,r.weight",
        ),
        (
            "ENTITY_OCCURRENCE_MAPPING",
            "SELECT eo.id,em.symbol_id,em.mapping_type,em.confidence FROM entity_mappings em JOIN entity_occurrences eo ON eo.repo_id=em.repo_id AND eo.entity_id=em.entity_id WHERE em.symbol_id IS NOT NULL ORDER BY eo.id,em.symbol_id,em.mapping_type,em.confidence",
            "MATCH (a:EntityOccurrence)-[r:ENTITY_OCCURRENCE_MAPPING]->(b:Symbol) RETURN a.entity_occurrence_id,b.symbol_id,r.mapping_type,r.confidence ORDER BY a.entity_occurrence_id,b.symbol_id,r.mapping_type,r.confidence",
        ),
        (
            "ENTITY_OCCURRENCE_WORKFLOW",
            "SELECT eo.id,w.id FROM workflows w JOIN entity_occurrences eo ON eo.repo_id=w.repo_id AND eo.entity_id=w.entity_id WHERE w.entity_id IS NOT NULL ORDER BY eo.id,w.id",
            "MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_WORKFLOW]->(b:Workflow) RETURN a.entity_occurrence_id,b.workflow_id ORDER BY a.entity_occurrence_id,b.workflow_id",
        ),
        (
            "ENTITY_OCCURRENCE_REST_ENDPOINT",
            "SELECT eo.id,ep.id FROM rest_endpoints ep JOIN entity_occurrences eo ON eo.repo_id=ep.repo_id AND eo.entity_id=ep.entity_id WHERE ep.entity_id IS NOT NULL ORDER BY eo.id,ep.id",
            "MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_REST_ENDPOINT]->(b:RestEndpoint) RETURN a.entity_occurrence_id,b.rest_endpoint_id ORDER BY a.entity_occurrence_id,b.rest_endpoint_id",
        ),
        (
            "ENTITY_OCCURRENCE_HAS_COMPONENT",
            "SELECT occurrence_id,id FROM entity_schema_components ORDER BY occurrence_id,id",
            "MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_HAS_COMPONENT]->(b:EntitySchemaComponent) RETURN a.entity_occurrence_id,b.entity_schema_component_id ORDER BY a.entity_occurrence_id,b.entity_schema_component_id",
        ),
        (
            "ENTITY_OCCURRENCE_HAS_SEMANTIC_FACT",
            "SELECT source_occurrence_id,id FROM entity_relationship_facts ORDER BY source_occurrence_id,id",
            "MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_HAS_SEMANTIC_FACT]->(b:EntityRelationshipFact) RETURN a.entity_occurrence_id,b.entity_relationship_fact_id ORDER BY a.entity_occurrence_id,b.entity_relationship_fact_id",
        ),
        (
            "SEMANTIC_FACT_TARGET_OCCURRENCE",
            "SELECT id,target_occurrence_id FROM entity_relationship_facts "
            "WHERE target_occurrence_id IS NOT NULL "
            "AND assertion_status IN ('VERIFIED','CORROBORATED') "
            "ORDER BY id,target_occurrence_id",
            "MATCH (a:EntityRelationshipFact)-[:SEMANTIC_FACT_TARGET_OCCURRENCE]->(b:EntityOccurrence) RETURN a.entity_relationship_fact_id,b.entity_occurrence_id ORDER BY a.entity_relationship_fact_id,b.entity_occurrence_id",
        ),
        (
            "ENTITY_OCCURRENCE_HAS_OPERATION_FACT",
            "SELECT occurrence_id,id FROM entity_operation_facts ORDER BY occurrence_id,id",
            "MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_HAS_OPERATION_FACT]->(b:EntityOperationFact) RETURN a.entity_occurrence_id,b.entity_operation_fact_id ORDER BY a.entity_occurrence_id,b.entity_operation_fact_id",
        ),
        (
            "INHERITS",
            "SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='INHERITS' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
            "MATCH (a:Symbol)-[:INHERITS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
        ),
        (
            "IMPLEMENTS",
            "SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='IMPLEMENTS' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
            "MATCH (a:Symbol)-[:IMPLEMENTS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
        ),
        (
            "IMPORTS",
            "SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='IMPORTS' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
            "MATCH (a:Symbol)-[:IMPORTS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
        ),
        (
            "USES",
            "SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='USES' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
            "MATCH (a:Symbol)-[:USES]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
        ),
        (
            "REFERENCES",
            "SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='REFERENCES' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
            "MATCH (a:Symbol)-[:REFERENCES]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
        ),
        (
            "CALLS",
            "SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type IN ('CALLS','STATIC_CALLS') AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
            "MATCH (a:Symbol)-[:CALLS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
        ),
        (
            "DECLARED_IN",
            "SELECT id,file_id FROM symbols ORDER BY id,file_id",
            "MATCH (a:Symbol)-[:DECLARED_IN]->(b:File) RETURN a.symbol_id,b.file_id ORDER BY a.symbol_id,b.file_id",
        ),
        (
            "REPOSITORY_CONTAINS_FILE",
            "SELECT repo_id,id FROM files ORDER BY repo_id,id",
            "MATCH (a:Repository)-[:REPOSITORY_CONTAINS_FILE]->(b:File) RETURN a.repo_id,b.file_id ORDER BY a.repo_id,b.file_id",
        ),
        (
            "HAS_WORKFLOW",
            "SELECT entity_id,id FROM workflows WHERE entity_id IS NOT NULL ORDER BY entity_id,id",
            "MATCH (a:Entity)-[:HAS_WORKFLOW]->(b:Workflow) RETURN a.entity_id,b.workflow_id ORDER BY a.entity_id,b.workflow_id",
        ),
        (
            "EXPOSES_ENTITY",
            "SELECT id,entity_id FROM rest_endpoints WHERE entity_id IS NOT NULL ORDER BY id,entity_id",
            "MATCH (a:RestEndpoint)-[:EXPOSES_ENTITY]->(b:Entity) RETURN a.rest_endpoint_id,b.entity_id ORDER BY a.rest_endpoint_id,b.entity_id",
        ),
        (
            "HANDLED_BY",
            "SELECT id,handler_symbol_id FROM rest_endpoints WHERE handler_symbol_id IS NOT NULL ORDER BY id,handler_symbol_id",
            "MATCH (a:RestEndpoint)-[:HANDLED_BY]->(b:Symbol) RETURN a.rest_endpoint_id,b.symbol_id ORDER BY a.rest_endpoint_id,b.symbol_id",
        ),
        (
            "WORKFLOW_HAS_NODE",
            "SELECT workflow_id,id FROM workflow_nodes ORDER BY workflow_id,id",
            "MATCH (a:Workflow)-[:WORKFLOW_HAS_NODE]->(b:WorkflowNode) RETURN a.workflow_id,b.workflow_node_id ORDER BY a.workflow_id,b.workflow_node_id",
        ),
        (
            "WORKFLOW_NODE_FILE",
            "SELECT id,file_id FROM workflow_nodes WHERE file_id IS NOT NULL ORDER BY id,file_id",
            "MATCH (a:WorkflowNode)-[:WORKFLOW_NODE_FILE]->(b:File) RETURN a.workflow_node_id,b.file_id ORDER BY a.workflow_node_id,b.file_id",
        ),
        (
            "WORKFLOW_NODE_SYMBOL",
            "SELECT id,symbol_id FROM workflow_nodes WHERE symbol_id IS NOT NULL ORDER BY id,symbol_id",
            "MATCH (a:WorkflowNode)-[:WORKFLOW_NODE_SYMBOL]->(b:Symbol) RETURN a.workflow_node_id,b.symbol_id ORDER BY a.workflow_node_id,b.symbol_id",
        ),
        (
            "WORKFLOW_NODE_EDGE",
            "SELECT from_node_id,to_node_id,workflow_id,COALESCE(edge_kind,''),COALESCE(ordinal,-1),COALESCE(evidence,''),COALESCE(confidence,0.0),file_id,symbol_id FROM workflow_edges ORDER BY from_node_id,to_node_id,workflow_id,COALESCE(edge_kind,''),COALESCE(ordinal,-1),COALESCE(evidence,''),COALESCE(confidence,0.0),file_id,symbol_id",
            "MATCH (a:WorkflowNode)-[r:WORKFLOW_NODE_EDGE]->(b:WorkflowNode) RETURN a.workflow_node_id,b.workflow_node_id,r.workflow_id,r.edge_kind,r.ordinal,r.evidence,r.confidence,r.file_id,r.symbol_id ORDER BY a.workflow_node_id,b.workflow_node_id,r.workflow_id,r.edge_kind,r.ordinal,r.evidence,r.confidence,r.file_id,r.symbol_id",
        ),
        (
            "OPENAPI_SPEC_FILE",
            "SELECT id,file_id FROM openapispec_index WHERE file_id IS NOT NULL ORDER BY id,file_id",
            "MATCH (a:OpenApiSpec)-[:OPENAPI_SPEC_FILE]->(b:File) RETURN a.openapi_id,b.file_id ORDER BY a.openapi_id,b.file_id",
        ),
        (
            "OPENAPI_FILE_REF",
            "SELECT source_file_id,target_file_id,COALESCE(ref_value,''),COALESCE(ref_path,''),COALESCE(confidence,0.0) FROM openapi_file_ref_edges ORDER BY source_file_id,target_file_id,COALESCE(ref_value,''),COALESCE(ref_path,''),COALESCE(confidence,0.0)",
            "MATCH (a:File)-[r:OPENAPI_FILE_REF]->(b:File) RETURN a.file_id,b.file_id,r.ref_value,r.ref_path,r.confidence ORDER BY a.file_id,b.file_id,r.ref_value,r.ref_path,r.confidence",
        ),
        (
            "SECURITY_OPERATION_FILE",
            "SELECT id,file_id FROM security_operations WHERE file_id IS NOT NULL ORDER BY id,file_id",
            "MATCH (a:SecurityOperation)-[:SECURITY_OPERATION_FILE]->(b:File) RETURN a.security_operation_id,b.file_id ORDER BY a.security_operation_id,b.file_id",
        ),
        (
            "SECURITY_POLICY_FILE",
            "SELECT id,file_id FROM security_policies WHERE file_id IS NOT NULL ORDER BY id,file_id",
            "MATCH (a:SecurityPolicy)-[:SECURITY_POLICY_FILE]->(b:File) RETURN a.security_policy_id,b.file_id ORDER BY a.security_policy_id,b.file_id",
        ),
        (
            "SECURITY_POLICY_HAS_VALUE",
            "SELECT policy_id,id FROM security_policy_values ORDER BY policy_id,id",
            "MATCH (a:SecurityPolicy)-[:SECURITY_POLICY_HAS_VALUE]->(b:SecurityPolicyValue) RETURN a.security_policy_id,b.security_policy_value_id ORDER BY a.security_policy_id,b.security_policy_value_id",
        ),
        (
            "SECURITY_MENU_FILE",
            "SELECT id,file_id FROM security_menus WHERE file_id IS NOT NULL ORDER BY id,file_id",
            "MATCH (a:SecurityMenu)-[:SECURITY_MENU_FILE]->(b:File) RETURN a.security_menu_id,b.file_id ORDER BY a.security_menu_id,b.file_id",
        ),
        (
            "SECURITY_MENU_HAS_ITEM",
            "SELECT menu_id,id FROM security_menu_items ORDER BY menu_id,id",
            "MATCH (a:SecurityMenu)-[:SECURITY_MENU_HAS_ITEM]->(b:SecurityMenuItem) RETURN a.security_menu_id,b.security_menu_item_id ORDER BY a.security_menu_id,b.security_menu_item_id",
        ),
        (
            "SECURITY_MENU_ITEM_TO_OPERATION",
            "SELECT menu_item_id,operation_id,COALESCE(op_key,''),COALESCE(resolution_reason,'') FROM security_menu_op_links WHERE operation_id IS NOT NULL ORDER BY menu_item_id,operation_id,COALESCE(op_key,''),COALESCE(resolution_reason,'')",
            "MATCH (a:SecurityMenuItem)-[r:SECURITY_MENU_ITEM_TO_OPERATION]->(b:SecurityOperation) RETURN a.security_menu_item_id,b.security_operation_id,r.op_key,r.resolution_reason ORDER BY a.security_menu_item_id,b.security_operation_id,r.op_key,r.resolution_reason",
        ),
        (
            "DBTABLE_FILE",
            "SELECT id,file_id FROM dbschema_tables WHERE file_id IS NOT NULL ORDER BY id,file_id",
            "MATCH (a:DbTable)-[:DBTABLE_FILE]->(b:File) RETURN a.dbschema_table_id,b.file_id ORDER BY a.dbschema_table_id,b.file_id",
        ),
        (
            "DBTABLE_HAS_FIELD",
            "SELECT dbschema_table_id,id FROM dbschema_fields ORDER BY dbschema_table_id,id",
            "MATCH (a:DbTable)-[:DBTABLE_HAS_FIELD]->(b:DbField) RETURN a.dbschema_table_id,b.dbschema_field_id ORDER BY a.dbschema_table_id,b.dbschema_field_id",
        ),
        (
            "ENTITY_ACCESS_LINK_ENTITY",
            "SELECT id,entity_id FROM entity_access_links ORDER BY id,entity_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_ENTITY]->(b:Entity) RETURN a.entity_access_link_id,b.entity_id ORDER BY a.entity_access_link_id,b.entity_id",
        ),
        (
            "ENTITY_ACCESS_LINK_FILE",
            "SELECT id,evidence_file_id FROM entity_access_links WHERE evidence_file_id IS NOT NULL ORDER BY id,evidence_file_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_FILE]->(b:File) RETURN a.entity_access_link_id,b.file_id ORDER BY a.entity_access_link_id,b.file_id",
        ),
        (
            "ENTITY_ACCESS_LINK_SYMBOL",
            "SELECT id,evidence_symbol_id FROM entity_access_links WHERE evidence_symbol_id IS NOT NULL ORDER BY id,evidence_symbol_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SYMBOL]->(b:Symbol) RETURN a.entity_access_link_id,b.symbol_id ORDER BY a.entity_access_link_id,b.symbol_id",
        ),
        (
            "ENTITY_ACCESS_LINK_WORKFLOW",
            "SELECT id,record_id FROM entity_access_links WHERE surface='workflow' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_WORKFLOW]->(b:Workflow) RETURN a.entity_access_link_id,b.workflow_id ORDER BY a.entity_access_link_id,b.workflow_id",
        ),
        (
            "ENTITY_ACCESS_LINK_REST_ENDPOINT",
            "SELECT id,record_id FROM entity_access_links WHERE surface='rest_endpoint' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_REST_ENDPOINT]->(b:RestEndpoint) RETURN a.entity_access_link_id,b.rest_endpoint_id ORDER BY a.entity_access_link_id,b.rest_endpoint_id",
        ),
        (
            "ENTITY_ACCESS_LINK_SECURITY_OPERATION",
            "SELECT id,record_id FROM entity_access_links WHERE surface='security_operation' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->(b:SecurityOperation) RETURN a.entity_access_link_id,b.security_operation_id ORDER BY a.entity_access_link_id,b.security_operation_id",
        ),
        (
            "ENTITY_ACCESS_LINK_SECURITY_RESOURCE",
            "SELECT id,record_id FROM entity_access_links WHERE surface='security_resource' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_RESOURCE]->(b:SecurityOperation) RETURN a.entity_access_link_id,b.security_operation_id ORDER BY a.entity_access_link_id,b.security_operation_id",
        ),
        (
            "ENTITY_ACCESS_LINK_SECURITY_POLICY",
            "SELECT id,record_id FROM entity_access_links WHERE surface='security_policy' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_POLICY]->(b:SecurityPolicy) RETURN a.entity_access_link_id,b.security_policy_id ORDER BY a.entity_access_link_id,b.security_policy_id",
        ),
        (
            "ENTITY_ACCESS_LINK_SECURITY_MENU",
            "SELECT id,record_id FROM entity_access_links WHERE surface='security_menu' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_MENU]->(b:SecurityMenu) RETURN a.entity_access_link_id,b.security_menu_id ORDER BY a.entity_access_link_id,b.security_menu_id",
        ),
        (
            "ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM",
            "SELECT id,record_id FROM entity_access_links WHERE surface='security_menu_item' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM]->(b:SecurityMenuItem) RETURN a.entity_access_link_id,b.security_menu_item_id ORDER BY a.entity_access_link_id,b.security_menu_item_id",
        ),
        (
            "ENTITY_ACCESS_LINK_ENTITY_OCCURRENCE",
            "SELECT eal.id,eo.id FROM entity_access_links eal JOIN entity_occurrences eo ON eo.repo_id=eal.repo_id AND eo.entity_id=eal.entity_id ORDER BY eal.id,eo.id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_ENTITY_OCCURRENCE]->(b:EntityOccurrence) RETURN a.entity_access_link_id,b.entity_occurrence_id ORDER BY a.entity_access_link_id,b.entity_occurrence_id",
        ),
        (
            "ENTITY_ACCESS_LINK_DBTABLE",
            "SELECT id,record_id FROM entity_access_links WHERE surface='dbschema_table' ORDER BY id,record_id",
            "MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_DBTABLE]->(b:DbTable) RETURN a.entity_access_link_id,b.dbschema_table_id ORDER BY a.entity_access_link_id,b.dbschema_table_id",
        ),
        (
            "DOCUMENTS_ENTITY",
            "SELECT DISTINCT o.id,em.entity_id FROM openapispec_index o JOIN entity_mappings em ON em.file_id=o.file_id AND em.entity_id IS NOT NULL AND em.mapping_type LIKE 'openapispec_%' WHERE o.x_mapped_to IS NOT NULL AND TRIM(o.x_mapped_to)<>'' ORDER BY o.id,em.entity_id",
            "MATCH (a:OpenApiSpec)-[:DOCUMENTS_ENTITY]->(b:Entity) RETURN a.openapi_id,b.entity_id ORDER BY a.openapi_id,b.entity_id",
        ),
        (
            "POLICY_VALUE_GRANTS_OPERATION",
            "SELECT DISTINCT spv.id,so.id FROM security_policy_values spv JOIN security_policies sp ON sp.id=spv.policy_id JOIN security_policy_eops spe ON spe.policy_value_id=spv.id JOIN security_operations so ON so.repo_id=sp.repo_id AND so.op_key=spe.op_key WHERE (sp.repo_id,spe.op_key) IN (SELECT repo_id,op_key FROM security_operations GROUP BY repo_id,op_key HAVING COUNT(*)=1) ORDER BY spv.id,so.id",
            "MATCH (a:SecurityPolicyValue)-[:POLICY_VALUE_GRANTS_OPERATION]->(b:SecurityOperation) RETURN a.security_policy_value_id,b.security_operation_id ORDER BY a.security_policy_value_id,b.security_operation_id",
        ),
        (
            "ALLOWS_SECURITY_OPERATION",
            "SELECT operation_id,allowed_operation_id,COALESCE(allowed_op_key,''),COALESCE(resolution_reason,'') FROM security_operation_allowops WHERE allowed_operation_id IS NOT NULL ORDER BY operation_id,allowed_operation_id,id",
            "MATCH (a:SecurityOperation)-[r:ALLOWS_SECURITY_OPERATION]->(b:SecurityOperation) RETURN a.security_operation_id,b.security_operation_id,r.allowed_op_key,r.resolution_reason ORDER BY a.security_operation_id,b.security_operation_id,r.allowed_op_key,r.resolution_reason",
        ),
    ]
    semantic_tables_present = bool(
        sqlite_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='entity_schema_components'"
        ).fetchone()
    )
    if not semantic_tables_present:
        semantic_check_names = {
            "EntitySchemaComponent",
            "EntityRelationshipFact",
            "EntityOperationFact",
            "ENTITY_OCCURRENCE_HAS_COMPONENT",
            "ENTITY_OCCURRENCE_HAS_SEMANTIC_FACT",
            "SEMANTIC_FACT_TARGET_OCCURRENCE",
            "ENTITY_OCCURRENCE_HAS_OPERATION_FACT",
        }
        checks = [check for check in checks if check[0] not in semantic_check_names]
    integration_columns = {
        row[1] for row in sqlite_conn.execute("PRAGMA table_info(integration_links)")
    }
    if {"id", "source_file_id", "target_file_id"} <= integration_columns:
        relation_col = (
            "relation_type"
            if "relation_type" in integration_columns
            else "'integration'"
        )
        confidence_col = "confidence" if "confidence" in integration_columns else "0.0"
        status_col = (
            "resolution_status"
            if "resolution_status" in integration_columns
            else "'resolved'"
        )
        status_filter = (
            " AND resolution_status IN ('resolved','validated')"
            if "resolution_status" in integration_columns
            else ""
        )
        checks.append(
            (
                "CROSS_REPO_INTEGRATION",
                f"SELECT source_file_id,target_file_id,id,{relation_col},COALESCE({confidence_col},0.0),COALESCE({status_col},'resolved') FROM integration_links WHERE source_file_id IS NOT NULL AND target_file_id IS NOT NULL AND source_repo_id <> target_repo_id{status_filter} ORDER BY id",
                "MATCH (a:File)-[r:CROSS_REPO_INTEGRATION]->(b:File) RETURN a.file_id,b.file_id,r.integration_link_id,r.relation_type,r.confidence,r.resolution_status ORDER BY r.integration_link_id",
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
) -> str:
    sqlite_conn = None
    graph_db = None
    graph_conn = None

    try:
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
        graph_db = lb.Database(graph_path, read_only=True)
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
            (
                "DOCUMENTS_ENTITY",
                """
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT o.id, em.entity_id
                    FROM openapispec_index o
                    JOIN entity_mappings em ON em.file_id = o.file_id
                        AND em.entity_id IS NOT NULL
                        AND em.mapping_type LIKE 'openapispec_%'
                    WHERE o.x_mapped_to IS NOT NULL AND TRIM(o.x_mapped_to) <> ''
                )
                """,
                "MATCH ()-[r:DOCUMENTS_ENTITY]->() RETURN count(r)",
            ),
            (
                "POLICY_VALUE_GRANTS_OPERATION",
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT spv.id, so.id
                    FROM security_policy_values spv
                    JOIN security_policies sp ON sp.id = spv.policy_id
                    JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
                    JOIN security_operations so
                        ON so.repo_id = sp.repo_id AND so.op_key = spe.op_key
                    WHERE (sp.repo_id, spe.op_key) IN (
                        SELECT repo_id, op_key FROM security_operations
                        GROUP BY repo_id, op_key HAVING COUNT(*) = 1
                    )
                )
                """,
                "MATCH ()-[r:POLICY_VALUE_GRANTS_OPERATION]->() RETURN count(r)",
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

        starting_fingerprint = _sqlite_file_fingerprint(sqlite_path)
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

        ending_fingerprint = _sqlite_file_fingerprint(sqlite_path)
        if ending_fingerprint != starting_fingerprint:
            raise RuntimeError("SQLite snapshot changed during graph validation")
        summary = json.dumps(
            {
                "source_fingerprint": ending_fingerprint,
                "exact_check_count": len(parity_results),
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
    args = parser.parse_args()
    validate_paths(args.db, args.graph)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Validation failed: {exc}")
        sys.exit(1)
