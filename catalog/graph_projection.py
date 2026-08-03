"""Canonical SQLite-to-Ladybug projection registry.

The full projector remains optimized around bulk COPY statements, while delta
planning uses this registry to identify canonical source-record changes.  Every
projected family is declared here so full and delta eligibility share one
versioned contract.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

# v3 excludes archived repository nodes.  An archive generation must therefore
# be materialized through a new full graph before graph traversal resumes.
GRAPH_PROJECTION_VERSION = 3


@dataclass(frozen=True)
class Projection:
    ladybug_table: str
    kind: Literal["node", "relationship"]
    source_table: str
    stable_key: tuple[str | int, ...]
    source_query: str
    graph_query: str
    property_keys: tuple[str, ...]


def _node(
    label: str,
    source_table: str,
    *,
    source_query: str,
    graph_query: str,
    property_keys: tuple[str, ...],
) -> Projection:
    return Projection(
        label,
        "node",
        source_table,
        (0,),
        source_query=source_query,
        graph_query=graph_query,
        property_keys=property_keys,
    )


def _relationship(
    label: str,
    stable_key: tuple[int, ...],
    *,
    source_query: str,
    graph_query: str,
    property_keys: tuple[str, ...] = (),
) -> Projection:
    return Projection(
        label,
        "relationship",
        "",
        stable_key,
        source_query=source_query,
        graph_query=graph_query,
        property_keys=property_keys,
    )


NODE_PROJECTIONS: tuple[Projection, ...] = (
    _node(
        "Entity",
        "entity_nodes",
        source_query="SELECT id,name,entity_type FROM entity_nodes ORDER BY id",
        graph_query="MATCH (n:Entity) RETURN n.entity_id,n.name,n.entity_type ORDER BY n.entity_id",
        property_keys=("entity_id", "name", "entity_type"),
    ),
    _node(
        "EntityOccurrence",
        "entity_occurrences",
        source_query="SELECT id,repo_id,entity_id,ent_file,module,table_name,view_name,dummy,source_file_id,extractor,confidence,created_at,updated_at FROM entity_occurrences ORDER BY id",
        graph_query="MATCH (n:EntityOccurrence) RETURN n.entity_occurrence_id,n.repo_id,n.entity_id,n.ent_file,n.module,n.table_name,n.view_name,n.dummy,n.source_file_id,n.extractor,n.confidence,n.created_at,n.updated_at ORDER BY n.entity_occurrence_id",
        property_keys=(
            "entity_occurrence_id",
            "repo_id",
            "entity_id",
            "ent_file",
            "module",
            "table_name",
            "view_name",
            "dummy",
            "source_file_id",
            "extractor",
            "confidence",
            "created_at",
            "updated_at",
        ),
    ),
    _node(
        "Symbol",
        "symbols",
        source_query="SELECT id,name,kind,start_line,end_line,signature FROM symbols ORDER BY id",
        graph_query="MATCH (n:Symbol) RETURN n.symbol_id,n.name,n.kind,n.start_line,n.end_line,n.signature ORDER BY n.symbol_id",
        property_keys=(
            "symbol_id",
            "name",
            "kind",
            "start_line",
            "end_line",
            "signature",
        ),
    ),
    _node(
        "Repository",
        "repos",
        source_query="SELECT id,repo_key,tracked_branch,indexed_commit_sha,COALESCE(last_built_at,last_scanned_at),index_status FROM repos WHERE lifecycle_state='active' ORDER BY id",
        graph_query="MATCH (n:Repository) RETURN n.repo_id,n.repo_key,n.tracked_branch,n.indexed_commit_sha,n.last_indexed_at,n.index_status ORDER BY n.repo_id",
        property_keys=(
            "repo_id",
            "repo_key",
            "tracked_branch",
            "indexed_commit_sha",
            "last_indexed_at",
            "index_status",
        ),
    ),
    _node(
        "File",
        "files",
        source_query="SELECT f.id,f.repo_id,r.repo_key,f.path,f.language FROM files f JOIN repos r ON r.id=f.repo_id ORDER BY f.id",
        graph_query="MATCH (n:File) RETURN n.file_id,n.repo_id,n.repo_key,n.path,n.language ORDER BY n.file_id",
        property_keys=("file_id", "repo_id", "repo_key", "path", "language"),
    ),
    _node(
        "Workflow",
        "workflows",
        source_query="SELECT id,name,workflow_type FROM workflows ORDER BY id",
        graph_query="MATCH (n:Workflow) RETURN n.workflow_id,n.name,n.workflow_type ORDER BY n.workflow_id",
        property_keys=("workflow_id", "name", "workflow_type"),
    ),
    _node(
        "RestEndpoint",
        "rest_endpoints",
        source_query="SELECT id,method,path FROM rest_endpoints ORDER BY id",
        graph_query="MATCH (n:RestEndpoint) RETURN n.rest_endpoint_id,n.method,n.path ORDER BY n.rest_endpoint_id",
        property_keys=("rest_endpoint_id", "method", "path"),
    ),
    _node(
        "WorkflowNode",
        "workflow_nodes",
        source_query="SELECT id,workflow_id,entity_id,node_kind,node_key,name,ordinal,action,source_kind,file_id,symbol_id,metadata_json,created_at FROM workflow_nodes ORDER BY id",
        graph_query="MATCH (n:WorkflowNode) RETURN n.workflow_node_id,n.workflow_id,n.entity_id,n.node_kind,n.node_key,n.name,n.ordinal,n.action,n.source_kind,n.file_id,n.symbol_id,n.metadata_json,n.created_at ORDER BY n.workflow_node_id",
        property_keys=(
            "workflow_node_id",
            "workflow_id",
            "entity_id",
            "node_kind",
            "node_key",
            "name",
            "ordinal",
            "action",
            "source_kind",
            "file_id",
            "symbol_id",
            "metadata_json",
            "created_at",
        ),
    ),
    _node(
        "OpenApiSpec",
        "openapispec_index",
        source_query="SELECT id,file_id,file_path,module,slug,version,kind,canonical_name,resource_path,x_mapped_to,title,state,last_seen_at FROM openapispec_index ORDER BY id",
        graph_query="MATCH (n:OpenApiSpec) RETURN n.openapi_id,n.file_id,n.file_path,n.module,n.slug,n.version,n.kind,n.canonical_name,n.resource_path,n.x_mapped_to,n.title,n.state,n.last_seen_at ORDER BY n.openapi_id",
        property_keys=(
            "openapi_id",
            "file_id",
            "file_path",
            "module",
            "slug",
            "version",
            "kind",
            "canonical_name",
            "resource_path",
            "x_mapped_to",
            "title",
            "state",
            "last_seen_at",
        ),
    ),
    _node(
        "SecurityOperation",
        "security_operations",
        source_query="SELECT id,op_key,op_numeric_id,title,action,script,force_mode,secure_only,allow_dev_env_only,source_file,file_id,source_line,source_kind,raw_hash FROM security_operations ORDER BY id",
        graph_query="MATCH (n:SecurityOperation) RETURN n.security_operation_id,n.op_key,n.op_numeric_id,n.title,n.action,n.script,n.force_mode,n.secure_only,n.allow_dev_env_only,n.source_file,n.file_id,n.source_line,n.source_kind,n.raw_hash ORDER BY n.security_operation_id",
        property_keys=(
            "security_operation_id",
            "op_key",
            "op_numeric_id",
            "title",
            "action",
            "script",
            "force_mode",
            "secure_only",
            "allow_dev_env_only",
            "source_file",
            "file_id",
            "source_line",
            "source_kind",
            "raw_hash",
        ),
    ),
    _node(
        "SecurityPolicy",
        "security_policies",
        source_query="SELECT id,policy_name,module,label,source_file,file_id,source_line FROM security_policies ORDER BY id",
        graph_query="MATCH (n:SecurityPolicy) RETURN n.security_policy_id,n.policy_name,n.module,n.label,n.source_file,n.file_id,n.source_line ORDER BY n.security_policy_id",
        property_keys=(
            "security_policy_id",
            "policy_name",
            "module",
            "label",
            "source_file",
            "file_id",
            "source_line",
        ),
    ),
    _node(
        "SecurityPolicyValue",
        "security_policy_values",
        source_query="SELECT id,policy_id,value_key,display,value_label,source_line FROM security_policy_values ORDER BY id",
        graph_query="MATCH (n:SecurityPolicyValue) RETURN n.security_policy_value_id,n.policy_id,n.value_key,n.display,n.value_label,n.source_line ORDER BY n.security_policy_value_id",
        property_keys=(
            "security_policy_value_id",
            "policy_id",
            "value_key",
            "display",
            "value_label",
            "source_line",
        ),
    ),
    _node(
        "SecurityMenu",
        "security_menus",
        source_query="SELECT id,module,menu_name,source_file,file_id FROM security_menus ORDER BY id",
        graph_query="MATCH (n:SecurityMenu) RETURN n.security_menu_id,n.module,n.menu_name,n.source_file,n.file_id ORDER BY n.security_menu_id",
        property_keys=(
            "security_menu_id",
            "module",
            "menu_name",
            "source_file",
            "file_id",
        ),
    ),
    _node(
        "SecurityMenuItem",
        "security_menu_items",
        source_query="SELECT id,menu_id,item_path,item_name,menu_item_id,menu_script,menu_key,source_line FROM security_menu_items ORDER BY id",
        graph_query="MATCH (n:SecurityMenuItem) RETURN n.security_menu_item_id,n.menu_id,n.item_path,n.item_name,n.menu_item_id,n.menu_script,n.menu_key,n.source_line ORDER BY n.security_menu_item_id",
        property_keys=(
            "security_menu_item_id",
            "menu_id",
            "item_path",
            "item_name",
            "menu_item_id",
            "menu_script",
            "menu_key",
            "source_line",
        ),
    ),
    _node(
        "DbTable",
        "dbschema_tables",
        source_query="SELECT id,table_name,primary_keys,source_file,file_id,source_line,raw_hash FROM dbschema_tables ORDER BY id",
        graph_query="MATCH (n:DbTable) RETURN n.dbschema_table_id,n.table_name,n.primary_keys,n.source_file,n.file_id,n.source_line,n.raw_hash ORDER BY n.dbschema_table_id",
        property_keys=(
            "dbschema_table_id",
            "table_name",
            "primary_keys",
            "source_file",
            "file_id",
            "source_line",
            "raw_hash",
        ),
    ),
    _node(
        "DbField",
        "dbschema_fields",
        source_query="SELECT id,dbschema_table_id,field_name,field_type,source_line FROM dbschema_fields ORDER BY id",
        graph_query="MATCH (n:DbField) RETURN n.dbschema_field_id,n.dbschema_table_id,n.field_name,n.field_type,n.source_line ORDER BY n.dbschema_field_id",
        property_keys=(
            "dbschema_field_id",
            "dbschema_table_id",
            "field_name",
            "field_type",
            "source_line",
        ),
    ),
    _node(
        "EntityAccessLink",
        "entity_access_links",
        source_query="SELECT id,entity_id,surface,record_id,link_type,evidence_file_id,evidence_symbol_id,confidence_mode,notes,created_at FROM entity_access_links ORDER BY id",
        graph_query="MATCH (n:EntityAccessLink) RETURN n.entity_access_link_id,n.entity_id,n.surface,n.record_id,n.link_type,n.evidence_file_id,n.evidence_symbol_id,n.confidence_mode,n.notes,n.created_at ORDER BY n.entity_access_link_id",
        property_keys=(
            "entity_access_link_id",
            "entity_id",
            "surface",
            "record_id",
            "link_type",
            "evidence_file_id",
            "evidence_symbol_id",
            "confidence_mode",
            "notes",
            "created_at",
        ),
    ),
    _node(
        "EntitySchemaComponent",
        "entity_schema_components",
        source_query="SELECT id,occurrence_id,component_kind,component_path,declared_name,target_literal,data_type,cardinality,writeability,confidence FROM entity_schema_components ORDER BY id",
        graph_query="MATCH (n:EntitySchemaComponent) RETURN n.entity_schema_component_id,n.occurrence_id,n.component_kind,n.component_path,n.declared_name,n.target_literal,n.data_type,n.cardinality,n.writeability,n.confidence ORDER BY n.entity_schema_component_id",
        property_keys=(
            "entity_schema_component_id",
            "occurrence_id",
            "component_kind",
            "component_path",
            "declared_name",
            "target_literal",
            "data_type",
            "cardinality",
            "writeability",
            "confidence",
        ),
    ),
    _node(
        "EntityRelationshipFact",
        "entity_relationship_facts",
        source_query="SELECT id,source_occurrence_id,target_occurrence_id,axis,relation_kind,fact_key,assertion_status,target_entity_name,target_literal,cardinality,confidence,source_path,start_line,end_line FROM entity_relationship_facts ORDER BY id",
        graph_query="MATCH (n:EntityRelationshipFact) RETURN n.entity_relationship_fact_id,n.source_occurrence_id,n.target_occurrence_id,n.axis,n.relation_kind,n.fact_key,n.assertion_status,n.target_entity_name,n.target_literal,n.cardinality,n.confidence,n.source_path,n.start_line,n.end_line ORDER BY n.entity_relationship_fact_id",
        property_keys=(
            "entity_relationship_fact_id",
            "source_occurrence_id",
            "target_occurrence_id",
            "axis",
            "relation_kind",
            "fact_key",
            "assertion_status",
            "target_entity_name",
            "target_literal",
            "cardinality",
            "confidence",
            "source_path",
            "start_line",
            "end_line",
        ),
    ),
    _node(
        "EntityOperationFact",
        "entity_operation_facts",
        source_query="SELECT id,occurrence_id,axis,operation,surface_kind,availability,invocation_context,persistence_scope,standalone,confidence FROM entity_operation_facts ORDER BY id",
        graph_query="MATCH (n:EntityOperationFact) RETURN n.entity_operation_fact_id,n.occurrence_id,n.axis,n.operation,n.surface_kind,n.availability,n.invocation_context,n.persistence_scope,n.standalone,n.confidence ORDER BY n.entity_operation_fact_id",
        property_keys=(
            "entity_operation_fact_id",
            "occurrence_id",
            "axis",
            "operation",
            "surface_kind",
            "availability",
            "invocation_context",
            "persistence_scope",
            "standalone",
            "confidence",
        ),
    ),
)

RELATIONSHIP_PROJECTIONS: tuple[Projection, ...] = (
    _relationship(
        "ENTITY_ROOT",
        (0, 1, 2),
        source_query="SELECT entity_id,symbol_id,role,weight FROM entity_roots ORDER BY entity_id,symbol_id,role,weight",
        graph_query="MATCH (a:Entity)-[r:ENTITY_ROOT]->(b:Symbol) RETURN a.entity_id,b.symbol_id,r.role,r.weight ORDER BY a.entity_id,b.symbol_id,r.role,r.weight",
        property_keys=("role", "weight"),
    ),
    _relationship(
        "ENTITY_MAPPING",
        (0, 1, 2),
        source_query="SELECT entity_id,symbol_id,mapping_type,confidence FROM entity_mappings WHERE symbol_id IS NOT NULL ORDER BY entity_id,symbol_id,mapping_type,confidence",
        graph_query="MATCH (a:Entity)-[r:ENTITY_MAPPING]->(b:Symbol) RETURN a.entity_id,b.symbol_id,r.mapping_type,r.confidence ORDER BY a.entity_id,b.symbol_id,r.mapping_type,r.confidence",
        property_keys=("mapping_type", "confidence"),
    ),
    _relationship(
        "REPOSITORY_HAS_ENTITY_OCCURRENCE",
        (0, 1),
        source_query="SELECT repo_id,id FROM entity_occurrences ORDER BY repo_id,id",
        graph_query="MATCH (a:Repository)-[:REPOSITORY_HAS_ENTITY_OCCURRENCE]->(b:EntityOccurrence) RETURN a.repo_id,b.entity_occurrence_id ORDER BY a.repo_id,b.entity_occurrence_id",
    ),
    _relationship(
        "ENTITY_HAS_OCCURRENCE",
        (0, 1),
        source_query="SELECT entity_id,id FROM entity_occurrences ORDER BY entity_id,id",
        graph_query="MATCH (a:Entity)-[:ENTITY_HAS_OCCURRENCE]->(b:EntityOccurrence) RETURN a.entity_id,b.entity_occurrence_id ORDER BY a.entity_id,b.entity_occurrence_id",
    ),
    _relationship(
        "ENTITY_OCCURRENCE_FILE",
        (0, 1),
        source_query="SELECT id,source_file_id FROM entity_occurrences WHERE source_file_id IS NOT NULL ORDER BY id",
        graph_query="MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_FILE]->(b:File) RETURN a.entity_occurrence_id,b.file_id ORDER BY a.entity_occurrence_id,b.file_id",
    ),
    _relationship(
        "ENTITY_OCCURRENCE_ROOT",
        (0, 1, 2),
        source_query="SELECT eo.id,er.symbol_id,er.role,er.weight FROM entity_roots er JOIN entity_occurrences eo ON eo.repo_id=er.repo_id AND eo.entity_id=er.entity_id ORDER BY eo.id,er.symbol_id,er.role,er.weight",
        graph_query="MATCH (a:EntityOccurrence)-[r:ENTITY_OCCURRENCE_ROOT]->(b:Symbol) RETURN a.entity_occurrence_id,b.symbol_id,r.role,r.weight ORDER BY a.entity_occurrence_id,b.symbol_id,r.role,r.weight",
        property_keys=("role", "weight"),
    ),
    _relationship(
        "ENTITY_OCCURRENCE_MAPPING",
        (0, 1, 2),
        source_query="SELECT eo.id,em.symbol_id,em.mapping_type,em.confidence FROM entity_mappings em JOIN entity_occurrences eo ON eo.repo_id=em.repo_id AND eo.entity_id=em.entity_id WHERE em.symbol_id IS NOT NULL ORDER BY eo.id,em.symbol_id,em.mapping_type,em.confidence",
        graph_query="MATCH (a:EntityOccurrence)-[r:ENTITY_OCCURRENCE_MAPPING]->(b:Symbol) RETURN a.entity_occurrence_id,b.symbol_id,r.mapping_type,r.confidence ORDER BY a.entity_occurrence_id,b.symbol_id,r.mapping_type,r.confidence",
        property_keys=("mapping_type", "confidence"),
    ),
    _relationship(
        "ENTITY_OCCURRENCE_WORKFLOW",
        (0, 1),
        source_query="SELECT eo.id,w.id FROM workflows w JOIN entity_occurrences eo ON eo.repo_id=w.repo_id AND eo.entity_id=w.entity_id WHERE w.entity_id IS NOT NULL ORDER BY eo.id,w.id",
        graph_query="MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_WORKFLOW]->(b:Workflow) RETURN a.entity_occurrence_id,b.workflow_id ORDER BY a.entity_occurrence_id,b.workflow_id",
    ),
    _relationship(
        "ENTITY_OCCURRENCE_REST_ENDPOINT",
        (0, 1),
        source_query="SELECT eo.id,ep.id FROM rest_endpoints ep JOIN entity_occurrences eo ON eo.repo_id=ep.repo_id AND eo.entity_id=ep.entity_id WHERE ep.entity_id IS NOT NULL ORDER BY eo.id,ep.id",
        graph_query="MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_REST_ENDPOINT]->(b:RestEndpoint) RETURN a.entity_occurrence_id,b.rest_endpoint_id ORDER BY a.entity_occurrence_id,b.rest_endpoint_id",
    ),
    _relationship(
        "ENTITY_OCCURRENCE_HAS_COMPONENT",
        (0, 1),
        source_query="SELECT occurrence_id,id FROM entity_schema_components ORDER BY occurrence_id,id",
        graph_query="MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_HAS_COMPONENT]->(b:EntitySchemaComponent) RETURN a.entity_occurrence_id,b.entity_schema_component_id ORDER BY a.entity_occurrence_id,b.entity_schema_component_id",
    ),
    _relationship(
        "ENTITY_OCCURRENCE_HAS_SEMANTIC_FACT",
        (0, 1),
        source_query="SELECT source_occurrence_id,id FROM entity_relationship_facts ORDER BY source_occurrence_id,id",
        graph_query="MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_HAS_SEMANTIC_FACT]->(b:EntityRelationshipFact) RETURN a.entity_occurrence_id,b.entity_relationship_fact_id ORDER BY a.entity_occurrence_id,b.entity_relationship_fact_id",
    ),
    _relationship(
        "SEMANTIC_FACT_TARGET_OCCURRENCE",
        (0, 1),
        source_query="SELECT id,target_occurrence_id FROM entity_relationship_facts WHERE target_occurrence_id IS NOT NULL AND assertion_status IN ('VERIFIED','CORROBORATED') ORDER BY id,target_occurrence_id",
        graph_query="MATCH (a:EntityRelationshipFact)-[:SEMANTIC_FACT_TARGET_OCCURRENCE]->(b:EntityOccurrence) RETURN a.entity_relationship_fact_id,b.entity_occurrence_id ORDER BY a.entity_relationship_fact_id,b.entity_occurrence_id",
    ),
    _relationship(
        "ENTITY_OCCURRENCE_HAS_OPERATION_FACT",
        (0, 1),
        source_query="SELECT occurrence_id,id FROM entity_operation_facts ORDER BY occurrence_id,id",
        graph_query="MATCH (a:EntityOccurrence)-[:ENTITY_OCCURRENCE_HAS_OPERATION_FACT]->(b:EntityOperationFact) RETURN a.entity_occurrence_id,b.entity_operation_fact_id ORDER BY a.entity_occurrence_id,b.entity_operation_fact_id",
    ),
    _relationship(
        "INHERITS",
        (0, 1),
        source_query="SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='INHERITS' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
        graph_query="MATCH (a:Symbol)-[:INHERITS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
    ),
    _relationship(
        "IMPLEMENTS",
        (0, 1),
        source_query="SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='IMPLEMENTS' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
        graph_query="MATCH (a:Symbol)-[:IMPLEMENTS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
    ),
    _relationship(
        "IMPORTS",
        (0, 1),
        source_query="SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='IMPORTS' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
        graph_query="MATCH (a:Symbol)-[:IMPORTS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
    ),
    _relationship(
        "USES",
        (0, 1),
        source_query="SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='USES' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
        graph_query="MATCH (a:Symbol)-[:USES]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
    ),
    _relationship(
        "REFERENCES",
        (0, 1),
        source_query="SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type='REFERENCES' AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
        graph_query="MATCH (a:Symbol)-[:REFERENCES]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
    ),
    _relationship(
        "CALLS",
        (0, 1),
        source_query="SELECT source_symbol_id,target_symbol_id FROM relationships WHERE relationship_type IN ('CALLS','STATIC_CALLS') AND source_symbol_id IS NOT NULL AND target_symbol_id IS NOT NULL ORDER BY source_symbol_id,target_symbol_id",
        graph_query="MATCH (a:Symbol)-[:CALLS]->(b:Symbol) RETURN a.symbol_id,b.symbol_id ORDER BY a.symbol_id,b.symbol_id",
    ),
    _relationship(
        "DECLARED_IN",
        (0, 1),
        source_query="SELECT id,file_id FROM symbols ORDER BY id,file_id",
        graph_query="MATCH (a:Symbol)-[:DECLARED_IN]->(b:File) RETURN a.symbol_id,b.file_id ORDER BY a.symbol_id,b.file_id",
    ),
    _relationship(
        "REPOSITORY_CONTAINS_FILE",
        (0, 1),
        source_query="SELECT repo_id,id FROM files ORDER BY repo_id,id",
        graph_query="MATCH (a:Repository)-[:REPOSITORY_CONTAINS_FILE]->(b:File) RETURN a.repo_id,b.file_id ORDER BY a.repo_id,b.file_id",
    ),
    _relationship(
        "HAS_WORKFLOW",
        (0, 1),
        source_query="SELECT entity_id,id FROM workflows WHERE entity_id IS NOT NULL ORDER BY entity_id,id",
        graph_query="MATCH (a:Entity)-[:HAS_WORKFLOW]->(b:Workflow) RETURN a.entity_id,b.workflow_id ORDER BY a.entity_id,b.workflow_id",
    ),
    _relationship(
        "EXPOSES_ENTITY",
        (0, 1),
        source_query="SELECT id,entity_id FROM rest_endpoints WHERE entity_id IS NOT NULL ORDER BY id,entity_id",
        graph_query="MATCH (a:RestEndpoint)-[:EXPOSES_ENTITY]->(b:Entity) RETURN a.rest_endpoint_id,b.entity_id ORDER BY a.rest_endpoint_id,b.entity_id",
    ),
    _relationship(
        "HANDLED_BY",
        (0, 1),
        source_query="SELECT id,handler_symbol_id FROM rest_endpoints WHERE handler_symbol_id IS NOT NULL ORDER BY id,handler_symbol_id",
        graph_query="MATCH (a:RestEndpoint)-[:HANDLED_BY]->(b:Symbol) RETURN a.rest_endpoint_id,b.symbol_id ORDER BY a.rest_endpoint_id,b.symbol_id",
    ),
    _relationship(
        "WORKFLOW_HAS_NODE",
        (0, 1),
        source_query="SELECT workflow_id,id FROM workflow_nodes ORDER BY workflow_id,id",
        graph_query="MATCH (a:Workflow)-[:WORKFLOW_HAS_NODE]->(b:WorkflowNode) RETURN a.workflow_id,b.workflow_node_id ORDER BY a.workflow_id,b.workflow_node_id",
    ),
    _relationship(
        "WORKFLOW_NODE_FILE",
        (0, 1),
        source_query="SELECT id,file_id FROM workflow_nodes WHERE file_id IS NOT NULL ORDER BY id,file_id",
        graph_query="MATCH (a:WorkflowNode)-[:WORKFLOW_NODE_FILE]->(b:File) RETURN a.workflow_node_id,b.file_id ORDER BY a.workflow_node_id,b.file_id",
    ),
    _relationship(
        "WORKFLOW_NODE_SYMBOL",
        (0, 1),
        source_query="SELECT id,symbol_id FROM workflow_nodes WHERE symbol_id IS NOT NULL ORDER BY id,symbol_id",
        graph_query="MATCH (a:WorkflowNode)-[:WORKFLOW_NODE_SYMBOL]->(b:Symbol) RETURN a.workflow_node_id,b.symbol_id ORDER BY a.workflow_node_id,b.symbol_id",
    ),
    _relationship(
        "WORKFLOW_NODE_EDGE",
        (0, 1, 2, 3, 4),
        source_query="SELECT from_node_id,to_node_id,workflow_id,COALESCE(edge_kind,''),COALESCE(ordinal,-1),COALESCE(evidence,''),COALESCE(confidence,0.0),file_id,symbol_id FROM workflow_edges ORDER BY from_node_id,to_node_id,workflow_id,COALESCE(edge_kind,''),COALESCE(ordinal,-1),COALESCE(evidence,''),COALESCE(confidence,0.0),file_id,symbol_id",
        graph_query="MATCH (a:WorkflowNode)-[r:WORKFLOW_NODE_EDGE]->(b:WorkflowNode) RETURN a.workflow_node_id,b.workflow_node_id,r.workflow_id,r.edge_kind,r.ordinal,r.evidence,r.confidence,r.file_id,r.symbol_id ORDER BY a.workflow_node_id,b.workflow_node_id,r.workflow_id,r.edge_kind,r.ordinal,r.evidence,r.confidence,r.file_id,r.symbol_id",
        property_keys=(
            "workflow_id",
            "edge_kind",
            "ordinal",
            "evidence",
            "confidence",
            "file_id",
            "symbol_id",
        ),
    ),
    _relationship(
        "OPENAPI_SPEC_FILE",
        (0, 1),
        source_query="SELECT id,file_id FROM openapispec_index WHERE file_id IS NOT NULL ORDER BY id,file_id",
        graph_query="MATCH (a:OpenApiSpec)-[:OPENAPI_SPEC_FILE]->(b:File) RETURN a.openapi_id,b.file_id ORDER BY a.openapi_id,b.file_id",
    ),
    _relationship(
        "OPENAPI_FILE_REF",
        (0, 1, 2, 3),
        source_query="SELECT source_file_id,target_file_id,COALESCE(ref_value,''),COALESCE(ref_path,''),COALESCE(confidence,0.0) FROM openapi_file_ref_edges ORDER BY source_file_id,target_file_id,COALESCE(ref_value,''),COALESCE(ref_path,''),COALESCE(confidence,0.0)",
        graph_query="MATCH (a:File)-[r:OPENAPI_FILE_REF]->(b:File) RETURN a.file_id,b.file_id,r.ref_value,r.ref_path,r.confidence ORDER BY a.file_id,b.file_id,r.ref_value,r.ref_path,r.confidence",
        property_keys=("ref_value", "ref_path", "confidence"),
    ),
    _relationship(
        "SECURITY_OPERATION_FILE",
        (0, 1),
        source_query="SELECT id,file_id FROM security_operations WHERE file_id IS NOT NULL ORDER BY id,file_id",
        graph_query="MATCH (a:SecurityOperation)-[:SECURITY_OPERATION_FILE]->(b:File) RETURN a.security_operation_id,b.file_id ORDER BY a.security_operation_id,b.file_id",
    ),
    _relationship(
        "SECURITY_POLICY_FILE",
        (0, 1),
        source_query="SELECT id,file_id FROM security_policies WHERE file_id IS NOT NULL ORDER BY id,file_id",
        graph_query="MATCH (a:SecurityPolicy)-[:SECURITY_POLICY_FILE]->(b:File) RETURN a.security_policy_id,b.file_id ORDER BY a.security_policy_id,b.file_id",
    ),
    _relationship(
        "SECURITY_POLICY_HAS_VALUE",
        (0, 1),
        source_query="SELECT policy_id,id FROM security_policy_values ORDER BY policy_id,id",
        graph_query="MATCH (a:SecurityPolicy)-[:SECURITY_POLICY_HAS_VALUE]->(b:SecurityPolicyValue) RETURN a.security_policy_id,b.security_policy_value_id ORDER BY a.security_policy_id,b.security_policy_value_id",
    ),
    _relationship(
        "SECURITY_MENU_FILE",
        (0, 1),
        source_query="SELECT id,file_id FROM security_menus WHERE file_id IS NOT NULL ORDER BY id,file_id",
        graph_query="MATCH (a:SecurityMenu)-[:SECURITY_MENU_FILE]->(b:File) RETURN a.security_menu_id,b.file_id ORDER BY a.security_menu_id,b.file_id",
    ),
    _relationship(
        "SECURITY_MENU_HAS_ITEM",
        (0, 1),
        source_query="SELECT menu_id,id FROM security_menu_items ORDER BY menu_id,id",
        graph_query="MATCH (a:SecurityMenu)-[:SECURITY_MENU_HAS_ITEM]->(b:SecurityMenuItem) RETURN a.security_menu_id,b.security_menu_item_id ORDER BY a.security_menu_id,b.security_menu_item_id",
    ),
    _relationship(
        "SECURITY_MENU_ITEM_TO_OPERATION",
        (0, 1, 2),
        source_query="SELECT menu_item_id,operation_id,COALESCE(op_key,''),COALESCE(resolution_reason,'') FROM security_menu_op_links WHERE operation_id IS NOT NULL ORDER BY menu_item_id,operation_id,COALESCE(op_key,''),COALESCE(resolution_reason,'')",
        graph_query="MATCH (a:SecurityMenuItem)-[r:SECURITY_MENU_ITEM_TO_OPERATION]->(b:SecurityOperation) RETURN a.security_menu_item_id,b.security_operation_id,r.op_key,r.resolution_reason ORDER BY a.security_menu_item_id,b.security_operation_id,r.op_key,r.resolution_reason",
        property_keys=("op_key", "resolution_reason"),
    ),
    _relationship(
        "DBTABLE_FILE",
        (0, 1),
        source_query="SELECT id,file_id FROM dbschema_tables WHERE file_id IS NOT NULL ORDER BY id,file_id",
        graph_query="MATCH (a:DbTable)-[:DBTABLE_FILE]->(b:File) RETURN a.dbschema_table_id,b.file_id ORDER BY a.dbschema_table_id,b.file_id",
    ),
    _relationship(
        "DBTABLE_HAS_FIELD",
        (0, 1),
        source_query="SELECT dbschema_table_id,id FROM dbschema_fields ORDER BY dbschema_table_id,id",
        graph_query="MATCH (a:DbTable)-[:DBTABLE_HAS_FIELD]->(b:DbField) RETURN a.dbschema_table_id,b.dbschema_field_id ORDER BY a.dbschema_table_id,b.dbschema_field_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_ENTITY",
        (0, 1),
        source_query="SELECT id,entity_id FROM entity_access_links ORDER BY id,entity_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_ENTITY]->(b:Entity) RETURN a.entity_access_link_id,b.entity_id ORDER BY a.entity_access_link_id,b.entity_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_FILE",
        (0, 1),
        source_query="SELECT id,evidence_file_id FROM entity_access_links WHERE evidence_file_id IS NOT NULL ORDER BY id,evidence_file_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_FILE]->(b:File) RETURN a.entity_access_link_id,b.file_id ORDER BY a.entity_access_link_id,b.file_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_SYMBOL",
        (0, 1),
        source_query="SELECT id,evidence_symbol_id FROM entity_access_links WHERE evidence_symbol_id IS NOT NULL ORDER BY id,evidence_symbol_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SYMBOL]->(b:Symbol) RETURN a.entity_access_link_id,b.symbol_id ORDER BY a.entity_access_link_id,b.symbol_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_WORKFLOW",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='workflow' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_WORKFLOW]->(b:Workflow) RETURN a.entity_access_link_id,b.workflow_id ORDER BY a.entity_access_link_id,b.workflow_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_REST_ENDPOINT",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='rest_endpoint' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_REST_ENDPOINT]->(b:RestEndpoint) RETURN a.entity_access_link_id,b.rest_endpoint_id ORDER BY a.entity_access_link_id,b.rest_endpoint_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_SECURITY_OPERATION",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='security_operation' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->(b:SecurityOperation) RETURN a.entity_access_link_id,b.security_operation_id ORDER BY a.entity_access_link_id,b.security_operation_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_SECURITY_RESOURCE",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='security_resource' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_RESOURCE]->(b:SecurityOperation) RETURN a.entity_access_link_id,b.security_operation_id ORDER BY a.entity_access_link_id,b.security_operation_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_SECURITY_POLICY",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='security_policy' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_POLICY]->(b:SecurityPolicy) RETURN a.entity_access_link_id,b.security_policy_id ORDER BY a.entity_access_link_id,b.security_policy_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_SECURITY_MENU",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='security_menu' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_MENU]->(b:SecurityMenu) RETURN a.entity_access_link_id,b.security_menu_id ORDER BY a.entity_access_link_id,b.security_menu_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='security_menu_item' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_MENU_ITEM]->(b:SecurityMenuItem) RETURN a.entity_access_link_id,b.security_menu_item_id ORDER BY a.entity_access_link_id,b.security_menu_item_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_ENTITY_OCCURRENCE",
        (0, 1),
        source_query="SELECT eal.id,eo.id FROM entity_access_links eal JOIN entity_occurrences eo ON eo.repo_id=eal.repo_id AND eo.entity_id=eal.entity_id ORDER BY eal.id,eo.id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_ENTITY_OCCURRENCE]->(b:EntityOccurrence) RETURN a.entity_access_link_id,b.entity_occurrence_id ORDER BY a.entity_access_link_id,b.entity_occurrence_id",
    ),
    _relationship(
        "ENTITY_ACCESS_LINK_DBTABLE",
        (0, 1),
        source_query="SELECT id,record_id FROM entity_access_links WHERE surface='dbschema_table' ORDER BY id,record_id",
        graph_query="MATCH (a:EntityAccessLink)-[:ENTITY_ACCESS_LINK_DBTABLE]->(b:DbTable) RETURN a.entity_access_link_id,b.dbschema_table_id ORDER BY a.entity_access_link_id,b.dbschema_table_id",
    ),
    _relationship(
        "DOCUMENTS_ENTITY",
        (0, 1),
        source_query="SELECT DISTINCT o.id,em.entity_id FROM openapispec_index o JOIN entity_mappings em ON em.file_id=o.file_id AND em.entity_id IS NOT NULL AND em.mapping_type LIKE 'openapispec_%' WHERE o.x_mapped_to IS NOT NULL AND TRIM(o.x_mapped_to)<>'' ORDER BY o.id,em.entity_id",
        graph_query="MATCH (a:OpenApiSpec)-[:DOCUMENTS_ENTITY]->(b:Entity) RETURN a.openapi_id,b.entity_id ORDER BY a.openapi_id,b.entity_id",
    ),
    _relationship(
        "POLICY_VALUE_GRANTS_OPERATION",
        (0, 1),
        source_query="SELECT DISTINCT spv.id,so.id FROM security_policy_values spv JOIN security_policies sp ON sp.id=spv.policy_id JOIN security_policy_eops spe ON spe.policy_value_id=spv.id JOIN security_operations so ON so.repo_id=sp.repo_id AND so.op_key=spe.op_key WHERE (sp.repo_id,spe.op_key) IN (SELECT repo_id,op_key FROM security_operations GROUP BY repo_id,op_key HAVING COUNT(*)=1) ORDER BY spv.id,so.id",
        graph_query="MATCH (a:SecurityPolicyValue)-[:POLICY_VALUE_GRANTS_OPERATION]->(b:SecurityOperation) RETURN a.security_policy_value_id,b.security_operation_id ORDER BY a.security_policy_value_id,b.security_operation_id",
    ),
    _relationship(
        "ALLOWS_SECURITY_OPERATION",
        (0, 1, 2),
        source_query="SELECT operation_id,allowed_operation_id,COALESCE(allowed_op_key,''),COALESCE(resolution_reason,'') FROM security_operation_allowops WHERE allowed_operation_id IS NOT NULL ORDER BY operation_id,allowed_operation_id,id",
        graph_query="MATCH (a:SecurityOperation)-[r:ALLOWS_SECURITY_OPERATION]->(b:SecurityOperation) RETURN a.security_operation_id,b.security_operation_id,r.allowed_op_key,r.resolution_reason ORDER BY a.security_operation_id,b.security_operation_id,r.allowed_op_key,r.resolution_reason",
        property_keys=("allowed_op_key", "resolution_reason"),
    ),
    _relationship(
        "CROSS_REPO_INTEGRATION",
        (2,),
        source_query="SELECT source_file_id,target_file_id,id,relation_type,COALESCE(confidence,0.0),COALESCE(resolution_status,'resolved') FROM integration_links WHERE source_file_id IS NOT NULL AND target_file_id IS NOT NULL AND source_repo_id <> target_repo_id AND resolution_status IN ('resolved','validated') ORDER BY id",
        graph_query="MATCH (a:File)-[r:CROSS_REPO_INTEGRATION]->(b:File) RETURN a.file_id,b.file_id,r.integration_link_id,r.relation_type,r.confidence,r.resolution_status ORDER BY r.integration_link_id",
        property_keys=(
            "integration_link_id",
            "relation_type",
            "confidence",
            "resolution_status",
        ),
    ),
)

PROJECTIONS: tuple[Projection, ...] = NODE_PROJECTIONS + RELATIONSHIP_PROJECTIONS


def iter_available_projections(
    conn: sqlite3.Connection, projections: tuple[Projection, ...] = PROJECTIONS
) -> Iterator[Projection]:
    """Yield projections supported by the SQLite snapshot, failing on bad SQL."""

    for projection in projections:
        try:
            conn.execute(
                f"SELECT * FROM ({projection.source_query}) AS projection LIMIT 0"
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                continue
            raise
        yield projection


def iter_available_node_projections(
    conn: sqlite3.Connection,
) -> Iterator[Projection]:
    return iter_available_projections(conn, NODE_PROJECTIONS)


def iter_available_relationship_projections(
    conn: sqlite3.Connection,
) -> Iterator[Projection]:
    return iter_available_projections(conn, RELATIONSHIP_PROJECTIONS)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, float):
        return {"float": value.hex()}
    return value


def canonical_projection_rows(
    conn: sqlite3.Connection, projection: Projection
) -> dict[str, str]:
    try:
        cursor = conn.execute(projection.source_query)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {}
        raise
    key_indexes = [int(column) for column in projection.stable_key]
    result: dict[str, str] = {}
    grouped: dict[str, list[str]] = {}
    for row in cursor:
        values = [_canonical_value(value) for value in row]
        key = json.dumps(
            [values[index] for index in key_indexes],
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
        grouped.setdefault(key, []).append(payload)
    for key, payloads in grouped.items():
        for ordinal, payload in enumerate(sorted(payloads)):
            result[key if ordinal == 0 else f"{key}#{ordinal}"] = payload
    return result


def projection_diff(
    previous: sqlite3.Connection, current: sqlite3.Connection
) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for projection in PROJECTIONS:
        old = canonical_projection_rows(previous, projection)
        new = canonical_projection_rows(current, projection)
        old_keys = set(old)
        new_keys = set(new)
        summary[projection.ladybug_table] = {
            "added": len(new_keys - old_keys),
            "deleted": len(old_keys - new_keys),
            "changed": sum(old[key] != new[key] for key in old_keys & new_keys),
        }
    return summary
