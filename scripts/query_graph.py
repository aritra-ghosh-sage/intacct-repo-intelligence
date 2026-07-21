#!/usr/bin/env python3
"""
Query interface for Ladybug graph database.

Phase 2: Hybrid Cypher + SQLite queries for file-change-impact analysis.

Commands:
  - file-impact <path>        : Analyze what entities/surfaces are affected by changes to a file
  - entity-context <name>     : Get full context for an entity (symbols, surfaces, database mappings)
  - who-uses <symbol>         : Find all code that uses/references a symbol
  - security-surface <entity> : Analyze security surface of an entity (ops, policies, menus)
"""
from __future__ import annotations

import os
import sqlite3
import sys
from functools import wraps
from pathlib import Path
from typing import Any

import click
import ladybug as lb

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    from ._query_json import emit_json, error_response, success_response
except ImportError:
    from _query_json import emit_json, error_response, success_response

from config import CATALOG_DB as SQLITE_DB, GRAPH_DB

DEFAULT_DB = os.environ.get("CATALOG_DB", SQLITE_DB)
DEFAULT_GRAPH = os.environ.get("GRAPH_DB", GRAPH_DB)

_graph_connection_cache: dict[str, tuple[lb.Database, lb.Connection]] = {}


def graph_error_boundary(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except click.ClickException:
            raise
        except Exception as exc:
            ctx = click.get_current_context()
            command = ctx.command.name or func.__name__
            params = {k: v for k, v in ctx.params.items() if k not in {"db", "graph", "json_output"}}
            if kwargs.get("json_output", False):
                emit_json(error_response(command=command, args=params, code="graph_query_failed", message=str(exc), details={"exception_type": type(exc).__name__}))
                return
            raise click.ClickException(f"Graph query failed: {exc}") from exc
    return wrapped


def get_graph_connection(graph_db_path: str) -> tuple[lb.Database, lb.Connection]:
    """Open a read-only Ladybug graph database connection."""
    if graph_db_path not in _graph_connection_cache:
        db = lb.Database(graph_db_path, read_only=True)
        _graph_connection_cache[graph_db_path] = (db, lb.Connection(db))
    return _graph_connection_cache[graph_db_path]


def enrich_symbols_from_sql(
    sql_conn: sqlite3.Connection,
    graph_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Batch-enrich graph symbols while preserving graph result order."""
    symbol_ids = [
        int(result["symbol_id"])
        for result in graph_results
        if result.get("symbol_id") is not None
    ]
    metadata_by_id: dict[int, sqlite3.Row] = {}
    for offset in range(0, len(symbol_ids), 900):
        chunk = symbol_ids[offset : offset + 900]
        placeholders = ",".join("?" for _ in chunk)
        rows = sql_conn.execute(
            f"""
            SELECT s.id, r.repo_key, f.path, s.start_line, s.end_line, s.signature
            FROM symbols s
            LEFT JOIN files f ON f.id = s.file_id
            LEFT JOIN repos r ON r.id = f.repo_id
            WHERE s.id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        metadata_by_id.update({int(row[0]): row for row in rows})

    enriched = []
    for graph_result in graph_results:
        result = dict(graph_result)
        symbol_id = result.get("symbol_id")
        metadata = metadata_by_id.get(int(symbol_id)) if symbol_id is not None else None
        if metadata is not None:
            result.update(
                {
                    "repo_key": metadata[1],
                    "file_path": metadata[2],
                    "start_line": metadata[3],
                    "end_line": metadata[4],
                    "signature": metadata[5],
                }
            )
        enriched.append(result)
    return enriched


@click.group()
def cli() -> None:
    """Query the Ladybug graph database with hybrid Cypher + SQLite enrichment."""
    pass


# ============================================================================
# Command 1: file-impact
# ============================================================================


def _query_file_symbols_from_graph(
    graph_conn: lb.Connection, file_path: str, repo_key: str | None = None
) -> list[dict[str, Any]]:
    """Query graph for all symbols declared in a file."""
    query = """
    MATCH (f:File {path: $file_path})-[r:DECLARED_IN]-(s:Symbol)
    WHERE $repo_key IS NULL OR f.repo_key = $repo_key
    RETURN 
        s.symbol_id AS symbol_id,
        s.name AS name,
        s.kind AS kind
    ORDER BY s.name, s.symbol_id
    """
    results = graph_conn.execute(query, {"file_path": file_path, "repo_key": repo_key})
    rows = results.get_all()
    return [
        {"symbol_id": row[0], "name": row[1], "kind": row[2]} for row in rows
    ]


def _query_entities_from_symbols(
    graph_conn: lb.Connection, symbol_ids: list[int]
) -> list[dict[str, Any]]:
    """Batch-query exact entity mappings for the supplied symbols."""
    if not symbol_ids:
        return []
    query = """
    MATCH (s:Symbol)-[:ENTITY_MAPPING]-(e:Entity)
    WHERE s.symbol_id IN $symbol_ids
    RETURN DISTINCT e.entity_id, e.name, e.entity_type, e.module
    ORDER BY e.entity_id
    """
    rows = graph_conn.execute(
        query, {"symbol_ids": sorted(set(symbol_ids))}
    ).get_all()
    return [
        {
            "entity_id": row[0],
            "name": row[1],
            "entity_type": row[2],
            "module": row[3],
        }
        for row in rows
    ]


def _query_surfaces_from_entities(
    graph_conn: lb.Connection, entity_ids: list[int]
) -> dict[str, list[dict[str, Any]]]:
    """Batch-query evidence-backed surfaces for the supplied entities."""
    empty = {
        "rest_endpoints": [],
        "workflows": [],
        "security_ops": [],
        "security_menus": [],
    }
    if not entity_ids:
        return empty
    params = {"entity_ids": sorted(set(entity_ids))}
    queries = {
        "rest_endpoints": (
            """
            MATCH (re:RestEndpoint)-[:EXPOSES_ENTITY]->(e:Entity)
            WHERE e.entity_id IN $entity_ids
            RETURN DISTINCT re.rest_endpoint_id, re.path, re.method
            ORDER BY re.rest_endpoint_id
            """,
            lambda row: {
                "rest_endpoint_id": row[0], "path": row[1], "method": row[2]
            },
        ),
        "workflows": (
            """
            MATCH (e:Entity)-[:HAS_WORKFLOW]->(wf:Workflow)
            WHERE e.entity_id IN $entity_ids
            RETURN DISTINCT wf.workflow_id, wf.name, wf.workflow_type
            ORDER BY wf.workflow_id
            """,
            lambda row: {
                "workflow_id": row[0], "name": row[1], "workflow_type": row[2]
            },
        ),
        "security_ops": (
            """
            MATCH (e:Entity)<-[:ENTITY_ACCESS_LINK_ENTITY]-(l:EntityAccessLink)
                  -[:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->(so:SecurityOperation)
            WHERE e.entity_id IN $entity_ids
            RETURN DISTINCT so.security_operation_id, so.op_key, so.title
            ORDER BY so.security_operation_id
            """,
            lambda row: {
                "security_operation_id": row[0], "op_key": row[1], "title": row[2]
            },
        ),
        "security_menus": (
            """
            MATCH (e:Entity)<-[:ENTITY_ACCESS_LINK_ENTITY]-(l:EntityAccessLink)
                  -[:ENTITY_ACCESS_LINK_SECURITY_MENU]->(sm:SecurityMenu)
            WHERE e.entity_id IN $entity_ids
            RETURN DISTINCT sm.security_menu_id, sm.menu_name
            ORDER BY sm.security_menu_id
            """,
            lambda row: {"security_menu_id": row[0], "menu_name": row[1]},
        ),
    }
    results = {}
    for key, (query, convert) in queries.items():
        results[key] = [
            convert(row) for row in graph_conn.execute(query, params).get_all()
        ]
    return results


def _query_bounded_incoming_traversal(
    graph_conn: lb.Connection,
    seed_ids: list[int],
    depth: int,
    max_edges_per_symbol: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Traverse incoming dependencies with one deterministic budget per target."""
    discovered_depth = {int(symbol_id): 0 for symbol_id in seed_ids}
    frontier = set(discovered_depth)
    nodes: dict[int, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for level in range(1, depth + 1):
        if not frontier:
            break
        next_frontier: set[int] = set()
        for target_id in sorted(frontier):
            query = """
            MATCH (consumer:Symbol)-[r:CALLS|REFERENCES|USES|IMPORTS]
                  ->(target:Symbol {symbol_id: $symbol_id})
            RETURN consumer.symbol_id, consumer.name, consumer.kind,
                   target.symbol_id, label(r)
            ORDER BY consumer.name, consumer.symbol_id, label(r)
            LIMIT $limit
            """
            rows = graph_conn.execute(
                query,
                {"symbol_id": target_id, "limit": max_edges_per_symbol},
            ).get_all()
            for row in rows:
                source_id = int(row[0])
                relationship_type = str(row[4])
                edges.append(
                    {
                        "source_symbol_id": source_id,
                        "target_symbol_id": int(row[3]),
                        "relationship_type": relationship_type,
                        "depth": level,
                    }
                )
                if source_id not in discovered_depth:
                    discovered_depth[source_id] = level
                    next_frontier.add(source_id)
                    nodes[source_id] = {
                        "symbol_id": source_id,
                        "name": row[1],
                        "kind": row[2],
                        "depth": level,
                        "is_seed": False,
                    }
        frontier = next_frontier
    return sorted(nodes.values(), key=lambda node: (node["depth"], node["symbol_id"])), edges


@cli.command("file-impact")
@click.argument("file_path")
@click.option("--repo", "repo_key", default=None, help="Repository key; required when a path exists in multiple repositories.")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--graph",
    default=DEFAULT_GRAPH,
    show_default=True,
    help="Path to Ladybug graph database.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
@click.option("--depth", type=click.IntRange(1, 3), default=1, show_default=True, help="Incoming traversal depth (planned bound).")
@click.option("--max-edges-per-symbol", type=click.IntRange(1, 1000), default=25, show_default=True, help="Maximum incoming edges examined per symbol.")
@graph_error_boundary
def file_impact(
    file_path: str,
    repo_key: str | None,
    db: str,
    graph: str,
    json_output: bool,
    depth: int,
    max_edges_per_symbol: int,
) -> None:
    """Analyze what entities and surfaces are affected by changes to a file."""
    sql_conn = None
    graph_conn = None
    graph_db = None

    try:
        sql_conn = get_connection(db)
        graph_db, graph_conn = get_graph_connection(graph)

        # Verify file exists
        file_rows = sql_conn.execute(
            "SELECT f.id,f.path,r.repo_key FROM files f JOIN repos r ON r.id=f.repo_id "
            "WHERE f.path=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,f.id",
            (file_path, repo_key, repo_key),
        ).fetchall()

        if repo_key is None and len(file_rows) > 1:
            details = {"candidates": [dict(row) for row in file_rows]}
            args = {"file_path": file_path, "repo_key": None}
            if json_output:
                emit_json(error_response(command="file-impact", args=args, code="ambiguous_file", message="File path exists in multiple repositories; retry with --repo", details=details))
                return
            raise click.ClickException("File path is ambiguous; retry with --repo (" + ", ".join(row[2] for row in file_rows) + ")")
        file_record = file_rows[0] if file_rows else None

        if not file_record:
            args = {"file_path": file_path, "repo_key": repo_key}
            error_payload = error_response(
                command="file-impact",
                args=args,
                code="file_not_found",
                message=f"File not found: {file_path}",
                details={"file_path": file_path},
            )
            if json_output:
                emit_json(error_payload)
                return
            raise click.ClickException(error_payload["error"]["message"])

        # Query graph
        seed_symbols = _query_file_symbols_from_graph(graph_conn, file_path, repo_key)
        seed_ids = [s["symbol_id"] for s in seed_symbols]
        traversed_symbols, traversal_edges = _query_bounded_incoming_traversal(
            graph_conn, seed_ids, depth, max_edges_per_symbol
        )
        affected_ids = list(dict.fromkeys(
            seed_ids + [s["symbol_id"] for s in traversed_symbols]
        ))
        seed_nodes = [
            {**symbol, "depth": 0, "is_seed": True} for symbol in seed_symbols
        ]
        traversal_nodes = seed_nodes + traversed_symbols
        symbols = enrich_symbols_from_sql(sql_conn, traversal_nodes)

        direct_entities = _query_entities_from_symbols(graph_conn, seed_ids)
        entities = _query_entities_from_symbols(graph_conn, affected_ids)
        entity_ids = [e["entity_id"] for e in entities]
        surfaces = _query_surfaces_from_entities(graph_conn, entity_ids)

        if json_output:
            args = {"file_path": file_path, "repo_key": repo_key, "depth": depth, "max_edges_per_symbol": max_edges_per_symbol}
            data = {
                "file": {"path": file_path, "id": file_record[0], "repo_key": file_record[2]},
                "seed_symbols": enrich_symbols_from_sql(sql_conn, seed_nodes),
                "direct_entities": direct_entities,
                "affected_symbols": symbols,
                "traversal": {
                    "depth": depth,
                    "max_edges_per_symbol": max_edges_per_symbol,
                    "nodes": symbols,
                    "edges": traversal_edges,
                },
                "affected_entities": entities,
                "surfaces": surfaces,
            }
            summary = {
                "symbol_count": len(symbols),
                "seed_symbol_count": len(seed_symbols),
                "traversal_edge_count": len(traversal_edges),
                "direct_entity_count": len(direct_entities),
                "entity_count": len(entities),
                "rest_endpoint_count": len(surfaces["rest_endpoints"]),
                "workflow_count": len(surfaces["workflows"]),
                "security_op_count": len(surfaces["security_ops"]),
                "security_menu_count": len(surfaces["security_menus"]),
            }
            emit_json(
                success_response(
                    command="file-impact",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
        else:
            click.echo(f"\nFile Impact: {file_path}")
            click.echo("=" * 100)
            click.echo(f"Seeds: {len(seed_symbols)}, affected symbols: {len(symbols)}, traversal edges: {len(traversal_edges)}")
            click.echo(f"Entities: {len(entities)}, Endpoints: {len(surfaces['rest_endpoints'])}")

    finally:
        if graph_conn:
            graph_conn.close()
        if graph_db:
            graph_db.close()
        if sql_conn:
            sql_conn.close()


# ============================================================================
# Command 2: entity-context
# ============================================================================


def _query_entity_from_graph(
    graph_conn: lb.Connection, entity_name: str
) -> dict[str, Any] | None:
    """Query graph for an entity by name."""
    query = """
    MATCH (e:Entity)
    WHERE toLower(e.name) = toLower($name)
    RETURN 
        e.entity_id AS id,
        e.name AS name,
        e.entity_type AS entity_type,
        e.module AS module,
        e.table_name AS table_name,
        e.ent_file AS ent_file
    """
    results = graph_conn.execute(query, {"name": entity_name})
    rows = results.get_all()
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row[0],
        "name": row[1],
        "entity_type": row[2],
        "module": row[3],
        "table_name": row[4],
        "ent_file": row[5],
    }


def _query_mapped_symbols_for_entity(
    graph_conn: lb.Connection, entity_id: int
) -> list[dict[str, Any]]:
    """Query graph for all symbols mapped to an entity."""
    query = """
    MATCH (e:Entity {entity_id: $entity_id})-[r:ENTITY_MAPPING]-(s:Symbol)
    RETURN 
        s.symbol_id AS symbol_id,
        s.name AS name,
        s.kind AS kind
    ORDER BY s.kind, s.name, s.symbol_id
    """
    results = graph_conn.execute(query, {"entity_id": entity_id})
    rows = results.get_all()
    return [
        {
            "symbol_id": row[0],
            "name": row[1],
            "kind": row[2],
        }
        for row in rows
    ]


@cli.command("entity-context")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--graph",
    default=DEFAULT_GRAPH,
    show_default=True,
    help="Path to Ladybug graph database.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
@graph_error_boundary
def entity_context(
    entity_name: str,
    db: str,
    graph: str,
    json_output: bool,
) -> None:
    """Get full context for an entity."""
    sql_conn = None
    graph_conn = None
    graph_db = None

    try:
        sql_conn = get_connection(db)
        graph_db, graph_conn = get_graph_connection(graph)

        entity = _query_entity_from_graph(graph_conn, entity_name)
        if not entity:
            args = {"entity_name": entity_name}
            error_payload = error_response(
                command="entity-context",
                args=args,
                code="entity_not_found",
                message=f"Entity not found: {entity_name}",
            )
            if json_output:
                emit_json(error_payload)
                return
            raise click.ClickException(error_payload["error"]["message"])

        symbols = _query_mapped_symbols_for_entity(graph_conn, entity["id"])
        symbols = enrich_symbols_from_sql(sql_conn, symbols)

        surfaces = _query_surfaces_from_entities(graph_conn, [entity["id"]])

        db_schema = []
        if entity["table_name"]:
            db_schema = sql_conn.execute(
                """
                SELECT 
                    dt.id,
                    dt.table_name,
                    dt.primary_keys,
                    COUNT(df.id) AS field_count
                FROM dbschema_tables dt
                LEFT JOIN dbschema_fields df ON df.dbschema_table_id = dt.id
                WHERE LOWER(dt.table_name) = LOWER(?)
                GROUP BY dt.id
                """,
                (entity["table_name"],),
            ).fetchall()
            db_schema = [
                {
                    "table_id": row[0],
                    "table_name": row[1],
                    "primary_keys": row[2],
                    "field_count": row[3],
                }
                for row in db_schema
            ]

        if json_output:
            args = {"entity_name": entity_name}
            data = {
                "entity": entity,
                "mapped_symbols": symbols,
                "surfaces": surfaces,
                "db_schema": db_schema,
            }
            summary = {
                "symbol_count": len(symbols),
                "rest_endpoint_count": len(surfaces["rest_endpoints"]),
                "workflow_count": len(surfaces["workflows"]),
                "security_op_count": len(surfaces["security_ops"]),
                "security_menu_count": len(surfaces["security_menus"]),
                "db_table_count": len(db_schema),
            }
            emit_json(
                success_response(
                    command="entity-context",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
        else:
            click.echo(f"Entity: {entity["name"]} (id={entity["id"]})")
            click.echo(f"Mapped symbols: {len(symbols)}")
            click.echo(f"REST endpoints: {len(surfaces["rest_endpoints"])}")
            click.echo(f"Workflows: {len(surfaces["workflows"])}")
            click.echo(f"Security operations: {len(surfaces["security_ops"])}")
            click.echo(f"Security menus: {len(surfaces["security_menus"])}")
            click.echo(f"Database tables: {len(db_schema)}")

    finally:
        if graph_conn:
            graph_conn.close()
        if graph_db:
            graph_db.close()
        if sql_conn:
            sql_conn.close()


# ============================================================================
# Command 3: who-uses
# ============================================================================


def _query_symbol_usages(
    graph_conn: lb.Connection, symbol_id: int
) -> dict[str, list[dict[str, Any]]]:
    """Query graph for all usages of a symbol."""
    usages = {"callers": [], "referencers": []}

    # Find callers
    calls_query = """
    MATCH (caller:Symbol)-[r:CALLS]->(s:Symbol {symbol_id: $symbol_id})
    RETURN 
        caller.symbol_id AS caller_id,
        caller.name AS caller_name,
        caller.kind AS caller_kind
    ORDER BY caller.name, caller.symbol_id
    """
    try:
        calls_results = graph_conn.execute(calls_query, {"symbol_id": symbol_id})
        usages["callers"] = [
            {
                "symbol_id": row[0],
                "name": row[1],
                "kind": row[2],
            }
            for row in calls_results.get_all()
        ]
    except Exception as exc:
        raise RuntimeError(f"graph query failed: {exc}") from exc

    # Find referencers
    refs_query = """
    MATCH (referrer:Symbol)-[r:REFERENCES|USES|IMPORTS]->(s:Symbol {symbol_id: $symbol_id})
    RETURN 
        referrer.symbol_id AS referrer_id,
        referrer.name AS referrer_name,
        referrer.kind AS referrer_kind
    ORDER BY referrer.name, referrer.symbol_id
    """
    try:
        refs_results = graph_conn.execute(refs_query, {"symbol_id": symbol_id})
        usages["referencers"] = [
            {
                "symbol_id": row[0],
                "name": row[1],
                "kind": row[2],
            }
            for row in refs_results.get_all()
        ]
    except Exception as exc:
        raise RuntimeError(f"graph query failed: {exc}") from exc

    return usages


@cli.command("who-uses")
@click.argument("symbol_name", required=False)
@click.option("--symbol-id", type=int, default=None, help="Unambiguous symbol ID.")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--graph",
    default=DEFAULT_GRAPH,
    show_default=True,
    help="Path to Ladybug graph database.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
@graph_error_boundary
def who_uses(
    symbol_name: str | None,
    symbol_id: int | None,
    db: str,
    graph: str,
    json_output: bool,
) -> None:
    """Find all code that uses/references/calls a symbol."""
    sql_conn = None
    graph_conn = None
    graph_db = None

    try:
        sql_conn = get_connection(db)
        graph_db, graph_conn = get_graph_connection(graph)

        if symbol_id is None and not symbol_name:
            raise click.ClickException("Provide a symbol name or --symbol-id")
        candidates = sql_conn.execute(
            """
            SELECT s.id, s.name, s.kind, f.path AS file_path, s.start_line, s.end_line
            FROM symbols s JOIN files f ON f.id = s.file_id
            WHERE (? IS NOT NULL AND s.id = ?)
               OR (? IS NULL AND s.name = ?)
            ORDER BY s.id
            """,
            (symbol_id, symbol_id, symbol_id, symbol_name),
        ).fetchall()
        if not candidates:
            args = {"symbol_name": symbol_name, "symbol_id": symbol_id}
            if json_output:
                emit_json(error_response(command="who-uses", args=args, code="symbol_not_found", message="Symbol not found"))
                return
            raise click.ClickException("Symbol not found")
        if symbol_id is None and len(candidates) > 1:
            details = {"candidates": [dict(row) for row in candidates]}
            args = {"symbol_name": symbol_name, "symbol_id": None}
            if json_output:
                emit_json(error_response(command="who-uses", args=args, code="ambiguous_symbol", message="Symbol name is ambiguous", details=details))
                return
            click.echo("Ambiguous symbol name; choose one of:")
            for row in candidates:
                click.echo(f"  {row[0]} {row[1]} ({row[2]}) {row[3]}:{row[4]}")
            raise click.ClickException("Retry with --symbol-id")
        target_id = int(candidates[0][0])
        usages = _query_symbol_usages(graph_conn, target_id)
        all_symbols = usages["callers"] + usages["referencers"]
        enriched = enrich_symbols_from_sql(sql_conn, all_symbols)

        usages["callers"] = enriched[:len(usages["callers"])]
        usages["referencers"] = enriched[len(usages["callers"]):]

        if json_output:
            args = {"symbol_name": symbol_name, "symbol_id": symbol_id}
            target = dict(candidates[0])
            data = {
                "target": target,
                "callers": usages["callers"],
                "referencers": usages["referencers"],
            }
            summary = {
                "caller_count": len(usages["callers"]),
                "referrer_count": len(usages["referencers"]),
                "total_usages": len(all_symbols),
            }
            emit_json(
                success_response(
                    command="who-uses",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
        else:
            click.echo(f"Symbol: {symbol_name or target_id}")
            click.echo(f"Callers: {len(usages["callers"])}")
            for row in usages["callers"]:
                click.echo(f"  {row["name"]} ({row["kind"]})")
            click.echo(f"Referencers: {len(usages["referencers"])}")
            for row in usages["referencers"]:
                click.echo(f"  {row["name"]} ({row["kind"]})")

    finally:
        if graph_conn:
            graph_conn.close()
        if graph_db:
            graph_db.close()
        if sql_conn:
            sql_conn.close()


# ============================================================================
# Command 4: security-surface
# ============================================================================


def _query_entity_security_surface(
    graph_conn: lb.Connection, entity_id: int
) -> dict[str, list[dict[str, Any]]]:
    """Query graph for security surface of an entity."""
    surface = {
        "resources": [],
        "operations": [],
        "policies": [],
        "menus": [],
    }

    # Query parent security resources via EntityAccessLink.
    resource_query = """
    MATCH (e:Entity {entity_id: $entity_id})<-[:ENTITY_ACCESS_LINK_ENTITY]-(l:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_RESOURCE]->(so:SecurityOperation)
    RETURN DISTINCT
        so.security_operation_id AS security_operation_id,
        so.op_key AS op_key,
        so.title AS title
    ORDER BY op_key, security_operation_id
    """
    try:
        resource_results = graph_conn.execute(resource_query, {"entity_id": entity_id})
        surface["resources"] = [
            {"security_operation_id": row[0], "op_key": row[1], "title": row[2]}
            for row in resource_results.get_all()
        ]
    except Exception as exc:
        raise RuntimeError(f"graph query failed: {exc}") from exc

    # Query action operations via EntityAccessLink.
    op_query = """
    MATCH (e:Entity {entity_id: $entity_id})<-[:ENTITY_ACCESS_LINK_ENTITY]-(l:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_OPERATION]->(so:SecurityOperation)
    RETURN DISTINCT
        so.security_operation_id AS security_operation_id,
        so.op_key AS op_key,
        so.title AS title
    ORDER BY op_key, security_operation_id
    """
    try:
        op_results = graph_conn.execute(op_query, {"entity_id": entity_id})
        surface["operations"] = [
            {"security_operation_id": row[0], "op_key": row[1], "title": row[2]}
            for row in op_results.get_all()
        ]
    except Exception as exc:
        raise RuntimeError(f"graph query failed: {exc}") from exc

    # Query policies via EntityAccessLink
    policy_query = """
    MATCH (e:Entity {entity_id: $entity_id})<-[:ENTITY_ACCESS_LINK_ENTITY]-(l:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_POLICY]->(sp:SecurityPolicy)
    RETURN DISTINCT
        sp.security_policy_id AS security_policy_id,
        sp.policy_name AS policy_name
    ORDER BY policy_name, security_policy_id
    """
    try:
        policy_results = graph_conn.execute(policy_query, {"entity_id": entity_id})
        surface["policies"] = [
            {"security_policy_id": row[0], "policy_name": row[1]}
            for row in policy_results.get_all()
        ]
    except Exception as exc:
        raise RuntimeError(f"graph query failed: {exc}") from exc

    # Query menus via EntityAccessLink
    menu_query = """
    MATCH (e:Entity {entity_id: $entity_id})<-[:ENTITY_ACCESS_LINK_ENTITY]-(l:EntityAccessLink)-[:ENTITY_ACCESS_LINK_SECURITY_MENU]->(sm:SecurityMenu)
    RETURN DISTINCT
        sm.security_menu_id AS security_menu_id,
        sm.menu_name AS menu_name
    ORDER BY menu_name, security_menu_id
    """
    try:
        menu_results = graph_conn.execute(menu_query, {"entity_id": entity_id})
        surface["menus"] = [
            {"security_menu_id": row[0], "menu_name": row[1]}
            for row in menu_results.get_all()
        ]
    except Exception as exc:
        raise RuntimeError(f"graph query failed: {exc}") from exc

    return surface


@cli.command("security-surface")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--graph",
    default=DEFAULT_GRAPH,
    show_default=True,
    help="Path to Ladybug graph database.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
@graph_error_boundary
def security_surface(
    entity_name: str,
    db: str,
    graph: str,
    json_output: bool,
) -> None:
    """Analyze the security surface of an entity."""
    sql_conn = None
    graph_conn = None
    graph_db = None

    try:
        sql_conn = get_connection(db)
        graph_db, graph_conn = get_graph_connection(graph)

        entity = _query_entity_from_graph(graph_conn, entity_name)
        if not entity:
            args = {"entity_name": entity_name}
            error_payload = error_response(
                command="security-surface",
                args=args,
                code="entity_not_found",
                message=f"Entity not found: {entity_name}",
            )
            if json_output:
                emit_json(error_payload)
                return
            raise click.ClickException(error_payload["error"]["message"])

        surface = _query_entity_security_surface(graph_conn, entity["id"])

        if json_output:
            args = {"entity_name": entity_name}
            data = {
                "entity": entity,
                "security_surface": surface,
            }
            summary = {
                "resource_count": len(surface["resources"]),
                "operation_count": len(surface["operations"]),
                "policy_count": len(surface["policies"]),
                "menu_count": len(surface["menus"]),
            }
            emit_json(
                success_response(
                    command="security-surface",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
        else:
            click.echo(f"Entity: {entity["name"]} (id={entity["id"]})")
            click.echo(f"Resources: {len(surface["resources"])}")
            for row in surface["resources"]:
                click.echo(f"  {row["op_key"]}")
            click.echo(f"Operations: {len(surface["operations"])}")
            for row in surface["operations"]:
                click.echo(f"  {row["op_key"]}")
            click.echo(f"Policies: {len(surface["policies"])}")
            click.echo(f"Menus: {len(surface["menus"])}")

    finally:
        if graph_conn:
            graph_conn.close()
        if graph_db:
            graph_db.close()
        if sql_conn:
            sql_conn.close()


if __name__ == "__main__":
    cli()
