"""Modern FastMCP server for the Intacct evidence catalog.

This implementation uses FastMCP v2.14.7+ patterns with explicit state management,
decorator-based tool registration, and type-safe request/response structures.
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import sqlite3
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, Literal, Required, TypedDict

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from catalog.graph_projection import GRAPH_PROJECTION_VERSION
from catalog.graph_state import graph_freshness
from catalog.rest_coverage import REQUIRED_TABLES, coverage_rows, coverage_summary
from config import CATALOG_DB, GRAPH_DB
from scripts.query_ui import (
    UiQueryError,
    query_ui_impact,
    query_ui_surface_detail,
)

# ============================================================================
# Type Definitions
# ============================================================================


class CatalogSnapshot(TypedDict, total=False):
    """Current state of the catalog and graph."""

    sqlite_snapshot: str
    graph_exists: bool
    active_graph_build: dict[str, Any] | None
    active_catalog_build: dict[str, Any] | None
    graph_fresh: bool
    repositories: list[dict[str, Any]]


class PageInfo(TypedDict, total=False):
    """Pagination info for paginated responses."""

    next_cursor: str | None
    truncated: bool


class CatalogError(TypedDict, total=False):
    """Standard error response structure."""

    code: str
    message: str
    details: dict[str, Any] | None


class CatalogResponse(TypedDict, total=False):
    """Standard response envelope for all catalog operations."""

    contract_version: int
    operation: str
    status: str
    data: dict[str, Any]
    snapshot: CatalogSnapshot
    page: PageInfo
    error: CatalogError | None


# ============================================================================
# MCP Input Contract
# ============================================================================


RepositoryKey = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Catalog repository identifier. Current configured values are "
            "'ia-main' and 'ia-restapi-automation'. Call repository_list to "
            "discover the values in the active catalog. Omit only when the "
            "requested name or path is unambiguous across repositories."
        ),
        examples=["ia-main", "ia-restapi-automation"],
    ),
]
EntityName = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Exact catalog entity name, matched case-insensitively. Use "
            "catalog_search(kind='entity') to discover names."
        ),
        examples=["APBill", "Customer", "GLAccount"],
    ),
]
CatalogFilePath = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Exact repository-relative path as stored by the catalog; do not "
            "pass an absolute filesystem path. Use catalog_search(kind='file') "
            "to discover paths."
        ),
        examples=["app/source/apar/ARInvoiceManager.cls"],
    ),
]
ResultLimit = Annotated[
    int,
    Field(
        ge=1,
        le=100,
        description="Maximum records to return for this page; valid range is 1..100.",
        examples=[25],
    ),
]
PaginationCursor = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Opaque next_cursor returned in the previous response page. Do not "
            "construct or modify it."
        ),
    ),
]
UiSurfaceKey = Annotated[
    str,
    Field(
        min_length=1,
        description=(
            "Exact UI surface key returned by ui_impact. actionUI keys begin with "
            "'actionui:' and NextGen keys begin with 'nextgen:'."
        ),
        examples=[
            "actionui:app/source/gl/glbatch_form.xml",
            "nextgen:general-ledger/journal-entry",
        ],
    ),
]
UiDetailRecordKind = Annotated[
    Literal[
        "artifacts",
        "fields",
        "events",
        "scripts",
        "includes",
        "references",
        "issues",
    ],
    Field(
        description=(
            "Evidence family to return for the UI surface. Events include at most "
            "100 nested handler-call records per event."
        ),
        examples=["events"],
    ),
]
ConfidenceScore = Annotated[
    float,
    Field(
        ge=0.0,
        le=1.0,
        description="Inclusive confidence score in the range 0.0..1.0.",
        examples=[0.7],
    ),
]
SemanticTraversalDepth = Annotated[
    int,
    Field(
        ge=1,
        le=3,
        description=(
            "Traversal depth from 1..3. Depth 1 uses SQLite evidence; depths "
            "2..3 require a fresh Ladybug graph projection."
        ),
        examples=[1],
    ),
]
GraphTraversalDepth = Annotated[
    int,
    Field(
        ge=1,
        le=3,
        description=(
            "Incoming graph traversal depth from 1..3. Every depth requires a "
            "fresh active Ladybug graph."
        ),
        examples=[1],
    ),
]
SemanticAxis = Annotated[
    Literal["A", "B", "C", "D", "E"],
    Field(
        description=(
            "Semantic axis: A=ownership/composition, B=business hierarchy, "
            "C=location hierarchy, D=visibility/restriction, "
            "E=entity-context metadata."
        )
    ),
]
RelationshipResolutionClass = Annotated[
    Literal[
        "builtin",
        "external",
        "heuristic",
        "project_resolved",
        "project_unresolved",
    ],
    Field(description="Relationship resolution classification to include."),
]
Eligibility = Annotated[
    Literal["active", "known_issue", "ci_only", "conditional"],
    Field(description="Gherkin scenario eligibility classification."),
]
RiskCategory = Annotated[
    Literal[
        "low_confidence_relationships",
        "unresolved_relationships",
        "heuristic_relationships",
        "entity_mapping_gaps",
        "security_conflicts",
        "security_unresolved_allowops",
        "missing_file_ids_security",
        "openapi_unknown_kind",
    ],
    Field(description="Catalog risk category returned by catalog_risk_summary."),
]
ConfidenceCategory = Annotated[
    Literal["relationships", "entity_mappings", "workflows", "entity_roots"],
    Field(description="Record family whose confidence or weight is filtered."),
]
AccessSurface = Annotated[
    Literal[
        "dbschema_table",
        "rest_endpoint",
        "security_menu",
        "security_menu_item",
        "security_operation",
        "security_policy",
        "security_resource",
        "workflow",
    ],
    Field(description="Entity access surface type to include."),
]
WorkflowType = Annotated[
    Literal[
        "allowed_operations",
        "approval",
        "posting",
        "reverse",
        "batch",
        "item",
        "entry",
        "ui",
        "rest",
    ],
    Field(description="Workflow classification to include."),
]


class QaChange(TypedDict, total=False):
    """One changed catalog file supplied to qa_impact."""

    file_path: Required[CatalogFilePath]


READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


# ============================================================================
# Configuration
# ============================================================================

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


# ============================================================================
# State Management
# ============================================================================


@dataclass
class CatalogState:
    """Encapsulates the Intacct catalog database connection and graph state."""

    db_path: Path
    graph_path: Path

    def conn(self) -> sqlite3.Connection:
        """Open a read-only connection to the catalog database."""
        if not self.db_path.is_file():
            raise FileNotFoundError("catalog database is unavailable")
        c = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")  # Immutable WAL snapshot for this connection
        return c

    def table_exists(self, c: sqlite3.Connection, name: str) -> bool:
        """Check if a table exists in the database."""
        return bool(
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
        )

    def snapshot(self, c: sqlite3.Connection) -> CatalogSnapshot:
        """Capture current catalog state."""
        catalog_build = None
        if self.table_exists(c, "catalog_builds"):
            catalog_build = c.execute(
                """SELECT id,parent_catalog_build_id,requested_mode,effective_mode,
                          status,content_fingerprint,source_revisions_json
                   FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        build = None
        if self.table_exists(c, "graph_builds"):
            graph_columns = {
                str(row[1]) for row in c.execute("PRAGMA table_info(graph_builds)")
            }
            generation_columns = {
                "catalog_build_id",
                "base_graph_build_id",
                "build_mode",
                "projection_version",
                "validation_summary",
            }
            canonical_graph_path = str(self.graph_path.expanduser().resolve())
            if generation_columns.issubset(graph_columns):
                build = c.execute(
                    """SELECT id,status,source_fingerprint,started_at,completed_at,
                              catalog_build_id,base_graph_build_id,build_mode,
                              projection_version,validation_summary
                       FROM graph_builds
                       WHERE status='active' AND graph_path=?
                       ORDER BY id DESC LIMIT 1""",
                    (canonical_graph_path,),
                ).fetchone()
            else:
                build = c.execute(
                    """SELECT id,status,source_fingerprint,started_at,completed_at
                       FROM graph_builds
                       WHERE status='active' AND graph_path=?
                       ORDER BY id DESC LIMIT 1""",
                    (canonical_graph_path,),
                ).fetchone()

        freshness = graph_freshness(
            c,
            catalog_path=self.db_path,
            graph_path=self.graph_path,
            projection_version=GRAPH_PROJECTION_VERSION,
        )
        graph_fresh = freshness.fresh

        repositories: list[dict[str, Any]] = []
        if self.table_exists(c, "repos"):
            repo_columns = {
                str(row[1]) for row in c.execute("PRAGMA table_info(repos)")
            }
            lifecycle_fields = (
                ",lifecycle_state,archive_source,archive_reason,archived_at"
                if {"lifecycle_state", "archive_source", "archive_reason", "archived_at"}
                .issubset(repo_columns)
                else ""
            )
            repositories = [
                dict(row)
                for row in c.execute(
                    "SELECT repo_key,tracked_branch,indexed_commit_sha,"
                    "last_scanned_at,last_built_at,index_status,diagnostic_error,"
                    "last_attempt_status,last_attempted_at,last_attempt_error"
                    + lifecycle_fields
                    + " FROM repos ORDER BY repo_key"
                )
            ]
            for repository in repositories:
                repository.setdefault("lifecycle_state", "active")
                repository.setdefault("archive_source", None)
                repository.setdefault("archive_reason", None)
                repository.setdefault("archived_at", None)

        return {
            "sqlite_snapshot": "read_transaction",
            "graph_exists": self.graph_path.is_file(),
            "active_catalog_build": dict(catalog_build) if catalog_build else None,
            "active_graph_build": dict(build) if build else None,
            "graph_fresh": graph_fresh,
            "repositories": repositories,
        }

    def graph_active(self, c: sqlite3.Connection) -> bool:
        """Check if there's an active graph matching current catalog."""
        return graph_freshness(
            c,
            catalog_path=self.db_path,
            graph_path=self.graph_path,
            projection_version=GRAPH_PROJECTION_VERSION,
        ).fresh


# ============================================================================
# Response Builders
# ============================================================================


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert sqlite3.Row to dict."""
    return dict(row)


def make_response(
    state: CatalogState,
    operation: str,
    data: dict[str, Any],
    c: sqlite3.Connection,
    status: str = "ok",
    error: CatalogError | None = None,
    next_cursor: str | None = None,
) -> CatalogResponse:
    """Build a standard catalog response envelope."""
    violations = c.execute("PRAGMA foreign_key_check").fetchall()
    if violations and status == "ok":
        status = "invalid_catalog"
        error = {
            "code": "foreign_key_violations",
            "message": "Catalog integrity validation failed",
            "details": {
                "count": len(violations),
                "sample": [tuple(v) for v in violations[:5]],
            },
        }

    return {
        "contract_version": 1,
        "operation": operation,
        "status": status,
        "data": data,
        "snapshot": state.snapshot(c),
        "page": {"next_cursor": next_cursor, "truncated": bool(next_cursor)},
        "error": error,
    }


def make_error_response(
    state: CatalogState,
    operation: str,
    code: str,
    message: str,
    c: sqlite3.Connection,
    details: dict[str, Any] | None = None,
) -> CatalogResponse:
    """Build an error response."""
    error: CatalogError = {"code": code, "message": message}
    if details:
        error["details"] = details
    return make_response(state, operation, {}, c, status="error", error=error)


def _repository_archived_response(
    state: CatalogState,
    operation: str,
    c: sqlite3.Connection,
    repo_key: str,
) -> CatalogResponse | None:
    """Return the explicit lifecycle result for a repository-qualified query."""

    columns = {str(row[1]) for row in c.execute("PRAGMA table_info(repos)")}
    if "lifecycle_state" not in columns:
        return None
    repo = c.execute(
        """SELECT repo_key,lifecycle_state,archive_source,archive_reason,archived_at
           FROM repos WHERE repo_key=?""",
        (repo_key,),
    ).fetchone()
    if repo is None or repo["lifecycle_state"] != "archived":
        return None
    return make_error_response(
        state,
        operation,
        "repository_archived",
        f"Repository is archived: {repo_key}",
        c,
        details={
            "repository": row_to_dict(repo),
            "message": "Archived repository evidence is unavailable by contract",
        },
    )


def repository_lifecycle_guard(func):
    """Make every explicit ``repo_key`` query report archive state consistently."""

    signature = inspect.signature(func)

    @wraps(func)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        repo_key = bound.arguments.get("repo_key")
        state = bound.arguments.get("state")
        if not repo_key or not isinstance(state, CatalogState):
            return func(*args, **kwargs)
        with state.conn() as c:
            response = _repository_archived_response(
                state, func.__name__.removesuffix("_impl"), c, str(repo_key)
            )
        return response if response is not None else func(*args, **kwargs)

    return wrapped


# ============================================================================
# Pagination & Validation
# ============================================================================


def decode_cursor(cursor: str | None) -> int:
    """Decode base64url cursor to offset."""
    if not cursor:
        return 0
    try:
        # Add padding
        padded = cursor + "=" * (-len(cursor) % 4)
        n = int(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if n < 0:
        raise ValueError("invalid cursor")
    return n


def encode_cursor(offset: int) -> str:
    """Encode offset to base64url cursor."""
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def validate_limit(n: int) -> int:
    """Validate and return limit value."""
    if not 1 <= n <= MAX_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_LIMIT}")
    return n


def paginate(
    rows: list[sqlite3.Row], limit: int, offset: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Paginate rows and return data + next cursor."""
    data = [row_to_dict(x) for x in rows[:limit]]
    next_cursor = encode_cursor(offset + limit) if len(rows) > limit else None
    return data, next_cursor


# ============================================================================
# Tool Implementations
# ============================================================================


# ============================================================================
# Phase 1: Dependency Surface Tools
# ============================================================================


@repository_lifecycle_guard
def workflow_structure_impl(
    state: CatalogState,
    entity_name: str,
    workflow_id: int | None = None,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Retrieve workflow structure: nodes and edges with ordinal sequencing."""
    with state.conn() as c:
        # Find workflow(s)
        sql = (
            "SELECT w.id, w.name, w.workflow_type, r.repo_key, e.name entity_name "
            "FROM workflows w "
            "JOIN entity_nodes e ON e.id = w.entity_id "
            "JOIN repos r ON r.id = w.repo_id "
            "WHERE e.name = ? COLLATE NOCASE AND (? IS NULL OR r.repo_key = ?)"
        )
        if workflow_id:
            sql += " AND w.id = ?"
            workflows = c.execute(
                sql, (entity_name, repo_key, repo_key, workflow_id)
            ).fetchall()
        else:
            workflows = c.execute(sql, (entity_name, repo_key, repo_key)).fetchall()

        if not workflows:
            return make_error_response(
                state,
                "workflow_structure",
                "workflow_not_found",
                f"No workflow found for entity {entity_name}"
                + (f" with id {workflow_id}" if workflow_id else ""),
                c,
            )

        data_list = []
        for wf in workflows:
            wf_id = wf["id"]
            nodes = [
                row_to_dict(r)
                for r in c.execute(
                    "SELECT id, workflow_id, entity_id, node_kind, node_key, name, ordinal, action, "
                    "source_kind, file_id, symbol_id, metadata_json "
                    "FROM workflow_nodes WHERE workflow_id = ? ORDER BY ordinal, id",
                    (wf_id,),
                )
            ]
            edges = [
                row_to_dict(r)
                for r in c.execute(
                    "SELECT id, workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence, "
                    "confidence, file_id, symbol_id "
                    "FROM workflow_edges WHERE workflow_id = ? ORDER BY ordinal, id",
                    (wf_id,),
                )
            ]
            data_list.append(
                {
                    "workflow": row_to_dict(wf),
                    "nodes": nodes,
                    "edges": edges,
                }
            )

        return make_response(
            state,
            "workflow_structure",
            {"workflows": data_list},
            c,
        )


@repository_lifecycle_guard
def entity_access_detail_impl(
    state: CatalogState,
    entity_name: str,
    surface_type: str | None = None,
    repo_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Retrieve entity access links by surface with evidence provenance."""
    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    with state.conn() as c:
        # Find entity
        e = c.execute(
            "SELECT id FROM entity_nodes WHERE name = ? COLLATE NOCASE",
            (entity_name,),
        ).fetchone()

        if not e:
            return make_error_response(
                state,
                "entity_access_detail",
                "entity_not_found",
                f"No entity named {entity_name}",
                c,
            )

        entity_id = e["id"]
        sql = (
            "SELECT eal.id, r.repo_key, eal.surface, eal.record_id, eal.link_type, "
            "eal.evidence_file_id, eal.evidence_symbol_id, eal.confidence_mode, eal.notes, "
            "ef.path evidence_file_path, es.name evidence_symbol_name "
            "FROM entity_access_links eal "
            "JOIN repos r ON r.id = eal.repo_id "
            "LEFT JOIN files ef ON ef.id = eal.evidence_file_id "
            "LEFT JOIN symbols es ON es.id = eal.evidence_symbol_id "
            "WHERE eal.entity_id = ? AND (? IS NULL OR r.repo_key = ?)"
        )
        args: list[Any] = [entity_id, repo_key, repo_key]

        if surface_type:
            sql += " AND eal.surface = ?"
            args.append(surface_type)

        sql += " ORDER BY eal.surface, eal.record_id, eal.id"

        rows = c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)).fetchall()
        data, nxt = paginate(rows, lim, off)

        return make_response(
            state,
            "entity_access_detail",
            {"entity_name": entity_name, "links": data},
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def security_dependency_chain_impl(
    state: CatalogState,
    op_key: str,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Retrieve security operation dependencies: allowops, policy grants, and menu links."""
    with state.conn() as c:
        # Find operation(s) by op_key
        ops = c.execute(
            "SELECT o.id, o.op_key, o.op_numeric_id, o.title, o.action, r.repo_key "
            "FROM security_operations o "
            "JOIN repos r ON r.id = o.repo_id "
            "WHERE o.op_key = ? AND (? IS NULL OR r.repo_key = ?)",
            (op_key, repo_key, repo_key),
        ).fetchall()

        if not ops:
            return make_error_response(
                state,
                "security_dependency_chain",
                "operation_not_found",
                f"No security operation with key {op_key}",
                c,
            )

        chains = []
        for op in ops:
            op_id = op["id"]

            # Allowed operations (allowops)
            allowops = [
                row_to_dict(r)
                for r in c.execute(
                    "SELECT sao.id, sao.allowed_op_key, sao.allowed_operation_id, sao.resolution_reason, "
                    "so.id allowed_op_id, so.title allowed_op_title "
                    "FROM security_operation_allowops sao "
                    "LEFT JOIN security_operations so ON so.id = sao.allowed_operation_id "
                    "WHERE sao.operation_id = ? ORDER BY sao.allowed_op_key",
                    (op_id,),
                )
            ]

            # Policy eops (what policies grant this operation)
            policy_eops = [
                row_to_dict(r)
                for r in c.execute(
                    "SELECT spe.id, sp.policy_name, spv.value_key, spe.op_key, so.id op_id, so.title op_title "
                    "FROM security_policy_eops spe "
                    "JOIN security_policy_values spv ON spv.id = spe.policy_value_id "
                    "JOIN security_policies sp ON sp.id = spv.policy_id "
                    "LEFT JOIN security_operations so ON so.op_key = spe.op_key AND so.repo_id = sp.repo_id "
                    "WHERE spe.op_key = ? ORDER BY sp.policy_name, spv.value_key",
                    (op_key,),
                )
            ]

            # Menu items pointing to this operation
            menu_items = [
                row_to_dict(r)
                for r in c.execute(
                    "SELECT smi.id, sm.menu_name, smi.item_path, smi.menu_key, "
                    "so.id op_id, so.title op_title "
                    "FROM security_menu_items smi "
                    "JOIN security_menus sm ON sm.id = smi.menu_id "
                    "LEFT JOIN security_operations so ON so.op_key = smi.menu_key AND so.repo_id = sm.repo_id "
                    "WHERE smi.menu_key = ? ORDER BY sm.menu_name, smi.item_path",
                    (op_key,),
                )
            ]

            chains.append(
                {
                    "operation": row_to_dict(op),
                    "allowed_operations": allowops,
                    "policy_grants": policy_eops,
                    "menu_references": menu_items,
                }
            )

        return make_response(
            state,
            "security_dependency_chain",
            {"chains": chains},
            c,
        )


@repository_lifecycle_guard
def openapi_file_dependencies_impl(
    state: CatalogState,
    file_path: str,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Retrieve OpenAPI file reference dependencies."""
    with state.conn() as c:
        # Find file
        files = c.execute(
            "SELECT f.id, r.repo_key, f.path FROM files f "
            "JOIN repos r ON r.id = f.repo_id "
            "WHERE f.path = ? AND (? IS NULL OR r.repo_key = ?)",
            (file_path, repo_key, repo_key),
        ).fetchall()

        if not files:
            return make_error_response(
                state,
                "openapi_file_dependencies",
                "file_not_found",
                f"File not found: {file_path}",
                c,
            )

        if len(files) > 1 and not repo_key:
            return make_response(
                state,
                "openapi_file_dependencies",
                {"candidates": [row_to_dict(r) for r in files]},
                c,
                status="ambiguous",
                error={
                    "code": "ambiguous_file",
                    "message": "File exists in multiple repos; retry with repo_key",
                },
            )

        f = files[0]
        file_id = f["id"]

        # Outgoing refs (from this file)
        outgoing = [
            row_to_dict(r)
            for r in c.execute(
                "SELECT ofre.id, ofre.ref_value, ofre.ref_path, ofre.confidence, "
                "tf.path target_file_path "
                "FROM openapi_file_ref_edges ofre "
                "JOIN files tf ON tf.id = ofre.target_file_id "
                "WHERE ofre.source_file_id = ? ORDER BY ofre.ref_value",
                (file_id,),
            )
        ]

        # Incoming refs (to this file)
        incoming = [
            row_to_dict(r)
            for r in c.execute(
                "SELECT ofre.id, ofre.ref_value, ofre.ref_path, ofre.confidence, "
                "sf.path source_file_path "
                "FROM openapi_file_ref_edges ofre "
                "JOIN files sf ON sf.id = ofre.source_file_id "
                "WHERE ofre.target_file_id = ? ORDER BY ofre.ref_value",
                (file_id,),
            )
        ]

        return make_response(
            state,
            "openapi_file_dependencies",
            {
                "file": row_to_dict(f),
                "outgoing_refs": outgoing,
                "incoming_refs": incoming,
            },
            c,
        )


# ============================================================================
# Phase 2: Risk Surface Tools
# ============================================================================


def catalog_risk_summary_impl(state: CatalogState) -> CatalogResponse:
    """Aggregate risk signals across the catalog."""
    with state.conn() as c:
        # Relationships confidence and resolution
        rel_stats = c.execute(
            "SELECT COUNT(*) as total, "
            "COUNT(CASE WHEN confidence < 0.7 THEN 1 END) as low_confidence, "
            "COUNT(CASE WHEN resolution_class = 'project_unresolved' THEN 1 END) as unresolved, "
            "COUNT(CASE WHEN resolution_class = 'heuristic' THEN 1 END) as heuristic, "
            "AVG(confidence) as avg_confidence "
            "FROM relationships"
        ).fetchone()

        # Entities and mappings
        entity_stats = c.execute(
            "SELECT COUNT(DISTINCT e.id) as total_entities, "
            "COUNT(DISTINCT em.id) as total_mappings, "
            "COUNT(CASE WHEN em.confidence < 1.0 THEN em.id END) as weak_mappings "
            "FROM entity_nodes e "
            "LEFT JOIN entity_mappings em ON em.entity_id = e.id"
        ).fetchone()

        # Entity roots (canonical symbols)
        roots_stats = c.execute(
            "SELECT COUNT(DISTINCT e.id) as entities_with_roots, "
            "COUNT(DISTINCT CASE WHEN er.id IS NULL THEN e.id END) as entities_missing_roots "
            "FROM entity_nodes e "
            "LEFT JOIN entity_roots er ON er.entity_id = e.id"
        ).fetchone()

        # Security operations
        sec_stats = c.execute(
            "SELECT COUNT(*) as total_operations, "
            "COUNT(CASE WHEN file_id IS NULL THEN 1 END) as missing_file_ids "
            "FROM security_operations"
        ).fetchone()

        # Security conflicts
        conflicts = c.execute(
            "SELECT COUNT(DISTINCT op_key) as conflicting_keys "
            "FROM security_operations "
            "GROUP BY op_key "
            "HAVING COUNT(DISTINCT op_numeric_id) > 1"
        ).fetchall()
        conflict_count = len(conflicts) if conflicts else 0

        # Security unresolved
        unresolved_allowops = c.execute(
            "SELECT COUNT(*) as count FROM security_operation_allowops WHERE allowed_operation_id IS NULL"
        ).fetchone()

        # OpenAPI specs
        openapi_stats = c.execute(
            "SELECT COUNT(*) as total_specs, "
            "COUNT(CASE WHEN kind = 'unknown' THEN 1 END) as unknown_kind, "
            "COUNT(CASE WHEN canonical_name LIKE '%/%' THEN 1 END) as path_slug_leakage, "
            "COUNT(CASE WHEN x_mapped_to IS NOT NULL THEN 1 END) as with_mapping "
            "FROM openapispec_index"
        ).fetchone()

        # Graph freshness
        graph_snapshot = state.snapshot(c)
        graph_build = graph_snapshot.get("active_graph_build")
        graph_fresh = bool(graph_snapshot.get("graph_fresh"))

        # Repository health
        repo_stats = c.execute(
            "SELECT COUNT(*) as total_repos, "
            "COUNT(CASE WHEN index_status = 'active' THEN 1 END) as active_repos, "
            "COUNT(CASE WHEN diagnostic_error IS NOT NULL THEN 1 END) as repos_with_errors "
            "FROM repos"
        ).fetchone()

        # Compute risk scores
        rel_confidence_score = (
            float(rel_stats["avg_confidence"]) if rel_stats["avg_confidence"] else 0.0
        )
        rel_resolution_score = 1.0 - (
            (rel_stats["unresolved"] + rel_stats["heuristic"])
            / max(rel_stats["total"], 1)
        )

        entities_with_roots = roots_stats["entities_with_roots"] or 0
        total_entities = entity_stats["total_entities"] or 1
        entity_completeness_score = (
            entities_with_roots / total_entities if total_entities > 0 else 0.0
        )

        sec_conflict_score = 1.0 - (
            (conflict_count + unresolved_allowops["count"])
            / max(sec_stats["total_operations"], 1)
        )

        openapi_linkage = openapi_stats["with_mapping"] / max(
            openapi_stats["total_specs"], 1
        )
        openapi_quality_score = 1.0 - (
            (openapi_stats["unknown_kind"] + openapi_stats["path_slug_leakage"])
            / max(openapi_stats["total_specs"], 1)
        )

        graph_freshness_score = 1.0 if graph_fresh else 0.5
        repo_health_score = (repo_stats["active_repos"] or 0) / max(
            repo_stats["total_repos"], 1
        )

        overall_score = (
            rel_resolution_score * 0.2
            + rel_confidence_score * 0.15
            + entity_completeness_score * 0.15
            + sec_conflict_score * 0.15
            + openapi_quality_score * 0.15
            + graph_freshness_score * 0.1
            + repo_health_score * 0.1
        )

        return make_response(
            state,
            "catalog_risk_summary",
            {
                "relationships": {
                    "total": rel_stats["total"],
                    "avg_confidence": round(rel_stats["avg_confidence"], 3)
                    if rel_stats["avg_confidence"]
                    else None,
                    "low_confidence": rel_stats["low_confidence"],
                    "unresolved": rel_stats["unresolved"],
                    "heuristic": rel_stats["heuristic"],
                },
                "entities": {
                    "total": entity_stats["total_entities"],
                    "total_mappings": entity_stats["total_mappings"],
                    "weak_mappings": entity_stats["weak_mappings"],
                    "with_canonical_roots": entities_with_roots,
                    "missing_roots": roots_stats["entities_missing_roots"],
                },
                "security": {
                    "total_operations": sec_stats["total_operations"],
                    "missing_file_ids": sec_stats["missing_file_ids"],
                    "conflicting_op_keys": conflict_count,
                    "unresolved_allowops": unresolved_allowops["count"],
                },
                "openapi": {
                    "total_specs": openapi_stats["total_specs"],
                    "with_entity_mapping": openapi_stats["with_mapping"],
                    "linkage_percent": round(openapi_linkage * 100, 2),
                    "unknown_kind": openapi_stats["unknown_kind"],
                    "path_slug_leakage": openapi_stats["path_slug_leakage"],
                },
                "graph": {
                    "active": graph_fresh,
                    "build_status": graph_build["status"] if graph_build else None,
                },
                "repositories": {
                    "total": repo_stats["total_repos"],
                    "active": repo_stats["active_repos"],
                    "with_errors": repo_stats["repos_with_errors"],
                },
                "risk_scores": {
                    "relationships_resolution": round(rel_resolution_score, 3),
                    "relationships_confidence": round(rel_confidence_score, 3),
                    "entity_completeness": round(entity_completeness_score, 3),
                    "security_integrity": round(sec_conflict_score, 3),
                    "openapi_quality": round(openapi_quality_score, 3),
                    "graph_freshness": round(graph_freshness_score, 3),
                    "repository_health": round(repo_health_score, 3),
                    "overall": round(overall_score, 3),
                },
            },
            c,
        )


@repository_lifecycle_guard
def risk_detail_impl(
    state: CatalogState,
    category: str,
    entity_name: str | None = None,
    symbol_name: str | None = None,
    repo_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Drill down into specific risk category with pagination."""
    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    valid_categories = {
        "low_confidence_relationships",
        "unresolved_relationships",
        "heuristic_relationships",
        "entity_mapping_gaps",
        "security_conflicts",
        "security_unresolved_allowops",
        "missing_file_ids_security",
        "openapi_unknown_kind",
    }

    if category not in valid_categories:
        raise ValueError(f"invalid category: {category}")

    with state.conn() as c:
        if category == "low_confidence_relationships":
            sql = (
                "SELECT rel.id, r.repo_key, rel.source_name, rel.target_name, "
                "rel.relationship_type, rel.confidence, rel.resolution_class, rel.evidence "
                "FROM relationships rel "
                "JOIN repos r ON r.id = rel.repo_id "
                "WHERE rel.confidence < 0.7 AND (? IS NULL OR r.repo_key = ?)"
            )
            args = [repo_key, repo_key]

        elif category == "unresolved_relationships":
            sql = (
                "SELECT rel.id, r.repo_key, rel.source_name, rel.target_name, "
                "rel.relationship_type, rel.confidence, rel.resolution_class, rel.resolution_reason "
                "FROM relationships rel "
                "JOIN repos r ON r.id = rel.repo_id "
                "WHERE rel.resolution_class = 'project_unresolved' AND (? IS NULL OR r.repo_key = ?)"
            )
            args = [repo_key, repo_key]

        elif category == "heuristic_relationships":
            sql = (
                "SELECT rel.id, r.repo_key, rel.source_name, rel.target_name, "
                "rel.relationship_type, rel.confidence, rel.resolution_class, rel.reason "
                "FROM relationships rel "
                "JOIN repos r ON r.id = rel.repo_id "
                "WHERE rel.resolution_class = 'heuristic' AND (? IS NULL OR r.repo_key = ?)"
            )
            args = [repo_key, repo_key]

        elif category == "entity_mapping_gaps":
            sql = (
                "SELECT em.id, r.repo_key, e.name entity_name, em.mapping_type, em.confidence, em.source_text "
                "FROM entity_mappings em "
                "JOIN entity_nodes e ON e.id = em.entity_id "
                "JOIN repos r ON r.id = em.repo_id "
                "WHERE em.confidence < 1.0 AND (? IS NULL OR r.repo_key = ?)"
            )
            if entity_name:
                sql += " AND e.name COLLATE NOCASE = ?"
                args = [repo_key, repo_key, entity_name]
            else:
                args = [repo_key, repo_key]

        elif category == "security_conflicts":
            sql = (
                "SELECT o1.id, r.repo_key, o1.op_key, o1.op_numeric_id, o1.title, "
                "GROUP_CONCAT(o2.op_numeric_id) as conflicting_ids, "
                "GROUP_CONCAT(o2.title) as conflicting_titles "
                "FROM security_operations o1 "
                "JOIN repos r ON r.id = o1.repo_id "
                "JOIN security_operations o2 ON o2.op_key = o1.op_key AND o2.repo_id = r.id AND o2.op_numeric_id != o1.op_numeric_id "
                "WHERE (? IS NULL OR r.repo_key = ?) "
                "GROUP BY o1.id, r.repo_key, o1.op_key, o1.op_numeric_id, o1.title"
            )
            args = [repo_key, repo_key]

        elif category == "security_unresolved_allowops":
            sql = (
                "SELECT sao.id, r.repo_key, so.op_key, sao.allowed_op_key, sao.allowed_operation_id "
                "FROM security_operation_allowops sao "
                "JOIN security_operations so ON so.id = sao.operation_id "
                "JOIN repos r ON r.id = so.repo_id "
                "WHERE sao.allowed_operation_id IS NULL AND (? IS NULL OR r.repo_key = ?)"
            )
            args = [repo_key, repo_key]

        elif category == "missing_file_ids_security":
            sql = (
                "SELECT so.id, r.repo_key, so.op_key, so.title, so.source_file FROM security_operations so "
                "JOIN repos r ON r.id = so.repo_id "
                "WHERE so.file_id IS NULL AND (? IS NULL OR r.repo_key = ?)"
            )
            args = [repo_key, repo_key]

        elif category == "openapi_unknown_kind":
            sql = (
                "SELECT oi.id, r.repo_key, oi.file_path, oi.module, oi.kind, oi.canonical_name "
                "FROM openapispec_index oi "
                "JOIN repos r ON r.id = oi.repo_id "
                "WHERE oi.kind = 'unknown' AND (? IS NULL OR r.repo_key = ?)"
            )
            args = [repo_key, repo_key]

        # Map category to id column for proper ORDER BY qualification
        id_columns = {
            "low_confidence_relationships": "rel.id",
            "unresolved_relationships": "rel.id",
            "heuristic_relationships": "rel.id",
            "entity_mapping_gaps": "em.id",
            "security_conflicts": "o1.id",
            "security_unresolved_allowops": "sao.id",
            "missing_file_ids_security": "so.id",
            "openapi_unknown_kind": "oi.id",
        }
        sql += f" ORDER BY {id_columns[category]}"
        rows = c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)).fetchall()
        data, nxt = paginate(rows, lim, off)

        return make_response(
            state,
            "risk_detail",
            {"category": category, "records": data},
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def confidence_band_query_impl(
    state: CatalogState,
    category: str,
    confidence_min: float,
    confidence_max: float,
    repo_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Query records within a confidence band."""
    if not 0.0 <= confidence_min <= 1.0 or not 0.0 <= confidence_max <= 1.0:
        raise ValueError("confidence_min and confidence_max must be 0.0..1.0")
    if confidence_min > confidence_max:
        raise ValueError("confidence_min must be <= confidence_max")

    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    valid_categories = {"relationships", "entity_mappings", "workflows", "entity_roots"}

    if category not in valid_categories:
        raise ValueError(f"invalid category: {category}")

    with state.conn() as c:
        if category == "relationships":
            sql = (
                "SELECT rel.id, r.repo_key, rel.source_name, rel.target_name, rel.confidence "
                "FROM relationships rel "
                "JOIN repos r ON r.id = rel.repo_id "
                "WHERE rel.confidence >= ? AND rel.confidence <= ? AND (? IS NULL OR r.repo_key = ?) "
                "ORDER BY rel.confidence, rel.id"
            )
            args = [confidence_min, confidence_max, repo_key, repo_key]

        elif category == "entity_mappings":
            sql = (
                "SELECT em.id, r.repo_key, e.name entity_name, em.mapping_type, em.confidence "
                "FROM entity_mappings em "
                "JOIN entity_nodes e ON e.id = em.entity_id "
                "JOIN repos r ON r.id = em.repo_id "
                "WHERE em.confidence >= ? AND em.confidence <= ? AND (? IS NULL OR r.repo_key = ?) "
                "ORDER BY em.confidence, em.id"
            )
            args = [confidence_min, confidence_max, repo_key, repo_key]

        elif category == "workflows":
            sql = (
                "SELECT w.id, r.repo_key, e.name entity_name, w.name workflow_name, w.confidence "
                "FROM workflows w "
                "JOIN entity_nodes e ON e.id = w.entity_id "
                "JOIN repos r ON r.id = w.repo_id "
                "WHERE w.confidence >= ? AND w.confidence <= ? AND (? IS NULL OR r.repo_key = ?) "
                "ORDER BY w.confidence, w.id"
            )
            args = [confidence_min, confidence_max, repo_key, repo_key]

        elif category == "entity_roots":
            sql = (
                "SELECT er.id, r.repo_key, e.name entity_name, s.name symbol_name, er.weight "
                "FROM entity_roots er "
                "JOIN entity_nodes e ON e.id = er.entity_id "
                "JOIN symbols s ON s.id = er.symbol_id "
                "JOIN repos r ON r.id = er.repo_id "
                "WHERE er.weight >= ? AND er.weight <= ? AND (? IS NULL OR r.repo_key = ?) "
                "ORDER BY er.weight, er.id"
            )
            args = [confidence_min, confidence_max, repo_key, repo_key]

        rows = c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)).fetchall()
        data, nxt = paginate(rows, lim, off)

        return make_response(
            state,
            "confidence_band_query",
            {
                "category": category,
                "confidence_range": [confidence_min, confidence_max],
                "records": data,
            },
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def catalog_search_impl(
    state: CatalogState,
    query: str,
    kind: str,
    limit: int,
    cursor: str | None,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Search across catalog entities, files, symbols, APIs, workflows, and security."""
    if not query.strip():
        raise ValueError("query must not be empty")

    lim = validate_limit(limit)
    off = decode_cursor(cursor)
    like = f"%{query.strip()}%"

    queries: dict[str, tuple[str, tuple[Any, ...]]] = {
        "entity": (
            "SELECT e.id,e.name,e.entity_type,e.confidence,r.repo_key,"
            "eo.ent_file,eo.module,eo.table_name,eo.view_name,eo.dummy,"
            "eo.source_file_id,eo.extractor,eo.confidence occurrence_confidence "
            "FROM entity_nodes e "
            "JOIN entity_occurrences eo ON eo.entity_id=e.id "
            "JOIN repos r ON r.id=eo.repo_id "
            "WHERE e.name LIKE ? AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY e.name,r.repo_key,e.id",
            (like, repo_key, repo_key),
        ),
        "file": (
            "SELECT f.id,r.repo_key,f.path,f.language,f.size_bytes,f.sha1 "
            "FROM files f "
            "JOIN repos r ON r.id=f.repo_id "
            "WHERE f.path LIKE ? AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY r.repo_key,f.path,f.id",
            (like, repo_key, repo_key),
        ),
        "symbol": (
            "SELECT s.id,s.name,s.kind,s.language,s.start_line,s.end_line,"
            "r.repo_key,f.path file_path "
            "FROM symbols s "
            "JOIN files f ON f.id=s.file_id "
            "JOIN repos r ON r.id=f.repo_id "
            "WHERE s.name LIKE ? AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY s.name,r.repo_key,s.id",
            (like, repo_key, repo_key),
        ),
        "api": (
            "SELECT o.id,r.repo_key,o.file_path,o.module,o.kind,o.canonical_name,"
            "o.resource_path,o.x_mapped_to "
            "FROM openapispec_index o "
            "JOIN repos r ON r.id=o.repo_id "
            "WHERE (o.canonical_name LIKE ? OR o.resource_path LIKE ?) "
            "AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY r.repo_key,o.file_path,o.id",
            (like, like, repo_key, repo_key),
        ),
        "workflow": (
            "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_file,"
            "e.name entity_name "
            "FROM workflows w "
            "JOIN entity_nodes e ON e.id=w.entity_id "
            "JOIN repos r ON r.id=w.repo_id "
            "WHERE (w.name LIKE ? OR e.name LIKE ?) "
            "AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY e.name,w.name,r.repo_key,w.id",
            (like, like, repo_key, repo_key),
        ),
        "security": (
            "SELECT o.id,r.repo_key,o.op_key,o.title,o.action,o.source_file,"
            "o.source_line "
            "FROM security_operations o "
            "JOIN repos r ON r.id=o.repo_id "
            "WHERE (o.op_key LIKE ? OR o.title LIKE ?) "
            "AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY o.op_key,r.repo_key,o.id",
            (like, like, repo_key, repo_key),
        ),
    }

    if kind not in {*queries, "all"}:
        raise ValueError("invalid search kind")

    if kind == "all" and cursor:
        raise ValueError(
            "catalog_search(kind='all') does not support pagination; choose one kind"
        )

    selected = queries if kind == "all" else {kind: queries[kind]}

    with state.conn() as c:
        records: list[dict[str, Any]] = []
        for label, (sql, args) in selected.items():
            rows = c.execute(
                sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)
            ).fetchall()
            records += [{"kind": label, "record": row_to_dict(r)} for r in rows]

        records.sort(key=lambda r: (r["kind"], str(r["record"])))

        if kind == "all" and len(records) > lim:
            raise ValueError(
                "catalog_search(kind='all') result exceeds limit; choose one kind"
            )

        nxt = None
        if kind != "all" and len(records) > lim:
            nxt = encode_cursor(off + lim)

        return make_response(
            state,
            "catalog_search",
            {"repo_key": repo_key, "results": records[:lim]},
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def entity_context_impl(
    state: CatalogState,
    name: str,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Retrieve full context for an entity including occurrences, mappings, and workflows."""
    with state.conn() as c:
        e = c.execute(
            "SELECT id,name,entity_type,confidence FROM entity_nodes "
            "WHERE name=? COLLATE NOCASE",
            (name,),
        ).fetchone()

        if not e:
            return make_error_response(
                state,
                "entity_context",
                "entity_not_found",
                f"No entity named {name}",
                c,
            )

        entity_id = e["id"]
        data: dict[str, Any] = {"entity": row_to_dict(e)}

        # Occurrences
        data["occurrences"] = [
            row_to_dict(r)
            for r in c.execute(
                "SELECT eo.id,r.repo_key,eo.ent_file,eo.module,eo.table_name,"
                "eo.view_name,eo.dummy,eo.source_file_id,eo.extractor,"
                "eo.confidence,eo.created_at,eo.updated_at "
                "FROM entity_occurrences eo "
                "JOIN repos r ON r.id=eo.repo_id "
                "WHERE eo.entity_id=? AND (? IS NULL OR r.repo_key=?) "
                "ORDER BY r.repo_key,eo.id",
                (entity_id, repo_key, repo_key),
            )
        ]

        # Mappings, roots, workflows, endpoints
        for key, sql in {
            "mappings": (
                "SELECT em.id,r.repo_key,em.mapping_type,em.confidence,"
                "em.source_text,s.id symbol_id,s.name symbol_name,f.path file_path,"
                "s.start_line,s.end_line "
                "FROM entity_mappings em "
                "JOIN repos r ON r.id=em.repo_id "
                "LEFT JOIN symbols s ON s.id=em.symbol_id "
                "LEFT JOIN files f ON f.id=COALESCE(em.file_id,s.file_id) "
                "WHERE em.entity_id=? AND (? IS NULL OR r.repo_key=?) "
                "ORDER BY r.repo_key,em.id"
            ),
            "roots": (
                "SELECT er.id,r.repo_key,er.role,er.weight,er.reason,er.is_shared,"
                "s.id symbol_id,s.name symbol_name,f.path file_path,s.start_line,"
                "s.end_line "
                "FROM entity_roots er "
                "JOIN repos r ON r.id=er.repo_id "
                "JOIN symbols s ON s.id=er.symbol_id "
                "JOIN files f ON f.id=s.file_id "
                "WHERE er.entity_id=? AND (? IS NULL OR r.repo_key=?) "
                "ORDER BY r.repo_key,er.weight DESC,er.id"
            ),
            "workflows": (
                "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_kind,"
                "w.source_file,w.source_symbol_id,w.confidence,w.reason "
                "FROM workflows w "
                "JOIN repos r ON r.id=w.repo_id "
                "WHERE w.entity_id=? AND (? IS NULL OR r.repo_key=?) "
                "ORDER BY r.repo_key,w.workflow_type,w.name,w.id"
            ),
            "rest_endpoints": (
                "SELECT ep.id,r.repo_key,ep.method,ep.path,ep.handler_symbol_id,"
                "f.path file_path "
                "FROM rest_endpoints ep "
                "JOIN repos r ON r.id=ep.repo_id "
                "LEFT JOIN files f ON f.id=ep.file_id "
                "WHERE ep.entity_id=? AND (? IS NULL OR r.repo_key=?) "
                "ORDER BY r.repo_key,ep.path,ep.method,ep.id"
            ),
        }.items():
            data[key] = [
                row_to_dict(r) for r in c.execute(sql, (entity_id, repo_key, repo_key))
            ]

        return make_response(state, "entity_context", data, c)


def rest_coverage_impl(
    state: CatalogState,
    name: str,
    version: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> CatalogResponse:
    """Show evidence-backed Gherkin REST coverage for an entity."""
    lim = validate_limit(limit)

    with state.conn() as c:
        missing = [
            table for table in REQUIRED_TABLES if not state.table_exists(c, table)
        ]
        if missing:
            return make_error_response(
                state,
                "rest_coverage",
                "coverage_tables_missing",
                "REST coverage tables are unavailable",
                c,
                {"missing_tables": missing},
            )

        entity = c.execute(
            "SELECT id,name FROM entity_nodes WHERE name=? COLLATE NOCASE",
            (name,),
        ).fetchone()

        if not entity:
            return make_error_response(
                state,
                "rest_coverage",
                "entity_not_found",
                f"No entity named {name}",
                c,
            )

        entity_id = int(entity["id"])
        endpoints, diagnostics = coverage_rows(c, entity_id, version, lim)

        version_predicate = (
            "AND (source_version = ? OR source_version IS NULL)" if version else ""
        )
        total = c.execute(
            f"SELECT COUNT(*) FROM rest_endpoints "
            f"WHERE entity_id = ? {version_predicate}",
            (entity_id, *((version,) if version else ())),
        ).fetchone()[0]

        data = {
            "entity": {"id": entity_id, "name": entity["name"]},
            "endpoint_coverage": endpoints,
            "diagnostics": diagnostics,
            "summary": coverage_summary(endpoints, diagnostics),
            "coverage_scope": {
                "total_endpoint_count": total,
                "returned_endpoint_count": len(endpoints),
                "sampled": len(endpoints) < total,
            },
        }

        return make_response(state, "rest_coverage", data, c)


def entity_test_coverage_impl(
    state: CatalogState,
    entity_name: str,
    workflow_name: str | None = None,
    eligibility: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Retrieve Gherkin test cases (scenarios and their HTTP requests) covering an entity or its workflows.

    Returns test cases organized by feature/scenario with full request steps,
    Jira references, eligibility status, and a summary by eligibility tier.
    If workflow_name is provided, only test cases containing at least one
    operation_kind='workflow' request step for that entity are returned.
    """
    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    with state.conn() as c:
        # Verify required tables are present
        required_tables = (
            "test_cases",
            "test_requests",
            "test_entity_links",
            "entity_nodes",
            "repos",
        )
        missing = [t for t in required_tables if not state.table_exists(c, t)]
        if missing:
            return make_error_response(
                state,
                "entity_test_coverage",
                "tables_missing",
                "Test coverage tables are unavailable",
                c,
                {"missing_tables": missing},
            )

        # Resolve entity
        entity = c.execute(
            "SELECT id, name, entity_type FROM entity_nodes WHERE name = ? COLLATE NOCASE",
            (entity_name,),
        ).fetchone()
        if not entity:
            return make_error_response(
                state,
                "entity_test_coverage",
                "entity_not_found",
                f"No entity named {entity_name}",
                c,
            )
        entity_id = int(entity["id"])

        # Gather workflow context for the entity (informational)
        workflows: list[dict[str, Any]] = []
        if state.table_exists(c, "workflows"):
            wf_sql = (
                "SELECT w.id, w.name, w.workflow_type, r.repo_key "
                "FROM workflows w JOIN repos r ON r.id = w.repo_id "
                "WHERE w.entity_id = ?"
            )
            wf_args: list[Any] = [entity_id]
            if workflow_name:
                wf_sql += " AND w.name = ? COLLATE NOCASE"
                wf_args.append(workflow_name)
            wf_sql += " ORDER BY w.name"
            workflows = [row_to_dict(r) for r in c.execute(wf_sql, wf_args)]

        # Build optional filter clauses (no extra params needed for workflow_filter)
        eligibility_clause = ""
        query_args: list[Any] = [entity_id]
        if eligibility:
            valid_eligibilities = {"active", "known_issue", "ci_only", "conditional"}
            if eligibility not in valid_eligibilities:
                return make_error_response(
                    state,
                    "entity_test_coverage",
                    "invalid_eligibility",
                    f"eligibility must be one of: {sorted(valid_eligibilities)}",
                    c,
                )
            eligibility_clause = " AND tc.eligibility = ?"
            query_args.append(eligibility)

        workflow_clause = (
            " AND EXISTS ("
            "SELECT 1 FROM test_requests tr2 "
            "WHERE tr2.test_case_id = tc.id AND tr2.operation_kind = 'workflow'"
            ")"
            if workflow_name
            else ""
        )

        entity_exists_clause = (
            "EXISTS ("
            "SELECT 1 FROM test_requests tr "
            "JOIN test_entity_links tel ON tel.test_request_id = tr.id "
            "WHERE tr.test_case_id = tc.id AND tel.entity_id = ?"
            ")"
        )

        # Summary counts by eligibility (unaffected by eligibility filter)
        summary_rows = c.execute(
            f"""
            SELECT tc.eligibility, COUNT(DISTINCT tc.id) AS cnt
            FROM test_cases tc
            WHERE {entity_exists_clause}
            {workflow_clause}
            GROUP BY tc.eligibility
            ORDER BY tc.eligibility
            """,
            [entity_id],
        ).fetchall()
        summary = {row["eligibility"]: row["cnt"] for row in summary_rows}
        total = sum(summary.values())

        # Paginated test case list
        rows = c.execute(
            f"""
            SELECT DISTINCT
                tc.id,
                r.repo_key AS suite_id,
                tc.feature_name,
                tc.scenario_name,
                tc.case_name,
                tc.example_row,
                tc.feature_line,
                tc.scenario_line,
                tc.eligibility,
                tc.tags_json,
                tc.jira_refs_json,
                f.path AS feature_path
            FROM test_cases tc
            JOIN repos r ON r.id = tc.repo_id
            LEFT JOIN files f ON f.id = tc.file_id
            WHERE {entity_exists_clause}
            {eligibility_clause}
            {workflow_clause}
            ORDER BY tc.eligibility, tc.feature_name, tc.scenario_name,
                     tc.example_row, tc.id
            LIMIT ? OFFSET ?
            """,
            (*query_args, lim + 1, off),
        ).fetchall()

        page_rows = rows[:lim]
        next_cursor = encode_cursor(off + lim) if len(rows) > lim else None

        # Enrich each test case with its HTTP request steps
        cases: list[dict[str, Any]] = []
        for row in page_rows:
            tc_id = row["id"]
            requests = [
                row_to_dict(r)
                for r in c.execute(
                    """
                    SELECT tr.id, tr.ordinal, tr.step_line, tr.method,
                           tr.object_token, tr.raw_path, tr.normalized_path,
                           tr.request_version, tr.expected_status, tr.operation_kind
                    FROM test_requests tr
                    WHERE tr.test_case_id = ?
                    ORDER BY tr.ordinal
                    """,
                    (tc_id,),
                )
            ]
            case_dict = row_to_dict(row)
            case_dict["requests"] = requests
            cases.append(case_dict)

        data: dict[str, Any] = {
            "entity": row_to_dict(entity),
            "workflows": workflows,
            "total_test_case_count": total,
            "summary_by_eligibility": summary,
            "test_cases": cases,
            "filter": {
                "workflow_name": workflow_name,
                "eligibility": eligibility,
            },
        }

        return make_response(
            state,
            "entity_test_coverage",
            data,
            c,
            next_cursor=next_cursor,
        )


@repository_lifecycle_guard
def relationship_query_impl(
    state: CatalogState,
    name: str,
    direction: str,
    resolution_classes: list[str] | None = None,
    confidence_min: float | None = None,
    confidence_max: float | None = None,
    repo_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Query relationships by source or target symbol with optional confidence filtering."""
    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    if confidence_min is not None and confidence_max is not None:
        if not (0.0 <= confidence_min <= 1.0 and 0.0 <= confidence_max <= 1.0):
            raise ValueError("confidence_min and confidence_max must be 0.0..1.0")
        if confidence_min > confidence_max:
            raise ValueError("confidence_min must be <= confidence_max")

    column = "source_name" if direction == "outgoing" else "target_name"
    sql = (
        "SELECT rel.id,r.repo_key,rel.source_symbol_id,rel.source_name,"
        "rel.source_kind,rel.target_symbol_id,rel.target_name,rel.target_kind,"
        "rel.relationship_type,rel.file_path,rel.language,rel.confidence,"
        "rel.evidence,rel.resolution_class,rel.resolution_reason,rel.extractor "
        "FROM relationships rel "
        "JOIN repos r ON r.id=rel.repo_id "
        f"WHERE rel.{column}=? AND (? IS NULL OR r.repo_key=?)"
    )
    args: list[Any] = [name, repo_key, repo_key]

    if resolution_classes:
        sql += (
            " AND resolution_class IN (" + ",".join("?" * len(resolution_classes)) + ")"
        )
        args += resolution_classes

    if confidence_min is not None:
        sql += " AND rel.confidence >= ?"
        args.append(confidence_min)

    if confidence_max is not None:
        sql += " AND rel.confidence <= ?"
        args.append(confidence_max)

    sql += " ORDER BY rel.id"

    with state.conn() as c:
        rows = c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)).fetchall()
        data, nxt = paginate(rows, lim, off)
        return make_response(
            state,
            "relationship_query",
            {"relationships": data},
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def api_surface_impl(
    state: CatalogState,
    entity_name: str | None = None,
    path_fragment: str | None = None,
    repo_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Query REST API endpoints by entity or path."""
    if not entity_name and not path_fragment:
        raise ValueError("entity_name or path_fragment is required")

    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    sql = (
        "SELECT r.id,rp.repo_key,r.method,r.path,e.id entity_id,"
        "e.name entity_name,r.handler_symbol_id,f.path file_path "
        "FROM rest_endpoints r "
        "JOIN repos rp ON rp.id=r.repo_id "
        "LEFT JOIN entity_nodes e ON e.id=r.entity_id "
        "LEFT JOIN files f ON f.id=r.file_id "
        "WHERE (? IS NULL OR rp.repo_key=?)"
    )
    args: list[Any] = [repo_key, repo_key]

    if entity_name:
        sql += " AND e.name=? COLLATE NOCASE"
        args.append(entity_name)

    if path_fragment:
        sql += " AND r.path LIKE ?"
        args.append("%" + path_fragment + "%")

    sql += " ORDER BY r.path,r.method,r.id"

    with state.conn() as c:
        rows = c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)).fetchall()
        data, nxt = paginate(rows, lim, off)
        return make_response(
            state,
            "api_surface",
            {"endpoints": data},
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def workflow_context_impl(
    state: CatalogState,
    entity_name: str,
    workflow_type: str | None = None,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Retrieve workflows for an entity."""
    sql = (
        "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_kind,"
        "w.source_file,w.source_symbol_id,w.confidence,w.reason "
        "FROM workflows w "
        "JOIN entity_nodes e ON e.id=w.entity_id "
        "JOIN repos r ON r.id=w.repo_id "
        "WHERE e.name=? COLLATE NOCASE AND (? IS NULL OR r.repo_key=?)"
    )
    args: list[Any] = [entity_name, repo_key, repo_key]

    if workflow_type:
        sql += " AND w.workflow_type=?"
        args.append(workflow_type)

    sql += " ORDER BY w.workflow_type,w.name,w.id"

    with state.conn() as c:
        rows = c.execute(sql, args).fetchall()
        data = [row_to_dict(r) for r in rows]
        return make_response(state, "workflow_context", {"workflows": data}, c)


@repository_lifecycle_guard
def security_surface_impl(
    state: CatalogState,
    key_fragment: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Search security operations by key or title."""
    lim = validate_limit(limit)
    off = decode_cursor(cursor)

    sql = (
        "SELECT o.id,r.repo_key,o.op_key,o.op_numeric_id,o.title,o.action,"
        "o.script,o.source_file,o.source_line,o.source_kind "
        "FROM security_operations o "
        "JOIN repos r ON r.id=o.repo_id "
        "WHERE (o.op_key LIKE ? OR o.title LIKE ?) "
        "AND (? IS NULL OR r.repo_key=?) "
        "ORDER BY o.op_key,r.repo_key,o.id"
    )
    args = (
        "%" + key_fragment + "%",
        "%" + key_fragment + "%",
        repo_key,
        repo_key,
    )

    with state.conn() as c:
        rows = c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)).fetchall()
        data, nxt = paginate(rows, lim, off)
        return make_response(
            state,
            "security_surface",
            {"operations": data},
            c,
            next_cursor=nxt,
        )


@repository_lifecycle_guard
def symbol_references_impl(
    state: CatalogState,
    symbol_name: str | None = None,
    symbol_id: int | None = None,
    repo_key: str | None = None,
) -> CatalogResponse:
    """Find all references (callers and referencers) to a symbol."""
    with state.conn() as c:
        hits = c.execute(
            "SELECT s.id,s.name,s.kind,r.repo_key,f.path file_path,"
            "s.start_line,s.end_line "
            "FROM symbols s "
            "JOIN files f ON f.id=s.file_id "
            "JOIN repos r ON r.id=f.repo_id "
            "WHERE ((? IS NOT NULL AND s.id=?) OR (? IS NULL AND s.name=?)) "
            "AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY r.repo_key,s.id",
            (symbol_id, symbol_id, symbol_id, symbol_name, repo_key, repo_key),
        ).fetchall()

        if not hits:
            return make_error_response(
                state,
                "symbol_references",
                "symbol_not_found",
                "Symbol not found",
                c,
            )

        if symbol_id is None and len(hits) > 1:
            return make_response(
                state,
                "symbol_references",
                {"candidates": [row_to_dict(r) for r in hits]},
                c,
                status="ambiguous",
                error={
                    "code": "ambiguous_symbol",
                    "message": "Retry with symbol_id",
                },
            )

        if not state.graph_active(c):
            return make_response(
                state,
                "symbol_references",
                {"target": row_to_dict(hits[0])},
                c,
                status="graph_unavailable",
                error={
                    "code": "graph_unavailable",
                    "message": "No active Ladybug graph",
                },
            )

        from scripts.query_graph import (
            _query_symbol_usages,
            enrich_symbols_from_sql,
            get_graph_connection,
        )

        db, g = get_graph_connection(str(state.graph_path))
        try:
            x = _query_symbol_usages(g, int(hits[0]["id"]))
            both = x["callers"] + x["referencers"]
            y = enrich_symbols_from_sql(c, both)
            return make_response(
                state,
                "symbol_references",
                {
                    "target": row_to_dict(hits[0]),
                    "callers": y[: len(x["callers"])],
                    "referencers": y[len(x["callers"]) :],
                },
                c,
            )
        finally:
            g.close()
            db.close()


@repository_lifecycle_guard
def file_impact_impl(
    state: CatalogState,
    file_path: str,
    repo_key: str | None = None,
    depth: int = 1,
    max_edges_per_symbol: int = 25,
) -> CatalogResponse:
    """Analyze impact of changes to a file (incoming call graph)."""
    if not 1 <= depth <= 3 or not 1 <= max_edges_per_symbol <= 1000:
        raise ValueError("depth must be 1..3 and max_edges_per_symbol 1..1000")

    with state.conn() as c:
        files = c.execute(
            "SELECT f.id,f.path,r.repo_key FROM files f "
            "JOIN repos r ON r.id=f.repo_id "
            "WHERE f.path=? AND (? IS NULL OR r.repo_key=?) "
            "ORDER BY r.repo_key,f.id",
            (file_path, repo_key, repo_key),
        ).fetchall()

        if repo_key is None and len(files) > 1:
            return make_response(
                state,
                "file_impact",
                {"candidates": [row_to_dict(row) for row in files]},
                c,
                status="ambiguous",
                error={
                    "code": "ambiguous_file",
                    "message": "File path exists in multiple repositories; retry with repo_key",
                },
            )

        f = files[0] if files else None
        if not f:
            return make_error_response(
                state,
                "file_impact",
                "file_not_found",
                f"File not found: {file_path}",
                c,
            )

        if not state.graph_active(c):
            return make_response(
                state,
                "file_impact",
                {"file": row_to_dict(f)},
                c,
                status="graph_unavailable",
                error={
                    "code": "graph_stale",
                    "message": "No active graph matches the current SQLite catalog",
                },
            )

        from scripts.query_graph import (
            _query_bounded_incoming_traversal,
            _query_file_occurrences_from_graph,
            _query_file_symbols_from_graph,
            enrich_symbols_from_sql,
            get_graph_connection,
        )

        db, g = get_graph_connection(str(state.graph_path))
        try:
            seeds = _query_file_symbols_from_graph(g, file_path, repo_key)
            nodes, edges = _query_bounded_incoming_traversal(
                g,
                [x["symbol_id"] for x in seeds],
                depth,
                max_edges_per_symbol,
            )
            allnodes = [{**x, "depth": 0, "is_seed": True} for x in seeds] + nodes
            direct_occurrences = _query_file_occurrences_from_graph(
                g, file_path, repo_key
            )
            return make_response(
                state,
                "file_impact",
                {
                    "file": row_to_dict(f),
                    "seed_symbols": enrich_symbols_from_sql(c, allnodes[: len(seeds)]),
                    "direct_entity_occurrences": direct_occurrences,
                    "affected_symbols": enrich_symbols_from_sql(c, allnodes),
                    "traversal": {
                        "depth": depth,
                        "max_edges_per_symbol": max_edges_per_symbol,
                        "edges": edges,
                    },
                },
                c,
            )
        finally:
            g.close()
            db.close()


def provenance_impl(
    state: CatalogState,
    record_type: str,
    record_id: int,
) -> CatalogResponse:
    """Retrieve full provenance for a record (file, symbol, relationship, etc.)."""
    queries = {
        "file": (
            "SELECT f.id,r.repo_key,r.tracked_branch,r.indexed_commit_sha,"
            "r.last_scanned_at,r.last_built_at,r.index_status,f.path,f.language,"
            "f.size_bytes,f.sha1,f.last_indexed FROM files f "
            "JOIN repos r ON r.id=f.repo_id WHERE f.id=?"
        ),
        "symbol": (
            "SELECT s.id,s.name,s.kind,s.language,s.start_line,s.end_line,"
            "s.signature,f.id file_id,r.repo_key,r.tracked_branch,"
            "r.indexed_commit_sha,f.path file_path FROM symbols s "
            "JOIN files f ON f.id=s.file_id JOIN repos r ON r.id=f.repo_id "
            "WHERE s.id=?"
        ),
        "relationship": (
            "SELECT rel.id,r.repo_key,r.tracked_branch,r.indexed_commit_sha,"
            "rel.source_symbol_id,rel.target_symbol_id,rel.relationship_type,"
            "rel.file_path,rel.confidence,rel.evidence,rel.resolution_class,"
            "rel.resolution_reason,rel.extractor,rel.created_at "
            "FROM relationships rel JOIN repos r ON r.id=rel.repo_id WHERE rel.id=?"
        ),
        "entity_mapping": (
            "SELECT em.id,r.repo_key,em.mapping_type,em.confidence,"
            "em.source_text,e.name entity_name,eo.ent_file,eo.module,"
            "eo.table_name,eo.view_name,s.id symbol_id,f.path file_path,"
            "s.start_line,s.end_line FROM entity_mappings em "
            "JOIN repos r ON r.id=em.repo_id JOIN entity_nodes e ON e.id=em.entity_id "
            "LEFT JOIN entity_occurrences eo ON eo.repo_id=em.repo_id "
            "AND eo.entity_id=em.entity_id LEFT JOIN symbols s ON s.id=em.symbol_id "
            "LEFT JOIN files f ON f.id=COALESCE(em.file_id,s.file_id) "
            "WHERE em.id=?"
        ),
        "workflow": (
            "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_kind,"
            "w.source_file,w.source_symbol_id,w.confidence,w.reason,"
            "e.name entity_name,eo.ent_file,eo.module,eo.table_name,eo.view_name "
            "FROM workflows w JOIN repos r ON r.id=w.repo_id "
            "JOIN entity_nodes e ON e.id=w.entity_id "
            "LEFT JOIN entity_occurrences eo ON eo.repo_id=w.repo_id "
            "AND eo.entity_id=w.entity_id WHERE w.id=?"
        ),
        "rest_endpoint": (
            "SELECT ep.id,r.repo_key,ep.method,ep.path,e.name entity_name,"
            "eo.ent_file,eo.module,eo.table_name,eo.view_name,"
            "ep.handler_symbol_id,f.path file_path FROM rest_endpoints ep "
            "JOIN repos r ON r.id=ep.repo_id "
            "LEFT JOIN entity_nodes e ON e.id=ep.entity_id "
            "LEFT JOIN entity_occurrences eo ON eo.repo_id=ep.repo_id "
            "AND eo.entity_id=ep.entity_id LEFT JOIN files f ON f.id=ep.file_id "
            "WHERE ep.id=?"
        ),
        "security_operation": (
            "SELECT id,op_key,title,action,source_file,source_line,source_kind,"
            "raw_hash FROM security_operations WHERE id=?"
        ),
    }

    if record_type not in queries:
        raise ValueError(f"invalid record_type: {record_type}")

    with state.conn() as c:
        r = c.execute(queries[record_type], (record_id,)).fetchone()
        if not r:
            return make_error_response(
                state,
                "provenance",
                "record_not_found",
                f"No {record_type} record {record_id}",
                c,
            )

        return make_response(
            state,
            "provenance",
            {"record_type": record_type, "evidence": row_to_dict(r)},
            c,
        )


def repository_list_impl(state: CatalogState) -> CatalogResponse:
    """List all tracked repositories and their branch/revision status."""
    with state.conn() as c:
        repo_columns = {str(row[1]) for row in c.execute("PRAGMA table_info(repos)")}
        lifecycle_fields = (
            ",lifecycle_state,archive_source,archive_reason,archived_at"
            if {"lifecycle_state", "archive_source", "archive_reason", "archived_at"}
            .issubset(repo_columns)
            else ""
        )
        rows = c.execute(
            "SELECT repo_key,tracked_branch,indexed_commit_sha,last_scanned_at,"
            "last_built_at,index_status,diagnostic_error,last_attempt_status,"
            "last_attempted_at,last_attempt_error"
            + lifecycle_fields
            + " FROM repos ORDER BY repo_key"
        ).fetchall()
        repositories = [row_to_dict(row) for row in rows]
        for repository in repositories:
            repository.setdefault("lifecycle_state", "active")
            repository.setdefault("archive_source", None)
            repository.setdefault("archive_reason", None)
            repository.setdefault("archived_at", None)
        return make_response(
            state,
            "repository_list",
            {"repositories": repositories},
            c,
        )


@repository_lifecycle_guard
def ui_impact_impl(
    state: CatalogState,
    entity_name: str,
    repo_key: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Return actionUI and NextGen surfaces linked to one entity."""
    with state.conn() as c:
        try:
            data = query_ui_impact(
                c,
                entity_name=entity_name,
                repo_key=repo_key,
                limit=limit,
                cursor=cursor,
            )
        except UiQueryError as error:
            return make_error_response(
                state,
                "ui_impact",
                error.code,
                str(error),
                c,
                details=dict(error.details),
            )
        return make_response(
            state,
            "ui_impact",
            data,
            c,
            next_cursor=data["page"]["next_cursor"],
        )


@repository_lifecycle_guard
def ui_surface_detail_impl(
    state: CatalogState,
    surface_key: str,
    repo_key: str,
    record_kind: str,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Return one evidence family for an exact actionUI or NextGen surface."""
    with state.conn() as c:
        try:
            data = query_ui_surface_detail(
                c,
                surface_key=surface_key,
                repo_key=repo_key,
                record_kind=record_kind,
                limit=limit,
                cursor=cursor,
            )
        except UiQueryError as error:
            return make_error_response(
                state,
                "ui_surface_detail",
                error.code,
                str(error),
                c,
                details=dict(error.details),
            )
        return make_response(
            state,
            "ui_surface_detail",
            data,
            c,
            next_cursor=data["page"]["next_cursor"],
        )


def catalog_status_impl(state: CatalogState) -> CatalogResponse:
    """Get catalog statistics (row counts per table)."""
    with state.conn() as c:
        counts = {}
        for table in (
            "files",
            "symbols",
            "relationships",
            "entity_nodes",
            "workflows",
            "rest_endpoints",
        ):
            if state.table_exists(c, table):
                counts[table] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        return make_response(state, "catalog_status", {"counts": counts}, c)


SEMANTIC_TABLES = (
    "entity_schema_components",
    "entity_relationship_facts",
    "entity_operation_facts",
    "entity_extraction_coverage",
    "entity_semantic_conflicts",
)


def _semantic_tables_missing(state: CatalogState, c: sqlite3.Connection) -> list[str]:
    return [name for name in SEMANTIC_TABLES if not state.table_exists(c, name)]


def _semantic_capability_error(
    state: CatalogState, operation: str, c: sqlite3.Connection, missing: list[str]
) -> CatalogResponse:
    return make_response(
        state,
        operation,
        {},
        c,
        status="capability_unavailable",
        error={
            "code": "semantic_tables_missing",
            "message": "Apply migration 021_entity_semantics and refresh the repository",
            "details": {"missing_tables": missing},
        },
    )


@repository_lifecycle_guard
def object_relationships_impl(
    state: CatalogState,
    object_name: str,
    repo_key: str | None = None,
    axes: list[str] | None = None,
    direction: str = "both",
    depth: int = 1,
    include: list[str] | None = None,
    confidence_min: float | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> CatalogResponse:
    """Return direct semantic evidence; graph traversal is deliberately opt-in."""
    lim = validate_limit(limit)
    off = decode_cursor(cursor)
    requested_axes = axes or ["A", "B", "C", "D", "E"]
    if any(axis not in {"A", "B", "C", "D", "E"} for axis in requested_axes):
        raise ValueError("axes must contain only A, B, C, D, E")
    if direction not in {"incoming", "outgoing", "both"}:
        raise ValueError("direction must be incoming, outgoing, or both")
    if not 1 <= depth <= 3:
        raise ValueError("depth must be 1..3")
    include = include or [
        "components",
        "relationships",
        "operations",
        "coverage",
        "conflicts",
    ]

    with state.conn() as c:
        missing = _semantic_tables_missing(state, c)
        if missing:
            return _semantic_capability_error(state, "object_relationships", c, missing)
        occurrences = c.execute(
            """SELECT eo.id occurrence_id,en.id entity_id,en.name,r.repo_key,eo.ent_file,
                      eo.module,eo.table_name,eo.view_name
               FROM entity_occurrences eo JOIN entity_nodes en ON en.id=eo.entity_id
               JOIN repos r ON r.id=eo.repo_id
               WHERE en.name=? COLLATE NOCASE AND (? IS NULL OR r.repo_key=?)
               ORDER BY r.repo_key,eo.id""",
            (object_name, repo_key, repo_key),
        ).fetchall()
        if not occurrences:
            return make_error_response(
                state,
                "object_relationships",
                "entity_not_found",
                f"No entity named {object_name}",
                c,
            )
        if repo_key is None and len(occurrences) > 1:
            return make_response(
                state,
                "object_relationships",
                {"candidates": [row_to_dict(row) for row in occurrences]},
                c,
                status="ambiguous",
                error={"code": "ambiguous_entity", "message": "Retry with repo_key"},
            )
        occurrence = occurrences[0]
        occurrence_id = int(occurrence["occurrence_id"])
        repo_id = c.execute(
            "SELECT id FROM repos WHERE repo_key=?", (occurrence["repo_key"],)
        ).fetchone()[0]
        placeholders = ",".join("?" for _ in requested_axes)
        relation_where = [f"rf.axis IN ({placeholders})"]
        relation_args: list[Any] = [*requested_axes]
        if direction == "outgoing":
            relation_where.append("rf.source_occurrence_id=?")
            relation_args.append(occurrence_id)
        elif direction == "incoming":
            relation_where.append("rf.target_occurrence_id=?")
            relation_args.append(occurrence_id)
        else:
            relation_where.append(
                "(rf.source_occurrence_id=? OR rf.target_occurrence_id=?)"
            )
            relation_args.extend([occurrence_id, occurrence_id])
        if confidence_min is not None:
            if not 0 <= confidence_min <= 1:
                raise ValueError("confidence_min must be 0.0..1.0")
            relation_where.append("rf.confidence >= ?")
            relation_args.append(confidence_min)
        relation_sql = (
            "SELECT rf.id,rf.axis,rf.relation_kind,rf.fact_key,rf.assertion_status,"
            "rf.target_entity_name,rf.target_literal,rf.cardinality,rf.qualifiers_json,"
            "rf.confidence,rf.source_path,rf.start_line,rf.end_line,rf.evidence_text,"
            "rf.extractor,rf.extractor_version,src.name source_entity_name,"
            "dst.name target_entity_resolved "
            "FROM entity_relationship_facts rf "
            "JOIN entity_occurrences src_occ ON src_occ.id=rf.source_occurrence_id "
            "JOIN entity_nodes src ON src.id=src_occ.entity_id "
            "LEFT JOIN entity_occurrences dst_occ ON dst_occ.id=rf.target_occurrence_id "
            "LEFT JOIN entity_nodes dst ON dst.id=dst_occ.entity_id "
            "WHERE " + " AND ".join(relation_where) + " ORDER BY rf.id"
        )
        # Axis classification must not depend on the requested page.  Fetch the
        # caller's page separately, then aggregate every matching assertion.
        axis_status_rows = c.execute(
            "SELECT rf.axis,rf.assertion_status,COUNT(*) count FROM "
            "entity_relationship_facts rf WHERE "
            + " AND ".join(relation_where)
            + " GROUP BY rf.axis,rf.assertion_status",
            relation_args,
        ).fetchall()
        rel_rows = c.execute(
            relation_sql + " LIMIT ? OFFSET ?", (*relation_args, lim + 1, off)
        ).fetchall()
        relationships, next_cursor = paginate(rel_rows, lim, off)
        for relationship in relationships:
            relationship["qualifiers"] = json.loads(
                relationship.pop("qualifiers_json") or "{}"
            )

        graph_status = "not_required"
        graph_used = False
        semantic_traversal: list[dict[str, Any]] = []
        if depth > 1:
            if state.graph_active(c):
                from scripts.query_graph import (
                    get_graph_connection,
                    query_semantic_relationship_traversal,
                )

                graph_db, graph = get_graph_connection(str(state.graph_path))
                try:
                    semantic_traversal = query_semantic_relationship_traversal(
                        graph, occurrence_id, requested_axes, depth
                    )
                    graph_status = "fresh"
                    graph_used = True
                except Exception:
                    # An older active graph can predate the semantic node
                    # projection.  Do not substitute unrelated SQLite joins.
                    graph_status = "semantic_projection_unavailable"
                finally:
                    graph.close()
                    graph_db.close()
            else:
                graph_status = "graph_unavailable"

        coverage_rows = c.execute(
            "SELECT declaration_family,status,diagnostic FROM entity_extraction_coverage "
            "WHERE occurrence_id=? AND declaration_family IN (" + placeholders + ") "
            "ORDER BY id DESC",
            (occurrence_id, *requested_axes),
        ).fetchall()
        coverage_by_axis: dict[str, list[str]] = {axis: [] for axis in requested_axes}
        for row in coverage_rows:
            coverage_by_axis[str(row["declaration_family"])].append(str(row["status"]))
        assertions_by_axis: dict[str, list[str]] = {axis: [] for axis in requested_axes}
        for row in axis_status_rows:
            assertions_by_axis[str(row["axis"])].extend(
                [str(row["assertion_status"])] * int(row["count"])
            )

        data: dict[str, Any] = {
            "object": row_to_dict(occurrence),
            "axes": {
                axis: {"status": "NOT_OBSERVED", "facts": []} for axis in requested_axes
            },
            "traversal": {
                "requested_depth": depth,
                "graph_used": graph_used,
                "graph_status": graph_status,
            },
        }
        if depth > 1 and graph_used:
            data["semantic_traversal"] = semantic_traversal
        for relationship in relationships:
            data["axes"][relationship["axis"]]["facts"].append(relationship)
        for axis in requested_axes:
            statuses = assertions_by_axis[axis]
            coverage_statuses = coverage_by_axis[axis]
            if "CONFLICTING" in statuses:
                data["axes"][axis]["status"] = "CONFLICTING"
            elif "CORROBORATED" in statuses:
                data["axes"][axis]["status"] = "CORROBORATED"
            elif "VERIFIED" in statuses:
                data["axes"][axis]["status"] = "VERIFIED"
            elif "UNRESOLVED" in statuses or any(
                state in {"partial", "failed"} for state in coverage_statuses
            ):
                data["axes"][axis]["status"] = "UNRESOLVED"
            # Only a complete or not-applicable declaration family can close
            # an empty axis as NOT_OBSERVED.  Missing coverage is unresolved.
            elif coverage_statuses and all(
                state in {"complete", "not_applicable"} for state in coverage_statuses
            ):
                data["axes"][axis]["status"] = "NOT_OBSERVED"
            else:
                data["axes"][axis]["status"] = "UNRESOLVED"

        if "relationships" in include:
            data["relationships"] = relationships
        if "components" in include:
            data["components"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT id,component_kind,component_path,declared_name,target_literal,"
                    "data_type,cardinality,writeability,properties_json,source_path,start_line,"
                    "end_line,evidence_text,extractor,extractor_version,confidence "
                    "FROM entity_schema_components WHERE occurrence_id=? ORDER BY id",
                    (occurrence_id,),
                )
            ]
            for component in data["components"]:
                component["properties"] = json.loads(
                    component.pop("properties_json") or "{}"
                )
        if "operations" in include:
            data["operations"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT id,axis,operation,surface_kind,availability,invocation_context,"
                    "persistence_scope,standalone,parent_occurrence_id,qualifiers_json,source_path,"
                    "start_line,end_line,evidence_text,confidence FROM entity_operation_facts "
                    "WHERE occurrence_id=? AND axis IN ("
                    + placeholders
                    + ") ORDER BY id",
                    (occurrence_id, *requested_axes),
                )
            ]
            for operation in data["operations"]:
                operation["qualifiers"] = json.loads(
                    operation.pop("qualifiers_json") or "{}"
                )
        if "coverage" in include:
            data["coverage"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT declaration_family,source_path,status,component_count,fact_count,diagnostic,"
                    "extractor,extractor_version FROM entity_extraction_coverage "
                    "WHERE occurrence_id=? ORDER BY id DESC",
                    (occurrence_id,),
                )
            ]
        if "conflicts" in include:
            data["conflicts"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT id,fact_key,conflict_kind,status,reason,resolution_evidence,confidence "
                    "FROM entity_semantic_conflicts WHERE repo_id=? AND fact_key IN "
                    "(SELECT fact_key FROM entity_relationship_facts WHERE source_occurrence_id=? "
                    "OR target_occurrence_id=?) ORDER BY id",
                    (repo_id, occurrence_id, occurrence_id),
                )
            ]
        status = "ok" if depth == 1 or graph_used else "graph_unavailable"
        error = (
            None
            if depth == 1 or graph_used
            else {
                "code": "graph_stale_or_unprojected",
                "message": "Semantic multi-hop traversal requires a fresh Ladybug semantic projection",
            }
        )
        return make_response(
            state,
            "object_relationships",
            data,
            c,
            status=status,
            error=error,
            next_cursor=next_cursor,
        )


@repository_lifecycle_guard
def qa_impact_impl(
    state: CatalogState,
    changes: list[dict[str, Any]],
    repo_key: str,
    axes: list[str] | None = None,
    depth: int = 1,
    include_tests: bool = True,
) -> CatalogResponse:
    """Return direct evidence surfaces for a set of changed repository files."""
    if not changes:
        raise ValueError("changes must not be empty")
    if not 1 <= depth <= 3:
        raise ValueError("depth must be 1..3")
    requested_axes = axes or ["A", "B", "C", "D", "E"]
    if any(axis not in {"A", "B", "C", "D", "E"} for axis in requested_axes):
        raise ValueError("axes must contain only A, B, C, D, E")
    with state.conn() as c:
        missing = _semantic_tables_missing(state, c)
        if missing:
            return _semantic_capability_error(state, "qa_impact", c, missing)
        repo = c.execute(
            "SELECT id FROM repos WHERE repo_key=?", (repo_key,)
        ).fetchone()
        if not repo:
            return make_error_response(
                state,
                "qa_impact",
                "repo_not_found",
                f"Unknown repository {repo_key}",
                c,
            )
        repo_id = int(repo[0])
        paths = [str(change.get("file_path") or "") for change in changes]
        if any(not path for path in paths):
            raise ValueError("each change requires file_path")
        input_resolution: list[dict[str, Any]] = []

        for path in paths:
            path_sources: dict[int, set[str]] = {}

            def record_seed(occurrence_id: int, source: str) -> None:
                path_sources.setdefault(occurrence_id, set()).add(source)

            for row in c.execute(
                "SELECT id FROM entity_occurrences "
                "WHERE repo_id=? AND ent_file=? ORDER BY id",
                (repo_id, path),
            ):
                record_seed(int(row[0]), "ent_file")

            file_row = c.execute(
                "SELECT id FROM files WHERE repo_id=? AND path=?",
                (repo_id, path),
            ).fetchone()
            if file_row is not None:
                file_id = int(file_row[0])
                for row in c.execute(
                    "SELECT DISTINCT eo.id FROM entity_mappings em "
                    "JOIN entity_occurrences eo "
                    "ON eo.repo_id=em.repo_id AND eo.entity_id=em.entity_id "
                    "WHERE em.repo_id=? AND em.file_id=? ORDER BY eo.id",
                    (repo_id, file_id),
                ):
                    record_seed(int(row[0]), "entity_mapping_file")
                for row in c.execute(
                    "SELECT DISTINCT eo.id FROM entity_mappings em "
                    "JOIN symbols s ON s.id=em.symbol_id "
                    "JOIN entity_occurrences eo "
                    "ON eo.repo_id=em.repo_id AND eo.entity_id=em.entity_id "
                    "WHERE em.repo_id=? AND s.file_id=? ORDER BY eo.id",
                    (repo_id, file_id),
                ):
                    record_seed(int(row[0]), "entity_mapping_symbol")
                for row in c.execute(
                    "SELECT DISTINCT eo.id FROM entity_access_links eal "
                    "JOIN entity_occurrences eo "
                    "ON eo.repo_id=eal.repo_id AND eo.entity_id=eal.entity_id "
                    "WHERE eal.repo_id=? AND eal.evidence_file_id=? ORDER BY eo.id",
                    (repo_id, file_id),
                ):
                    record_seed(int(row[0]), "entity_access_evidence")

            resolved_ids = sorted(path_sources)
            seed_sources = sorted(
                {
                    source
                    for occurrence_id in resolved_ids
                    for source in path_sources[occurrence_id]
                }
            )
            input_resolution.append(
                {
                    "file_path": path,
                    "status": "resolved" if resolved_ids else "unresolved",
                    "seed_sources": seed_sources,
                    "occurrence_ids": resolved_ids,
                    "diagnostic": (
                        None
                        if resolved_ids
                        else "No repository-scoped entity occurrence mapping was observed"
                    ),
                }
            )

        occurrence_ids = sorted(
            {
                occurrence_id
                for item in input_resolution
                for occurrence_id in item["occurrence_ids"]
            }
        )
        if occurrence_ids:
            occurrence_placeholders = ",".join("?" for _ in occurrence_ids)
            occurrences = c.execute(
                """SELECT eo.id occurrence_id,en.id entity_id,en.name,eo.ent_file,
                          eo.module,eo.table_name,eo.view_name
                   FROM entity_occurrences eo
                   JOIN entity_nodes en ON en.id=eo.entity_id
                   WHERE eo.repo_id=? AND eo.id IN ("""
                + occurrence_placeholders
                + ") ORDER BY eo.id",
                (repo_id, *occurrence_ids),
            ).fetchall()
        else:
            occurrences = []
        data: dict[str, Any] = {
            "changes": changes,
            "input_resolution": input_resolution,
            "seed_entity_occurrences": [row_to_dict(row) for row in occurrences],
            "semantic_relationships": [],
            "semantic_operations": [],
            "components": [],
            "surfaces": {
                "mappings": [],
                "rest_endpoints": [],
                "workflows": [],
                "db_tables": [],
                "tests": [],
            },
            "coverage_gaps": [],
            "semantic_coverage": [],
            "conflicts": [],
            "impacted_components": [],
            "traversal": {
                "requested_depth": depth,
                "graph_used": False,
                "graph_status": "not_required" if depth == 1 else "graph_unavailable",
            },
        }
        for resolution in input_resolution:
            if resolution["status"] == "unresolved":
                data["coverage_gaps"].append(
                    {
                        "file_path": resolution["file_path"],
                        "kind": "investigation_gap",
                        "reason": resolution["diagnostic"],
                    }
                )
        if occurrence_ids:
            ids = ",".join("?" for _ in occurrence_ids)
            axis_placeholders = ",".join("?" for _ in requested_axes)
            data["semantic_relationships"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT id,axis,relation_kind,fact_key,assertion_status,target_entity_name,"
                    "target_literal,confidence,source_path,start_line,end_line,evidence_text "
                    f"FROM entity_relationship_facts WHERE source_occurrence_id IN ({ids}) "
                    f"AND axis IN ({axis_placeholders}) ORDER BY id",
                    (*occurrence_ids, *requested_axes),
                )
            ]
            data["semantic_operations"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT id,axis,operation,surface_kind,availability,invocation_context,"
                    "persistence_scope,standalone,parent_occurrence_id,confidence,source_path,"
                    "start_line,end_line,evidence_text FROM entity_operation_facts "
                    f"WHERE occurrence_id IN ({ids}) AND axis IN ({axis_placeholders}) ORDER BY id",
                    (*occurrence_ids, *requested_axes),
                )
            ]
            data["components"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT id,component_kind,component_path,declared_name,target_literal,"
                    "source_path,start_line,end_line,evidence_text,confidence "
                    f"FROM entity_schema_components WHERE occurrence_id IN ({ids}) ORDER BY id",
                    occurrence_ids,
                )
            ]
            data["semantic_coverage"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT eo.id occurrence_id,en.name entity_name,ec.declaration_family,"
                    "ec.status,ec.diagnostic,ec.source_path,ec.component_count,ec.fact_count "
                    "FROM entity_extraction_coverage ec "
                    "JOIN entity_occurrences eo ON eo.id=ec.occurrence_id "
                    "JOIN entity_nodes en ON en.id=eo.entity_id "
                    f"WHERE ec.occurrence_id IN ({ids}) "
                    f"AND ec.declaration_family IN ({axis_placeholders}) "
                    "ORDER BY eo.id,ec.declaration_family,ec.id DESC",
                    (*occurrence_ids, *requested_axes),
                )
            ]
            data["conflicts"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT esc.id,esc.fact_key,esc.conflict_kind,esc.status,esc.reason,"
                    "esc.resolution_evidence,esc.confidence "
                    "FROM entity_semantic_conflicts esc WHERE esc.repo_id=? AND esc.fact_key IN ("
                    f"SELECT fact_key FROM entity_relationship_facts WHERE source_occurrence_id IN ({ids})"
                    ") ORDER BY esc.id",
                    (repo_id, *occurrence_ids),
                )
            ]
            entity_ids = [int(row["entity_id"]) for row in occurrences]
            entity_placeholders = ",".join("?" for _ in entity_ids)
            data["surfaces"]["mappings"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT en.name entity_name,em.mapping_type,f.path file_path,s.name symbol_name,"
                    "s.start_line,s.end_line,em.confidence FROM entity_mappings em "
                    "JOIN entity_nodes en ON en.id=em.entity_id LEFT JOIN files f ON f.id=em.file_id "
                    "LEFT JOIN symbols s ON s.id=em.symbol_id WHERE em.repo_id=? AND em.entity_id IN ("
                    + entity_placeholders
                    + ") ORDER BY em.id",
                    (repo_id, *entity_ids),
                )
            ]
            data["surfaces"]["rest_endpoints"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT en.name entity_name,re.method,re.path,re.source_version,f.path file_path "
                    "FROM rest_endpoints re JOIN entity_nodes en ON en.id=re.entity_id "
                    "LEFT JOIN files f ON f.id=re.file_id WHERE re.repo_id=? AND re.entity_id IN ("
                    + entity_placeholders
                    + ") ORDER BY re.id",
                    (repo_id, *entity_ids),
                )
            ]
            data["surfaces"]["workflows"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT en.name entity_name,w.name,w.workflow_type,w.source_kind,w.source_file,w.confidence "
                    "FROM workflows w JOIN entity_nodes en ON en.id=w.entity_id WHERE w.repo_id=? AND w.entity_id IN ("
                    + entity_placeholders
                    + ") ORDER BY w.id",
                    (repo_id, *entity_ids),
                )
            ]
            data["surfaces"]["db_tables"] = [
                row_to_dict(row)
                for row in c.execute(
                    "SELECT DISTINCT en.name entity_name,dt.id dbschema_table_id,dt.table_name,"
                    "dt.source_file,dt.source_line FROM entity_access_links eal "
                    "JOIN entity_nodes en ON en.id=eal.entity_id "
                    "JOIN dbschema_tables dt ON dt.id=eal.record_id "
                    "WHERE eal.repo_id=? AND eal.surface='dbschema_table' "
                    "AND eal.entity_id IN (" + entity_placeholders + ") ORDER BY dt.id",
                    (repo_id, *entity_ids),
                )
            ]
            if include_tests:
                data["surfaces"]["tests"] = [
                    row_to_dict(row)
                    for row in c.execute(
                        "SELECT en.name entity_name,tc.id test_case_id,tc.feature_name,tc.scenario_name,"
                        "tc.eligibility,tc.scenario_line FROM test_entity_links tel "
                        "JOIN entity_nodes en ON en.id=tel.entity_id JOIN test_requests tr ON tr.id=tel.test_request_id "
                        "JOIN test_cases tc ON tc.id=tr.test_case_id "
                        "WHERE tc.repo_id=? AND tel.entity_id IN ("
                        + entity_placeholders
                        + ") ORDER BY tc.id",
                        (repo_id, *entity_ids),
                    )
                ]
            for occurrence in occurrences:
                entity_id = int(occurrence["entity_id"])
                count = c.execute(
                    "SELECT COUNT(*) FROM test_entity_links tel "
                    "JOIN test_requests tr ON tr.id=tel.test_request_id "
                    "JOIN test_cases tc ON tc.id=tr.test_case_id "
                    "WHERE tel.entity_id=? AND tc.repo_id=?",
                    (entity_id, repo_id),
                ).fetchone()[0]
                if not count:
                    data["coverage_gaps"].append(
                        {
                            "entity_name": occurrence["name"],
                            "kind": "investigation_gap",
                            "reason": "No linked test requests were observed; no BDD scenario is inferred.",
                        }
                    )
        # Risk ranking is a deterministic triage signal, not a claim that a
        # behavioral regression will occur.  Each item retains the extracted
        # evidence that caused it to be surfaced.
        impact_rows: list[dict[str, Any]] = []
        for row in data["semantic_relationships"]:
            impact_rows.append(
                {
                    "risk": "high",
                    "component_type": "semantic_relationship",
                    "reason": f"Axis {row['axis']} relationship can change object semantics",
                    "evidence": row,
                }
            )
        for row in data["semantic_operations"]:
            impact_rows.append(
                {
                    "risk": "high"
                    if row["operation"] in {"create", "update", "delete"}
                    else "medium",
                    "component_type": "semantic_operation",
                    "reason": "Operation behavior is declared for the changed object",
                    "evidence": row,
                }
            )
        for surface_name in ("rest_endpoints", "workflows"):
            for row in data["surfaces"][surface_name]:
                impact_rows.append(
                    {
                        "risk": "high",
                        "component_type": surface_name,
                        "reason": f"Linked {surface_name.rstrip('s')} surface",
                        "evidence": row,
                    }
                )
        for surface_name in ("mappings", "db_tables"):
            for row in data["surfaces"][surface_name]:
                impact_rows.append(
                    {
                        "risk": "medium",
                        "component_type": surface_name,
                        "reason": f"Linked {surface_name.rstrip('s')} surface",
                        "evidence": row,
                    }
                )
        for row in data["semantic_coverage"]:
            if row["status"] in {"partial", "failed"}:
                impact_rows.append(
                    {
                        "risk": "unresolved",
                        "component_type": "extraction_coverage",
                        "reason": row["diagnostic"],
                        "evidence": row,
                    }
                )
        risk_order = {"high": 0, "medium": 1, "low": 2, "unresolved": 3}
        data["impacted_components"] = sorted(
            impact_rows,
            key=lambda row: (
                risk_order[row["risk"]],
                row["component_type"],
                str(row["evidence"]),
            ),
        )
        graph_used = False
        graph_status = "not_required"
        if depth > 1:
            if state.graph_active(c):
                from scripts.query_graph import (
                    get_graph_connection,
                    query_semantic_relationship_traversal,
                )

                graph_db, graph = get_graph_connection(str(state.graph_path))
                try:
                    data["semantic_traversal"] = [
                        fact
                        for occurrence_id in occurrence_ids
                        for fact in query_semantic_relationship_traversal(
                            graph, occurrence_id, requested_axes, depth
                        )
                    ]
                    graph_used = True
                    graph_status = "fresh"
                except Exception:
                    graph_status = "semantic_projection_unavailable"
                finally:
                    graph.close()
                    graph_db.close()
            else:
                graph_status = "graph_unavailable"
        data["traversal"]["graph_used"] = graph_used
        data["traversal"]["graph_status"] = graph_status
        status = "ok" if depth == 1 or graph_used else "graph_unavailable"
        error = (
            None
            if depth == 1 or graph_used
            else {
                "code": "graph_stale_or_unprojected",
                "message": "Semantic multi-hop traversal requires a fresh Ladybug semantic projection",
            }
        )
        return make_response(state, "qa_impact", data, c, status=status, error=error)


class Catalog:
    """Compatibility wrapper exposing class-based catalog methods for tests/legacy callers."""

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        graph_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.state = CatalogState(
            db_path=Path(db_path or os.getenv("CATALOG_DB", CATALOG_DB)).resolve(),
            graph_path=Path(graph_path or os.getenv("GRAPH_DB", GRAPH_DB)).resolve(),
        )

    def entity(
        self,
        name: str,
        repo_key: str | None = None,
    ) -> CatalogResponse:
        return entity_context_impl(self.state, name, repo_key)

    def repositories(self) -> CatalogResponse:
        return repository_list_impl(self.state)

    def coverage(
        self,
        name: str,
        version: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> CatalogResponse:
        response = rest_coverage_impl(self.state, name, version, limit)
        # Preserve legacy class API contract expected by validation tests.
        error = response.get("error") or {}
        if error.get("code") == "entity_not_found":
            response["status"] = "not_found"
        return response


# ============================================================================
# Server Setup
# ============================================================================


def create_server(
    db_path: str | None = None,
    graph_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8010,
) -> tuple[FastMCP, CatalogState]:
    """Create and configure the FastMCP server.

    Returns a tuple of (FastMCP instance, CatalogState) for lifecycle management.
    """
    state = CatalogState(
        db_path=Path(db_path or os.getenv("CATALOG_DB", CATALOG_DB)).resolve(),
        graph_path=Path(graph_path or os.getenv("GRAPH_DB", GRAPH_DB)).resolve(),
    )

    mcp = FastMCP(
        name="intacct_catalog",
        instructions=(
            "Read-only evidence-first catalog. Call repository_list to discover "
            "repo_key values. Use repository-relative file paths from "
            "catalog_search, never absolute paths. Follow input enums and numeric "
            "bounds exactly; reuse page.next_cursor unchanged. Every result uses "
            "the CatalogResponse v1 envelope: inspect status and error before "
            "using data. Cite returned source paths, line ranges, record IDs, and "
            "confidence; do not infer missing evidence."
        ),
        host=host,
        port=port,
    )

    # Register tools with factory closures to bind state
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def catalog_search(
        query: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Case-insensitive name or path fragment. Use a specific kind "
                    "when pagination may be needed."
                ),
                examples=["ARInvoiceManager", "APBill"],
            ),
        ],
        kind: Annotated[
            Literal["all", "entity", "file", "symbol", "api", "workflow", "security"],
            Field(
                description=(
                    "Catalog record family to search. 'all' searches every family "
                    "but does not accept cursor and errors if the combined result "
                    "exceeds limit; choose one kind for pagination."
                )
            ),
        ] = "all",
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Discover exact catalog names, IDs, and repository-relative paths before calling narrower tools."""
        return catalog_search_impl(state, query, kind, limit, cursor, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def entity_context(
        entity_name: EntityName,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Return an entity's repository occurrences, code mappings, root symbols, workflows, and REST endpoints."""
        return entity_context_impl(state, entity_name, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def ui_impact(
        entity_name: EntityName,
        repo_key: RepositoryKey,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return actionUI and NextGen screens linked by direct or supported UI roles."""
        return ui_impact_impl(state, entity_name, repo_key, limit, cursor)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def ui_surface_detail(
        surface_key: UiSurfaceKey,
        repo_key: RepositoryKey,
        record_kind: UiDetailRecordKind,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return one paged evidence family for an exact actionUI or NextGen surface."""
        return ui_surface_detail_impl(
            state, surface_key, repo_key, record_kind, limit, cursor
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def object_relationships(
        object_name: EntityName,
        repo_key: RepositoryKey | None = None,
        axes: Annotated[
            list[SemanticAxis],
            Field(
                min_length=1,
                description="Semantic axes to query; omit to query A, B, C, D, and E.",
            ),
        ]
        | None = None,
        direction: Annotated[
            Literal["incoming", "outgoing", "both"],
            Field(description="Relationship direction relative to object_name."),
        ] = "both",
        depth: SemanticTraversalDepth = 1,
        include: Annotated[
            list[
                Literal[
                    "components",
                    "relationships",
                    "operations",
                    "coverage",
                    "conflicts",
                ]
            ],
            Field(
                min_length=1,
                description=(
                    "Response sections to include; omit to include all five sections."
                ),
            ),
        ]
        | None = None,
        confidence_min: ConfidenceScore | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Query provenance-backed ownership, hierarchy, visibility, and entity-context facts for one entity."""
        return object_relationships_impl(
            state,
            object_name,
            repo_key,
            axes,
            direction,
            depth,
            include,
            confidence_min,
            limit,
            cursor,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def qa_impact(
        changes: Annotated[
            list[QaChange],
            Field(
                min_length=1,
                description=(
                    "Changed files. Every item must contain file_path using the "
                    "exact repository-relative catalog path."
                ),
                examples=[[{"file_path": ("app/source/apar/ARInvoiceManager.cls")}]],
            ),
        ],
        repo_key: RepositoryKey,
        axes: Annotated[
            list[SemanticAxis],
            Field(
                min_length=1,
                description="Semantic axes to assess; omit to assess all five axes.",
            ),
        ]
        | None = None,
        depth: SemanticTraversalDepth = 1,
        include_tests: Annotated[
            bool,
            Field(
                description=(
                    "When true, include linked Gherkin tests and explicit test "
                    "coverage gaps."
                )
            ),
        ] = True,
    ) -> CatalogResponse:
        """Assess evidence-backed semantic, API, workflow, database, and test surfaces for changed files in one repository."""
        return qa_impact_impl(state, changes, repo_key, axes, depth, include_tests)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def rest_coverage(
        entity_name: EntityName,
        version: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Optional REST source version. Current catalog examples are "
                    "'s1' and 's2'; omit to include all versions."
                ),
                examples=["s1", "s2"],
            ),
        ]
        | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
    ) -> CatalogResponse:
        """Compare an entity's REST endpoints with linked Gherkin requests and report covered and uncovered endpoints."""
        return rest_coverage_impl(state, entity_name, version, limit)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def entity_test_coverage(
        entity_name: EntityName,
        workflow_name: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Exact workflow name, matched case-insensitively. When set, "
                    "return scenarios containing a workflow request step."
                ),
                examples=["approve"],
            ),
        ]
        | None = None,
        eligibility: Eligibility | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return linked Gherkin scenarios, Jira references, eligibility, feature paths, lines, and ordered HTTP steps."""
        return entity_test_coverage_impl(
            state, entity_name, workflow_name, eligibility, limit, cursor
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def relationship_query(
        name: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Exact symbol name. It is matched against source_name for "
                    "outgoing queries and target_name for incoming queries."
                ),
                examples=["ARInvoiceManager"],
            ),
        ],
        direction: Annotated[
            Literal["outgoing", "incoming"],
            Field(description="Direction relative to name; 'both' is not valid."),
        ] = "outgoing",
        resolution_classes: Annotated[
            list[RelationshipResolutionClass],
            Field(
                min_length=1,
                description="Only return relationships in these resolution classes.",
            ),
        ]
        | None = None,
        confidence_min: ConfidenceScore | None = None,
        confidence_max: ConfidenceScore | None = None,
        repo_key: RepositoryKey | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return direct extracted relationships for an exact source or target symbol name; call twice for both directions."""
        return relationship_query_impl(
            state,
            name,
            direction,
            resolution_classes,
            confidence_min,
            confidence_max,
            repo_key,
            limit,
            cursor,
        )

    # Phase 1: Dependency Surface Tools
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def workflow_structure(
        entity_name: EntityName,
        workflow_id: Annotated[
            int,
            Field(
                ge=1,
                description=(
                    "Exact workflow record ID. Omit to return every workflow for "
                    "the entity."
                ),
                examples=[907],
            ),
        ]
        | None = None,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Return workflow records plus their ordered nodes and edges for an entity."""
        return workflow_structure_impl(state, entity_name, workflow_id, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def entity_access_detail(
        entity_name: EntityName,
        surface_type: AccessSurface | None = None,
        repo_key: RepositoryKey | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return an entity's links to workflow, REST, security, and database surfaces with evidence IDs."""
        return entity_access_detail_impl(
            state, entity_name, surface_type, repo_key, limit, cursor
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def security_dependency_chain(
        op_key: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Exact security operation key. Use security_surface to "
                    "discover keys."
                ),
                examples=["ee/lists/employee"],
            ),
        ],
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Return an exact security operation with allowed operations, granting policies, and menu references."""
        return security_dependency_chain_impl(state, op_key, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def openapi_file_dependencies(
        file_path: CatalogFilePath,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Return incoming and outgoing OpenAPI reference edges for one exact catalog file."""
        return openapi_file_dependencies_impl(state, file_path, repo_key)

    # Phase 2: Risk Surface Tools
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def catalog_risk_summary() -> CatalogResponse:
        """Return aggregate catalog-quality signals and the category names accepted by risk_detail."""
        return catalog_risk_summary_impl(state)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def risk_detail(
        category: RiskCategory,
        entity_name: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Optional exact entity name, matched case-insensitively. "
                    "Applied only when category='entity_mapping_gaps'."
                ),
                examples=["APBill"],
            ),
        ]
        | None = None,
        symbol_name: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Reserved symbol-name filter. The current implementation "
                    "does not apply this filter."
                ),
            ),
        ]
        | None = None,
        repo_key: RepositoryKey | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return paginated evidence rows for one category from catalog_risk_summary."""
        return risk_detail_impl(
            state, category, entity_name, symbol_name, repo_key, limit, cursor
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def confidence_band_query(
        category: ConfidenceCategory,
        confidence_min: ConfidenceScore,
        confidence_max: ConfidenceScore,
        repo_key: RepositoryKey | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Return records whose confidence or entity-root weight falls in an inclusive 0.0..1.0 band."""
        return confidence_band_query_impl(
            state, category, confidence_min, confidence_max, repo_key, limit, cursor
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def api_surface(
        entity_name: EntityName | None = None,
        path_fragment: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Case-sensitive substring of a REST endpoint path. At least "
                    "one of entity_name or path_fragment is required."
                ),
                examples=["/objects/accounts-payable/bill"],
            ),
        ]
        | None = None,
        repo_key: RepositoryKey | None = None,
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
    ) -> CatalogResponse:
        """Find REST endpoints by exact entity name, endpoint-path fragment, or both."""
        return api_surface_impl(
            state, entity_name, path_fragment, repo_key, limit, cursor
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def workflow_context(
        entity_name: EntityName,
        workflow_type: WorkflowType | None = None,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Return atomic workflow/action records for an exact entity, optionally filtered by workflow type."""
        return workflow_context_impl(state, entity_name, workflow_type, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def security_surface(
        key_fragment: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Case-insensitive substring matched against operation key "
                    "and title."
                ),
                examples=["employee"],
            ),
        ],
        limit: ResultLimit = DEFAULT_LIMIT,
        cursor: PaginationCursor | None = None,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Discover security operation IDs and exact keys by key or title fragment."""
        return security_surface_impl(state, key_fragment, limit, cursor, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def symbol_references(
        symbol_name: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Exact symbol name. If it resolves to multiple symbols, the "
                    "response is ambiguous and returns candidates; retry with "
                    "symbol_id."
                ),
                examples=["create"],
            ),
        ]
        | None = None,
        symbol_id: Annotated[
            int,
            Field(
                ge=1,
                description=(
                    "Exact symbol record ID from catalog_search or an ambiguous "
                    "symbol_references response. Takes precedence over symbol_name."
                ),
                examples=[6361],
            ),
        ]
        | None = None,
        repo_key: RepositoryKey | None = None,
    ) -> CatalogResponse:
        """Find graph callers and referencers for one symbol; provide symbol_name or the preferred unambiguous symbol_id."""
        return symbol_references_impl(state, symbol_name, symbol_id, repo_key)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def file_impact(
        file_path: CatalogFilePath,
        repo_key: RepositoryKey | None = None,
        depth: GraphTraversalDepth = 1,
        max_edges_per_symbol: Annotated[
            int,
            Field(
                ge=1,
                le=1000,
                description=(
                    "Maximum incoming graph edges expanded per symbol at each "
                    "depth; valid range is 1..1000."
                ),
                examples=[25],
            ),
        ] = 25,
    ) -> CatalogResponse:
        """Traverse incoming graph references from every symbol in one exact repository-relative file."""
        return file_impact_impl(state, file_path, repo_key, depth, max_edges_per_symbol)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def provenance(
        record_type: Annotated[
            Literal[
                "file",
                "symbol",
                "relationship",
                "entity_mapping",
                "workflow",
                "rest_endpoint",
                "security_operation",
            ],
            Field(
                description=(
                    "Catalog table family that owns record_id. Use an ID returned "
                    "by another catalog tool."
                )
            ),
        ],
        record_id: Annotated[
            int,
            Field(
                ge=1,
                description="Exact catalog record ID returned by another tool.",
                examples=[6361],
            ),
        ],
    ) -> CatalogResponse:
        """Resolve one catalog record ID to its repository revision and source evidence."""
        return provenance_impl(state, record_type, record_id)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def repository_list() -> CatalogResponse:
        """Discover valid repo_key values and each repository's indexed branch, commit, and health status."""
        return repository_list_impl(state)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    def catalog_status() -> CatalogResponse:
        """Return high-level row counts for the active SQLite catalog."""
        return catalog_status_impl(state)

    # Register resources
    @mcp.resource("catalog://schema")
    def schema() -> str:
        """Return the catalog database schema."""
        return (
            Path(__file__)
            .resolve()
            .parent.parent.joinpath("catalog/schema.sql")
            .read_text()
        )

    @mcp.resource("catalog://snapshot")
    def snapshot() -> str:
        """Return current catalog snapshot."""
        with state.conn() as c:
            return str(state.snapshot(c))

    return mcp, state


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8010"))
    host = os.getenv("MCP_HOST", "127.0.0.1")
    mcp, state = create_server(port=port, host=host)
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be stdio, sse, or streamable-http")

    mcp.run(transport=transport)
