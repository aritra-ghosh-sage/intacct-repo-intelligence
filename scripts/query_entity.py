from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    from ._query_json import emit_json, error_response, success_response
except ImportError:
    from _query_json import emit_json, error_response, success_response

DEFAULT_DB = os.environ.get("CATALOG_DB", "catalog/catalog.db")


@dataclass
class GraphNode:
    symbol_id: int
    name: str
    kind: str
    depth: int
    via: str | None = None
    direction: str | None = None
    from_symbol: str | None = None


@click.group()
def cli() -> None:
    pass


def get_entity(conn: sqlite3.Connection, entity_name: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, name
        FROM entity_nodes
        WHERE lower(name) = ?
        """,
        (entity_name.lower(),),
    ).fetchone()


def get_entity_symbols(conn: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.kind,
            em.mapping_type,
            em.confidence,
            em.source_text,
            em.file_id
        FROM entity_mappings em
        JOIN symbols s
            ON s.id = em.symbol_id
        WHERE em.entity_id = ?
        ORDER BY s.kind, s.name
        """,
        (entity_id,),
    ).fetchall()


def get_root_symbols(
    conn: sqlite3.Connection,
    entity_id: int,
    min_weight: float,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            s.id,
            s.name,
            s.kind,
            er.role,
            er.weight,
            er.reason
        FROM entity_roots er
        JOIN symbols s
            ON s.id = er.symbol_id
        WHERE er.entity_id = ?
          AND er.weight >= ?
        ORDER BY er.weight DESC, s.name
        """,
        (entity_id, min_weight),
    ).fetchall()


def _seed_symbols(
    conn: sqlite3.Connection,
    entity_id: int,
    core_only: bool,
    min_weight: float,
) -> list[sqlite3.Row]:
    if core_only:
        return get_root_symbols(conn, entity_id, min_weight=min_weight)
    return get_entity_symbols(conn, entity_id)


def get_outgoing_relationships(
    conn: sqlite3.Connection,
    symbol_id: int,
    min_confidence: float = 0.0,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            r.relationship_type,
            r.source_symbol_id,
            r.source_name,
            r.source_kind,
            r.target_symbol_id,
            r.target_name,
            r.target_kind,
            r.confidence,
            r.file_path
        FROM relationships r
        WHERE r.source_symbol_id = ?
          AND r.target_symbol_id IS NOT NULL
          AND IFNULL(r.confidence, 0) >= ?
        ORDER BY r.relationship_type, r.target_name
        """,
        (symbol_id, min_confidence),
    ).fetchall()


def get_incoming_relationships(
    conn: sqlite3.Connection,
    symbol_id: int,
    min_confidence: float = 0.0,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            r.relationship_type,
            r.source_symbol_id,
            r.source_name,
            r.source_kind,
            r.target_symbol_id,
            r.target_name,
            r.target_kind,
            r.confidence,
            r.file_path
        FROM relationships r
        WHERE r.target_symbol_id = ?
          AND r.source_symbol_id IS NOT NULL
          AND IFNULL(r.confidence, 0) >= ?
        ORDER BY r.relationship_type, r.source_name
        """,
        (symbol_id, min_confidence),
    ).fetchall()


def get_symbol_files(
    conn: sqlite3.Connection, symbol_id: int, limit: int = 20
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT file_path
        FROM relationships
        WHERE source_symbol_id = ?
           OR target_symbol_id = ?
        ORDER BY file_path
        LIMIT ?
        """,
        (symbol_id, symbol_id, limit),
    ).fetchall()


def print_header(title: str) -> None:
    click.echo("")
    click.echo("=" * 100)
    click.echo(title)
    click.echo("=" * 100)


def print_section(title: str) -> None:
    click.echo("")
    click.echo(title)
    click.echo("-" * len(title))


def group_relationships(rows: list[sqlite3.Row], direction: str) -> None:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for row in rows:
        grouped[row["relationship_type"]].append(row)

    for rel_type in sorted(grouped):
        for row in grouped[rel_type]:
            if direction == "out":
                click.echo(
                    f"    {rel_type:<15} -> "
                    f"{row['target_name']:<45} "
                    f"(confidence={row['confidence']})"
                )
            else:
                click.echo(
                    f"    {rel_type:<15} <- "
                    f"{row['source_name']:<45} "
                    f"(confidence={row['confidence']})"
                )


def _entity_payload(entity: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": entity["id"],
        "name": entity["name"],
    }


def _mapping_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        key = row.get("mapping_type") or "unknown"
        counts[str(key)] += 1
    return {k: counts[k] for k in sorted(counts)}


def _workflow_type_counts(workflows_by_type: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {
        workflow_type: len(workflows_by_type[workflow_type])
        for workflow_type in sorted(workflows_by_type)
    }


def _collect_entity_db_tables(
    conn: sqlite3.Connection, entity_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH matched_tables AS (
            SELECT DISTINCT dt.id, dt.table_name, dt.primary_keys
            FROM entity_occurrences eo
            JOIN dbschema_tables dt
                ON LOWER(dt.table_name) = LOWER(eo.table_name)
            WHERE eo.entity_id = ?
              AND eo.table_name IS NOT NULL
              AND TRIM(eo.table_name) <> ''
        )
        SELECT
            mt.id,
            mt.table_name,
            mt.primary_keys,
            COUNT(df.id) AS field_count
        FROM matched_tables mt
        LEFT JOIN dbschema_fields df
            ON df.dbschema_table_id = mt.id
        GROUP BY mt.id, mt.table_name, mt.primary_keys
        ORDER BY mt.table_name
        """,
        (entity_id,),
    ).fetchall()


def _get_entity_or_error(
    conn: sqlite3.Connection,
    *,
    command: str,
    args: dict[str, Any],
    entity_name: str,
) -> tuple[sqlite3.Row | None, dict[str, Any] | None]:
    entity = get_entity(conn, entity_name)
    if entity is None:
        return None, error_response(
            command=command,
            args=args,
            code="entity_not_found",
            message=f"Entity not found: {entity_name}",
            details={"entity_name": entity_name},
        )
    return entity, None


def _collect_workflows_by_type(
    conn: sqlite3.Connection,
    entity_id: int,
    workflow_type: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for wf in get_workflows(conn, entity_id, workflow_type):
        wf_type = wf["workflow_type"] or "unknown"
        grouped.setdefault(wf_type, []).append(
            {
                "workflow_id": wf["id"],
                "name": wf["name"],
                "workflow_type": wf_type,
                "source_kind": wf["source_kind"],
                "source_file": wf["source_file"],
            }
        )

    return {
        wf_type: sorted(items, key=lambda x: (x["name"], x["workflow_id"]))
        for wf_type, items in sorted(grouped.items())
    }


def _collect_entity_default_json(
    conn: sqlite3.Connection, entity: sqlite3.Row
) -> tuple[dict[str, Any], dict[str, Any]]:
    mapped_symbols = [
        {
            "symbol_id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "mapping_type": row["mapping_type"],
            "confidence": row["confidence"],
            "source_text": row["source_text"],
            "file_id": row["file_id"],
        }
        for row in get_entity_symbols(conn, entity["id"])
    ]
    data = {
        "entity": _entity_payload(entity),
        "mapped_symbols": mapped_symbols,
    }
    summary = {
        "mapped_symbol_count": len(mapped_symbols),
        "mapping_type_counts": _mapping_type_counts(mapped_symbols),
    }
    return data, summary


def _collect_entity_workflow_json(
    conn: sqlite3.Connection, entity: sqlite3.Row
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflows_by_type = _collect_workflows_by_type(conn, entity["id"])
    workflow_count = sum(len(items) for items in workflows_by_type.values())
    data = {
        "entity": _entity_payload(entity),
        "workflows_by_type": workflows_by_type,
    }
    summary = {
        "workflow_count": workflow_count,
        "workflow_type_counts": _workflow_type_counts(workflows_by_type),
    }
    return data, summary


def _collect_entity_flow_json(
    conn: sqlite3.Connection, entity: sqlite3.Row
) -> tuple[dict[str, Any], dict[str, Any]]:
    roots = [
        {
            "symbol_id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "role": row["role"],
            "weight": row["weight"],
            "reason": row["reason"],
        }
        for row in get_root_symbols(conn, entity["id"], 0.75)
    ]

    db_table_rows = _collect_entity_db_tables(conn, entity["id"])

    db_tables: list[dict[str, Any]] = []
    for row in db_table_rows:
        fields = conn.execute(
            """
            SELECT field_name, field_type
            FROM dbschema_fields
            WHERE dbschema_table_id = ?
            ORDER BY field_name
            """,
            (row["id"],),
        ).fetchall()
        db_tables.append(
            {
                "table_name": row["table_name"],
                "primary_keys": row["primary_keys"],
                "field_count": row["field_count"],
                "fields": [
                    {
                        "field_name": f["field_name"],
                        "field_type": f["field_type"],
                    }
                    for f in fields
                ],
            }
        )

    workflows_by_type = _collect_workflows_by_type(conn, entity["id"])
    workflow_count = sum(len(items) for items in workflows_by_type.values())

    data = {
        "entity": _entity_payload(entity),
        "core_roots": roots,
        "db_schema_tables": db_tables,
        "workflows_by_type": workflows_by_type,
    }
    summary = {
        "core_root_count": len(roots),
        "db_table_count": len(db_tables),
        "workflow_count": workflow_count,
    }
    return data, summary


def _collect_entity_openapispec_json(
    conn: sqlite3.Connection, entity: sqlite3.Row
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT source_text, mapping_type, file_id
        FROM entity_mappings
        WHERE entity_id = ?
          AND mapping_type LIKE 'openapispec_%'
        ORDER BY mapping_type, source_text
        """,
        (entity["id"],),
    ).fetchall()

    mappings = [
        {
            "mapping_type": row["mapping_type"],
            "source_text": row["source_text"],
            "file_id": row["file_id"],
        }
        for row in rows
    ]

    data = {
        "entity": _entity_payload(entity),
        "openapi_mappings": mappings,
    }
    summary = {
        "openapi_mapping_count": len(mappings),
        "mapping_type_counts": _mapping_type_counts(mappings),
    }
    return data, summary


def _collect_entity_access_json(
    conn: sqlite3.Connection, entity: sqlite3.Row
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            eal.surface,
            eal.record_id,
            eal.link_type,
            eal.evidence_file_id,
            ef.path AS evidence_file,
            eal.notes,
            CASE
                WHEN eal.surface = 'security_operation' THEN (
                    SELECT so.op_key
                    FROM security_operations so
                    WHERE so.id = eal.record_id
                )
                WHEN eal.surface = 'security_policy' THEN (
                    SELECT sp.policy_name
                    FROM security_policies sp
                    WHERE sp.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu' THEN (
                    SELECT COALESCE(sm.menu_name, sm.module, '(menu)')
                    FROM security_menus sm
                    WHERE sm.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu_item' THEN (
                    SELECT smi.item_path
                    FROM security_menu_items smi
                    WHERE smi.id = eal.record_id
                )
                WHEN eal.surface = 'dbschema_table' THEN (
                    SELECT dt.table_name
                    FROM dbschema_tables dt
                    WHERE dt.id = eal.record_id
                )
                WHEN eal.surface = 'workflow' THEN (
                    SELECT wf.name
                    FROM workflows wf
                    WHERE wf.id = eal.record_id
                )
                WHEN eal.surface = 'rest_endpoint' THEN (
                    SELECT re.method || ' ' || re.path
                    FROM rest_endpoints re
                    WHERE re.id = eal.record_id
                )
                ELSE '(unknown)'
            END AS label,
            CASE
                WHEN eal.surface = 'security_operation' THEN (
                    SELECT so.source_file
                    FROM security_operations so
                    WHERE so.id = eal.record_id
                )
                WHEN eal.surface = 'security_policy' THEN (
                    SELECT sp.source_file
                    FROM security_policies sp
                    WHERE sp.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu' THEN (
                    SELECT sm.source_file
                    FROM security_menus sm
                    WHERE sm.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu_item' THEN (
                    SELECT sm.source_file
                    FROM security_menu_items smi
                    JOIN security_menus sm ON sm.id = smi.menu_id
                    WHERE smi.id = eal.record_id
                )
                WHEN eal.surface = 'dbschema_table' THEN (
                    SELECT dt.source_file
                    FROM dbschema_tables dt
                    WHERE dt.id = eal.record_id
                )
                WHEN eal.surface = 'workflow' THEN (
                    SELECT wf.source_file
                    FROM workflows wf
                    WHERE wf.id = eal.record_id
                )
                WHEN eal.surface = 'rest_endpoint' THEN (
                    SELECT f.path
                    FROM rest_endpoints re
                    JOIN files f ON f.id = re.file_id
                    WHERE re.id = eal.record_id
                )
                ELSE NULL
            END AS source_file
        FROM entity_access_links eal
        LEFT JOIN files ef
          ON ef.id = eal.evidence_file_id
        WHERE eal.entity_id = ?
        ORDER BY eal.surface, eal.link_type, label
        """,
        (entity["id"],),
    ).fetchall()

    access_links = [
        {
            "surface": row["surface"],
            "record_id": row["record_id"],
            "link_type": row["link_type"],
            "label": row["label"],
            "source_file": row["source_file"],
            "evidence_file": row["evidence_file"],
            "notes": row["notes"],
        }
        for row in rows
    ]

    dbschema_fields_by_record_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["surface"] != "dbschema_table":
            continue
        record_id = str(row["record_id"])
        if record_id in dbschema_fields_by_record_id:
            continue
        fields = conn.execute(
            """
            SELECT field_name, field_type
            FROM dbschema_fields
            WHERE dbschema_table_id = ?
            ORDER BY field_name
            """,
            (row["record_id"],),
        ).fetchall()
        dbschema_fields_by_record_id[record_id] = [
            {
                "field_name": f["field_name"],
                "field_type": f["field_type"],
            }
            for f in fields
        ]

    surface_counts: dict[str, int] = defaultdict(int)
    link_type_counts: dict[str, int] = defaultdict(int)
    for link in access_links:
        surface_counts[link["surface"]] += 1
        link_type_counts[link["link_type"]] += 1

    data = {
        "entity": _entity_payload(entity),
        "access_links": access_links,
        "dbschema_fields_by_record_id": {
            key: dbschema_fields_by_record_id[key]
            for key in sorted(dbschema_fields_by_record_id, key=int)
        },
    }
    summary = {
        "access_link_count": len(access_links),
        "surface_counts": {k: surface_counts[k] for k in sorted(surface_counts)},
        "link_type_counts": {k: link_type_counts[k] for k in sorted(link_type_counts)},
    }
    return data, summary


def _collect_root_symbols_json(
    conn: sqlite3.Connection, entity: sqlite3.Row, min_weight: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    roots = [
        {
            "symbol_id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "role": row["role"],
            "weight": row["weight"],
            "reason": row["reason"],
        }
        for row in get_root_symbols(conn, entity["id"], min_weight)
    ]
    data = {
        "entity": _entity_payload(entity),
        "roots": roots,
    }
    summary = {
        "root_count": len(roots),
        "min_weight": min_weight,
    }
    return data, summary


def _relationship_outgoing_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "relationship_type": row["relationship_type"],
        "target_symbol_id": row["target_symbol_id"],
        "target_name": row["target_name"],
        "target_kind": row["target_kind"],
        "confidence": row["confidence"],
        "file_path": row["file_path"],
    }


def _relationship_incoming_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "relationship_type": row["relationship_type"],
        "source_symbol_id": row["source_symbol_id"],
        "source_name": row["source_name"],
        "source_kind": row["source_kind"],
        "confidence": row["confidence"],
        "file_path": row["file_path"],
    }


def _collect_direct_impact_json(
    conn: sqlite3.Connection,
    entity: sqlite3.Row,
    min_confidence: float,
    per_symbol_limit: int,
    core_only: bool,
    min_weight: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    seed_rows = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )

    seed_symbols: list[dict[str, Any]] = []
    symbol_impacts: list[dict[str, Any]] = []
    outgoing_edge_count = 0
    incoming_edge_count = 0
    related_files_seen: set[str] = set()

    for row in seed_rows:
        seed_symbols.append(
            {
                "symbol_id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "seed_type": "root" if "role" in row.keys() else "mapped_symbol",
                "mapping_type": row["mapping_type"] if "mapping_type" in row.keys() else None,
                "confidence": row["confidence"] if "confidence" in row.keys() else None,
                "role": row["role"] if "role" in row.keys() else None,
                "weight": row["weight"] if "weight" in row.keys() else None,
            }
        )

        outgoing_rows = get_outgoing_relationships(conn, row["id"], min_confidence)[
            :per_symbol_limit
        ]
        incoming_rows = get_incoming_relationships(conn, row["id"], min_confidence)[
            :per_symbol_limit
        ]
        file_rows = get_symbol_files(conn, row["id"], limit=10)

        outgoing_edge_count += len(outgoing_rows)
        incoming_edge_count += len(incoming_rows)

        related_files = [f["file_path"] for f in file_rows]
        related_files_seen.update(related_files)

        symbol_impacts.append(
            {
                "seed_symbol_id": row["id"],
                "outgoing": [_relationship_outgoing_payload(r) for r in outgoing_rows],
                "incoming": [_relationship_incoming_payload(r) for r in incoming_rows],
                "related_files": related_files,
            }
        )

    data = {
        "entity": _entity_payload(entity),
        "seed_symbols": seed_symbols,
        "symbol_impacts": symbol_impacts,
    }
    summary = {
        "seed_count": len(seed_symbols),
        "outgoing_edge_count": outgoing_edge_count,
        "incoming_edge_count": incoming_edge_count,
        "related_file_count": len(related_files_seen),
    }
    return data, summary


def _collect_impact_json(
    conn: sqlite3.Connection,
    entity: sqlite3.Row,
    depth: int,
    min_confidence: float,
    include_incoming: bool,
    include_outgoing: bool,
    core_only: bool,
    min_weight: float,
    max_edges_per_node: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    seed_rows = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )

    discovered = bfs_impact(
        conn=conn,
        seed_symbols=seed_rows,
        max_depth=depth,
        min_confidence=min_confidence,
        include_incoming=include_incoming,
        include_outgoing=include_outgoing,
        max_edges_per_node=max_edges_per_node,
    )

    node_ids = {node.symbol_id for node in discovered}
    nodes = [
        {
            "symbol_id": node.symbol_id,
            "name": node.name,
            "kind": node.kind,
            "depth": node.depth,
            "is_seed": node.depth == 0,
        }
        for node in discovered
    ]

    edge_set: set[tuple[int, int, str, str, float, str | None]] = set()
    for node in discovered:
        if node.depth >= depth:
            continue

        if include_outgoing:
            for row in get_outgoing_relationships(conn, node.symbol_id, min_confidence)[
                :max_edges_per_node
            ]:
                target_id = row["target_symbol_id"]
                if target_id is None or target_id not in node_ids:
                    continue
                edge_set.add(
                    (
                        node.symbol_id,
                        target_id,
                        row["relationship_type"],
                        "out",
                        float(row["confidence"] or 0.0),
                        row["file_path"],
                    )
                )

        if include_incoming:
            for row in get_incoming_relationships(conn, node.symbol_id, min_confidence)[
                :max_edges_per_node
            ]:
                source_id = row["source_symbol_id"]
                if source_id is None or source_id not in node_ids:
                    continue
                edge_set.add(
                    (
                        source_id,
                        node.symbol_id,
                        row["relationship_type"],
                        "in",
                        float(row["confidence"] or 0.0),
                        row["file_path"],
                    )
                )

    edges = [
        {
            "from_symbol_id": from_symbol_id,
            "to_symbol_id": to_symbol_id,
            "relationship_type": relationship_type,
            "direction": direction,
            "confidence": confidence,
            "file_path": file_path,
        }
        for (
            from_symbol_id,
            to_symbol_id,
            relationship_type,
            direction,
            confidence,
            file_path,
        ) in sorted(edge_set)
    ]

    by_kind_counts: dict[str, int] = defaultdict(int)
    by_depth_counts: dict[int, int] = defaultdict(int)
    for node in nodes:
        by_kind_counts[node["kind"]] += 1
        by_depth_counts[node["depth"]] += 1

    data = {
        "entity": _entity_payload(entity),
        "traversal": {
            "nodes": nodes,
            "edges": edges,
        },
    }
    summary = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "by_kind": {k: by_kind_counts[k] for k in sorted(by_kind_counts)},
        "by_depth": {
            str(k): by_depth_counts[k]
            for k in sorted(by_depth_counts)
        },
    }
    return data, summary


def _collect_risk_json(
    conn: sqlite3.Connection,
    entity: sqlite3.Row,
    depth: int,
    min_confidence: float,
    max_edges_per_node: int,
    core_only: bool,
    min_weight: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )

    discovered = bfs_impact(
        conn=conn,
        seed_symbols=seed_symbols,
        max_depth=depth,
        min_confidence=min_confidence,
        include_incoming=True,
        include_outgoing=True,
        max_edges_per_node=max_edges_per_node,
    )

    incoming_count = 0
    outgoing_count = 0
    expansion_points: dict[str, int] = defaultdict(int)
    for node in discovered:
        if node.direction == "in":
            incoming_count += 1
        elif node.direction == "out":
            outgoing_count += 1
        expansion_points[node.from_symbol or "seed"] += 1

    top_expansion_points = [
        {
            "symbol_name": symbol_name,
            "count": count,
        }
        for symbol_name, count in sorted(
            expansion_points.items(), key=lambda x: (-x[1], x[0])
        )[:20]
    ]

    metrics = {
        "seed_count": len(seed_symbols),
        "discovered_count": len(discovered),
        "incoming_count": incoming_count,
        "outgoing_count": outgoing_count,
    }
    data = {
        "entity": _entity_payload(entity),
        "metrics": metrics,
        "top_expansion_points": top_expansion_points,
    }
    summary = dict(metrics)
    return data, summary


def show_entity(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    symbols = get_entity_symbols(conn, entity["id"])
    print_header(f"ENTITY: {entity_name}")
    print_section("Mapped Symbols")

    if not symbols:
        click.echo("No symbols mapped.")
        return 0

    for symbol in symbols:
        click.echo(
            f"{symbol['kind']:<15} "
            f"{symbol['name']:<60} "
            f"{(symbol['mapping_type'] or ''):<25} "
            f"confidence={symbol['confidence']}"
        )
    return 0


def show_root_symbols(
    conn: sqlite3.Connection, entity_name: str, min_weight: float
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    roots = get_root_symbols(conn, entity["id"], min_weight=min_weight)

    print_header(f"CORE ENTITY ROOTS: {entity_name}")
    click.echo(f"Min weight: {min_weight}")

    if not roots:
        click.echo(
            f"No canonical roots found at min_weight={min_weight}. "
            "Run build_entity_roots.py build first."
        )
        return 0

    print_section("Entity Roots")
    for root in roots:
        click.echo(
            f"{root['weight']:<8.2f} "
            f"{root['role']:<15} "
            f"{root['kind']:<15} "
            f"{root['name']:<60} "
            f"{root['reason']}"
        )
    return 0


def show_direct_impact(
    conn: sqlite3.Connection,
    entity_name: str,
    min_confidence: float,
    per_symbol_limit: int,
    core_only: bool,
    min_weight: float,
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )

    print_header(f"DIRECT IMPACT REPORT: {entity_name}")
    if core_only:
        click.echo(f"Using canonical roots only (min_weight={min_weight})")
    else:
        click.echo("Using all mapped symbols as seeds")

    print_section("Seed Symbols")
    if not seed_symbols:
        click.echo(
            "No seed symbols found. Try lowering --min-weight or drop --core-only."
        )
        return 0

    for symbol in seed_symbols:
        if "role" in symbol.keys():
            extras = f"role={symbol['role']:<14} weight={symbol['weight']:.2f}"
        else:
            extras = (
                f"mapping={(symbol['mapping_type'] or ''):<20} "
                f"confidence={symbol['confidence']}"
            )

        click.echo(f"{symbol['kind']:<15} {symbol['name']:<60} {extras}")

    print_section("Symbol-Level Impact")
    for symbol in seed_symbols:
        click.echo("")
        click.echo(f"[{symbol['kind']}] {symbol['name']}")

        outgoing = get_outgoing_relationships(conn, symbol["id"], min_confidence)[
            :per_symbol_limit
        ]
        incoming = get_incoming_relationships(conn, symbol["id"], min_confidence)[
            :per_symbol_limit
        ]
        files = get_symbol_files(conn, symbol["id"], limit=10)

        if outgoing:
            click.echo("")
            click.echo("  Depends On:")
            group_relationships(outgoing, "out")

        if incoming:
            click.echo("")
            click.echo("  Referenced By:")
            group_relationships(incoming, "in")

        if files:
            click.echo("")
            click.echo("  Related Files:")
            for file_row in files:
                click.echo(f"    {file_row['file_path']}")
    return 0


def bfs_impact(
    conn: sqlite3.Connection,
    seed_symbols: list[sqlite3.Row],
    max_depth: int,
    min_confidence: float,
    include_incoming: bool,
    include_outgoing: bool,
    max_edges_per_node: int,
) -> list[GraphNode]:
    visited: set[int] = set()
    discovered: list[GraphNode] = []
    queue: deque[GraphNode] = deque()

    for symbol in seed_symbols:
        queue.append(
            GraphNode(
                symbol_id=symbol["id"],
                name=symbol["name"],
                kind=symbol["kind"],
                depth=0,
                via="ENTITY_MAPPING",
                direction="seed",
                from_symbol=None,
            )
        )

    while queue:
        node = queue.popleft()
        if node.symbol_id in visited:
            continue

        visited.add(node.symbol_id)
        discovered.append(node)

        if node.depth >= max_depth:
            continue

        relationships: list[tuple[str, sqlite3.Row]] = []
        if include_outgoing:
            relationships.extend(
                ("out", row)
                for row in get_outgoing_relationships(
                    conn, node.symbol_id, min_confidence
                )[:max_edges_per_node]
            )
        if include_incoming:
            relationships.extend(
                ("in", row)
                for row in get_incoming_relationships(
                    conn, node.symbol_id, min_confidence
                )[:max_edges_per_node]
            )

        for direction, row in relationships:
            if direction == "out":
                next_id = row["target_symbol_id"]
                next_name = row["target_name"]
                next_kind = row["target_kind"] or "unknown"
            else:
                next_id = row["source_symbol_id"]
                next_name = row["source_name"]
                next_kind = row["source_kind"] or "unknown"

            if next_id is None or next_id in visited:
                continue

            queue.append(
                GraphNode(
                    symbol_id=next_id,
                    name=next_name,
                    kind=next_kind,
                    depth=node.depth + 1,
                    via=row["relationship_type"],
                    direction=direction,
                    from_symbol=node.name,
                )
            )

    return discovered


def show_bfs_impact(
    conn: sqlite3.Connection,
    entity_name: str,
    depth: int,
    min_confidence: float,
    include_incoming: bool,
    include_outgoing: bool,
    max_edges_per_node: int,
    core_only: bool,
    min_weight: float,
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )
    if not seed_symbols:
        click.echo(
            f"No seed symbols for entity: {entity_name}. "
            "Try lowering --min-weight or drop --core-only."
        )
        return 0

    print_header(f"TRANSITIVE IMPACT REPORT: {entity_name}")
    click.echo(f"Depth: {depth}")
    click.echo(f"Min confidence: {min_confidence}")
    click.echo(f"Include outgoing dependencies: {include_outgoing}")
    click.echo(f"Include incoming references: {include_incoming}")
    click.echo(f"Max edges per node: {max_edges_per_node}")
    click.echo(f"Core-only seeds: {core_only}")
    click.echo(f"Min seed weight: {min_weight}")

    discovered = bfs_impact(
        conn=conn,
        seed_symbols=seed_symbols,
        max_depth=depth,
        min_confidence=min_confidence,
        include_incoming=include_incoming,
        include_outgoing=include_outgoing,
        max_edges_per_node=max_edges_per_node,
    )

    by_depth: dict[int, list[GraphNode]] = defaultdict(list)
    for node in discovered:
        by_depth[node.depth].append(node)

    for current_depth in sorted(by_depth):
        print_section(f"Depth {current_depth}")
        for node in by_depth[current_depth]:
            if current_depth == 0:
                click.echo(f"{node.kind:<15} {node.name:<60} [seed]")
            else:
                arrow = "->" if node.direction == "out" else "<-"
                click.echo(
                    f"{node.kind:<15} {node.name:<60} {arrow} {node.via} from {node.from_symbol}"
                )

    print_section("Summary")
    click.echo(f"Seed symbols: {len(seed_symbols)}")
    click.echo(f"Discovered symbols: {len(discovered)}")

    kind_counts: dict[str, int] = defaultdict(int)
    for node in discovered:
        kind_counts[node.kind] += 1

    click.echo("")
    click.echo("By kind:")
    for kind, count in sorted(
        kind_counts.items(), key=lambda item: item[1], reverse=True
    ):
        click.echo(f"  {kind:<15} {count}")
    return 0


def show_risk_summary(
    conn: sqlite3.Connection,
    entity_name: str,
    depth: int,
    min_confidence: float,
    max_edges_per_node: int,
    core_only: bool,
    min_weight: float,
) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    seed_symbols = _seed_symbols(
        conn=conn,
        entity_id=entity["id"],
        core_only=core_only,
        min_weight=min_weight,
    )
    if not seed_symbols:
        click.echo(
            f"No seed symbols for entity: {entity_name}. "
            "Try lowering --min-weight or drop --core-only."
        )
        return 0

    discovered = bfs_impact(
        conn=conn,
        seed_symbols=seed_symbols,
        max_depth=depth,
        min_confidence=min_confidence,
        include_incoming=True,
        include_outgoing=True,
        max_edges_per_node=max_edges_per_node,
    )

    incoming_count = 0
    outgoing_count = 0
    expansion_points: dict[str, int] = defaultdict(int)

    for node in discovered:
        if node.direction == "in":
            incoming_count += 1
        elif node.direction == "out":
            outgoing_count += 1
        expansion_points[node.from_symbol or "seed"] += 1

    print_header(f"RISK SUMMARY: {entity_name}")
    click.echo(f"Core-only seeds: {core_only}")
    click.echo(f"Min seed weight: {min_weight}")
    click.echo(f"Mapped seed symbols: {len(seed_symbols)}")
    click.echo(f"Total discovered symbols: {len(discovered)}")
    click.echo(f"Incoming impact references: {incoming_count}")
    click.echo(f"Outgoing dependencies: {outgoing_count}")

    print_section("Top Expansion Points")
    top = sorted(expansion_points.items(), key=lambda item: item[1], reverse=True)[:20]
    for name, count in top:
        click.echo(f"{name:<60} {count}")
    return 0


def _resolve_direction_flags(
    incoming_only: bool, outgoing_only: bool
) -> tuple[bool, bool]:
    if incoming_only and outgoing_only:
        raise click.ClickException("Use only one of --incoming-only or --outgoing-only")

    include_incoming = not outgoing_only
    include_outgoing = not incoming_only
    return include_incoming, include_outgoing


@cli.command("entity")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--workflow", is_flag=True, help="Show discovered workflows for the entity."
)
@click.option("--flow", is_flag=True, help="Show end-to-end flow view for the entity.")
@click.option(
    "--openapispec", is_flag=True, help="Show openapispec mappings for the entity."
)
@click.option("--access", is_flag=True, help="Show linked entity access graph records.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def entity(
    entity_name: str,
    db: str,
    workflow: bool,
    flow: bool,
    openapispec: bool,
    access: bool,
    json_output: bool,
) -> None:
    """Show mapped symbols for an entity."""
    selected_views = sum([workflow, flow, openapispec, access])
    if selected_views > 1:
        if json_output:
            emit_json(
                error_response(
                    command="entity",
                    args={
                        "entity_name": entity_name,
                        "workflow": workflow,
                        "flow": flow,
                        "openapispec": openapispec,
                        "access": access,
                    },
                    code="invalid_flags",
                    message="Use only one of --workflow, --flow, --openapispec, or --access",
                    details={
                        "workflow": workflow,
                        "flow": flow,
                        "openapispec": openapispec,
                        "access": access,
                    },
                )
            )
            return
        raise click.ClickException(
            "Use only one of --workflow, --flow, --openapispec, or --access"
        )

    conn = get_connection(db)
    try:
        if json_output:
            selected = "entity"
            if workflow:
                selected = "workflow"
            elif flow:
                selected = "flow"
            elif openapispec:
                selected = "openapispec"
            elif access:
                selected = "access"

            args = {
                "entity_name": entity_name,
                "view": selected,
                "workflow": workflow,
                "flow": flow,
                "openapispec": openapispec,
                "access": access,
            }

            entity_row, error_payload = _get_entity_or_error(
                conn,
                command="entity",
                args=args,
                entity_name=entity_name,
            )
            if error_payload is not None:
                emit_json(error_payload)
                return

            assert entity_row is not None

            if workflow:
                data, summary = _collect_entity_workflow_json(conn, entity_row)
            elif flow:
                data, summary = _collect_entity_flow_json(conn, entity_row)
            elif openapispec:
                data, summary = _collect_entity_openapispec_json(conn, entity_row)
            elif access:
                data, summary = _collect_entity_access_json(conn, entity_row)
            else:
                data, summary = _collect_entity_default_json(conn, entity_row)

            emit_json(
                success_response(
                    command="entity",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
            return

        if workflow:
            raise SystemExit(show_workflow_view(conn, entity_name))

        if flow:
            raise SystemExit(show_flow_view(conn, entity_name))

        if openapispec:
            raise SystemExit(show_openapispec_view(conn, entity_name))

        if access:
            raise SystemExit(show_access_view(conn, entity_name))

        raise SystemExit(show_entity(conn, entity_name))
    finally:
        conn.close()


@cli.command("root-symbols")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def root_symbols(entity_name: str, db: str, min_weight: float, json_output: bool) -> None:
    """Show canonical roots for an entity."""
    conn = get_connection(db)
    try:
        if json_output:
            args = {
                "entity_name": entity_name,
                "min_weight": min_weight,
            }
            entity_row, error_payload = _get_entity_or_error(
                conn,
                command="root-symbols",
                args=args,
                entity_name=entity_name,
            )
            if error_payload is not None:
                emit_json(error_payload)
                return

            assert entity_row is not None
            data, summary = _collect_root_symbols_json(conn, entity_row, min_weight)
            emit_json(
                success_response(
                    command="root-symbols",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
            return
        raise SystemExit(show_root_symbols(conn, entity_name, min_weight))
    finally:
        conn.close()


@cli.command("direct-impact")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--min-confidence", type=float, default=0.0, show_default=True)
@click.option(
    "--core-only",
    is_flag=True,
    help="Use only canonical entity roots as traversal seeds.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option(
    "--per-symbol-limit",
    "--limit",
    "per_symbol_limit",
    type=int,
    default=25,
    show_default=True,
    help="Maximum relationships per symbol.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def direct_impact(
    entity_name: str,
    db: str,
    min_confidence: float,
    core_only: bool,
    min_weight: float,
    per_symbol_limit: int,
    json_output: bool,
) -> None:
    """Show direct symbol-level incoming/outgoing relationships."""
    conn = get_connection(db)
    try:
        if json_output:
            args = {
                "entity_name": entity_name,
                "min_confidence": min_confidence,
                "core_only": core_only,
                "min_weight": min_weight,
                "per_symbol_limit": per_symbol_limit,
            }
            entity_row, error_payload = _get_entity_or_error(
                conn,
                command="direct-impact",
                args=args,
                entity_name=entity_name,
            )
            if error_payload is not None:
                emit_json(error_payload)
                return

            assert entity_row is not None
            data, summary = _collect_direct_impact_json(
                conn=conn,
                entity=entity_row,
                min_confidence=min_confidence,
                per_symbol_limit=per_symbol_limit,
                core_only=core_only,
                min_weight=min_weight,
            )
            emit_json(
                success_response(
                    command="direct-impact",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
            return
        raise SystemExit(
            show_direct_impact(
                conn=conn,
                entity_name=entity_name,
                min_confidence=min_confidence,
                per_symbol_limit=per_symbol_limit,
                core_only=core_only,
                min_weight=min_weight,
            )
        )
    finally:
        conn.close()


@cli.command("impact")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--depth", type=int, default=1, show_default=True, help="Traversal depth."
)
@click.option("--min-confidence", type=float, default=0.0, show_default=True)
@click.option("--incoming-only", is_flag=True, help="Only include incoming references.")
@click.option(
    "--outgoing-only", is_flag=True, help="Only include outgoing dependencies."
)
@click.option(
    "--core-only",
    is_flag=True,
    help="Use only canonical entity roots as traversal seeds.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option(
    "--max-edges-per-node",
    "--limit",
    "max_edges_per_node",
    type=int,
    default=25,
    show_default=True,
    help="Maximum traversed relationships per symbol.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def impact(
    entity_name: str,
    db: str,
    depth: int,
    min_confidence: float,
    incoming_only: bool,
    outgoing_only: bool,
    core_only: bool,
    min_weight: float,
    max_edges_per_node: int,
    json_output: bool,
) -> None:
    """Show impact analysis; depth=1 uses direct impact, depth>1 uses BFS traversal."""
    if incoming_only and outgoing_only:
        if json_output:
            emit_json(
                error_response(
                    command="impact",
                    args={
                        "entity_name": entity_name,
                        "depth": depth,
                        "min_confidence": min_confidence,
                        "incoming_only": incoming_only,
                        "outgoing_only": outgoing_only,
                        "core_only": core_only,
                        "min_weight": min_weight,
                        "max_edges_per_node": max_edges_per_node,
                    },
                    code="invalid_flags",
                    message="Use only one of --incoming-only or --outgoing-only",
                    details={
                        "incoming_only": incoming_only,
                        "outgoing_only": outgoing_only,
                    },
                )
            )
            return
        raise click.ClickException("Use only one of --incoming-only or --outgoing-only")

    include_incoming, include_outgoing = _resolve_direction_flags(incoming_only, outgoing_only)

    conn = get_connection(db)
    try:
        if json_output:
            args = {
                "entity_name": entity_name,
                "depth": depth,
                "min_confidence": min_confidence,
                "incoming_only": incoming_only,
                "outgoing_only": outgoing_only,
                "core_only": core_only,
                "min_weight": min_weight,
                "max_edges_per_node": max_edges_per_node,
            }
            entity_row, error_payload = _get_entity_or_error(
                conn,
                command="impact",
                args=args,
                entity_name=entity_name,
            )
            if error_payload is not None:
                emit_json(error_payload)
                return

            assert entity_row is not None
            data, summary = _collect_impact_json(
                conn=conn,
                entity=entity_row,
                depth=depth,
                min_confidence=min_confidence,
                include_incoming=include_incoming,
                include_outgoing=include_outgoing,
                core_only=core_only,
                min_weight=min_weight,
                max_edges_per_node=max_edges_per_node,
            )
            emit_json(
                success_response(
                    command="impact",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
            return
        if depth <= 1:
            raise SystemExit(
                show_direct_impact(
                    conn=conn,
                    entity_name=entity_name,
                    min_confidence=min_confidence,
                    per_symbol_limit=max_edges_per_node,
                    core_only=core_only,
                    min_weight=min_weight,
                )
            )

        raise SystemExit(
            show_bfs_impact(
                conn=conn,
                entity_name=entity_name,
                depth=depth,
                min_confidence=min_confidence,
                include_incoming=include_incoming,
                include_outgoing=include_outgoing,
                max_edges_per_node=max_edges_per_node,
                core_only=core_only,
                min_weight=min_weight,
            )
        )
    finally:
        conn.close()


@cli.command("risk")
@click.argument("entity_name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--depth", type=int, default=2, show_default=True)
@click.option("--min-confidence", type=float, default=0.0, show_default=True)
@click.option(
    "--core-only",
    is_flag=True,
    help="Use only canonical entity roots as traversal seeds.",
)
@click.option("--min-weight", type=float, default=0.75, show_default=True)
@click.option("--max-edges-per-node", type=int, default=25, show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def risk(
    entity_name: str,
    db: str,
    depth: int,
    min_confidence: float,
    core_only: bool,
    min_weight: float,
    max_edges_per_node: int,
    json_output: bool,
) -> None:
    """Show compact risk summary for transitive impact."""
    conn = get_connection(db)
    try:
        if json_output:
            args = {
                "entity_name": entity_name,
                "depth": depth,
                "min_confidence": min_confidence,
                "core_only": core_only,
                "min_weight": min_weight,
                "max_edges_per_node": max_edges_per_node,
            }
            entity_row, error_payload = _get_entity_or_error(
                conn,
                command="risk",
                args=args,
                entity_name=entity_name,
            )
            if error_payload is not None:
                emit_json(error_payload)
                return

            assert entity_row is not None
            data, summary = _collect_risk_json(
                conn=conn,
                entity=entity_row,
                depth=depth,
                min_confidence=min_confidence,
                max_edges_per_node=max_edges_per_node,
                core_only=core_only,
                min_weight=min_weight,
            )
            emit_json(
                success_response(
                    command="risk",
                    args=args,
                    data=data,
                    summary=summary,
                )
            )
            return
        raise SystemExit(
            show_risk_summary(
                conn=conn,
                entity_name=entity_name,
                depth=depth,
                min_confidence=min_confidence,
                max_edges_per_node=max_edges_per_node,
                core_only=core_only,
                min_weight=min_weight,
            )
        )
    finally:
        conn.close()


def get_workflows(
    conn: sqlite3.Connection,
    entity_id: int,
    workflow_type: str | None = None,
) -> list[sqlite3.Row]:
    if workflow_type:
        return conn.execute(
            """
            SELECT id, name, workflow_type, source_kind, source_file
            FROM workflows
            WHERE entity_id = ?
              AND workflow_type = ?
            ORDER BY workflow_type, name
            """,
            (entity_id, workflow_type),
        ).fetchall()

    return conn.execute(
        """
        SELECT id, name, workflow_type, source_kind, source_file
        FROM workflows
        WHERE entity_id = ?
        ORDER BY workflow_type, name
        """,
        (entity_id,),
    ).fetchall()


def show_workflow_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    wfs = get_workflows(conn, entity["id"])

    print_header(f"WORKFLOWS: {entity_name}")

    if not wfs:
        click.echo("No workflows discovered.")
        return 0

    by_type: dict[str, list[sqlite3.Row]] = {}
    for wf in wfs:
        by_type.setdefault(wf["workflow_type"], []).append(wf)

    for wf_type in sorted(by_type.keys()):
        print_section(wf_type.upper())
        for wf in by_type[wf_type]:
            src = wf["source_kind"]
            source_file = wf["source_file"] or ""
            click.echo(f"  {wf['name']}   [source={src} {source_file}]")

    return 0


def show_flow_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    print_header(f"END-TO-END FLOW: {entity_name}")

    roots = get_root_symbols(conn, entity["id"], 0.75)

    print_section("Core Roots (>= 0.75)")
    for r in roots:
        click.echo(f"  {r['role']:<28} {r['name']}")

    db_tables = _collect_entity_db_tables(conn, entity["id"])

    print_section("DB Schema")
    if db_tables:
        for t in db_tables:
            pkeys = t["primary_keys"] or ""
            pkey_str = f"  pk=[{pkeys}]" if pkeys else ""
            click.echo(f"  {t['table_name']:<40} {t['field_count']} fields{pkey_str}")
    else:
        click.echo(
            "  no db table mapped (entity_occurrences.table_name is NULL or not in dbschema)"
        )

    wfs = get_workflows(conn, entity["id"])

    if wfs:
        by_type: dict[str, list[sqlite3.Row]] = {}
        for wf in wfs:
            by_type.setdefault(wf["workflow_type"], []).append(wf)

        for wf_type in sorted(by_type.keys()):
            print_section(f"{wf_type} workflows")
            for wf in by_type[wf_type]:
                src = wf["source_kind"]
                source_file = wf["source_file"] or ""
                click.echo(f"  {wf['name']}   [source={src} {source_file}]")
    else:
        print_section("Workflows")
        click.echo("  none discovered yet")

    return 0


def show_access_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    rows = conn.execute(
        """
        SELECT
            eal.surface,
            eal.record_id,
            eal.link_type,
            eal.evidence_file_id,
            ef.path AS evidence_file,
            eal.notes,
            CASE
                WHEN eal.surface = 'security_operation' THEN (
                    SELECT so.op_key
                    FROM security_operations so
                    WHERE so.id = eal.record_id
                )
                WHEN eal.surface = 'security_policy' THEN (
                    SELECT sp.policy_name
                    FROM security_policies sp
                    WHERE sp.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu' THEN (
                    SELECT COALESCE(sm.menu_name, sm.module, '(menu)')
                    FROM security_menus sm
                    WHERE sm.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu_item' THEN (
                    SELECT smi.item_path
                    FROM security_menu_items smi
                    WHERE smi.id = eal.record_id
                )
                WHEN eal.surface = 'dbschema_table' THEN (
                    SELECT dt.table_name
                    FROM dbschema_tables dt
                    WHERE dt.id = eal.record_id
                )
                WHEN eal.surface = 'workflow' THEN (
                    SELECT wf.name
                    FROM workflows wf
                    WHERE wf.id = eal.record_id
                )
                WHEN eal.surface = 'rest_endpoint' THEN (
                    SELECT re.method || ' ' || re.path
                    FROM rest_endpoints re
                    WHERE re.id = eal.record_id
                )
                ELSE '(unknown)'
            END AS label,
            CASE
                WHEN eal.surface = 'security_operation' THEN (
                    SELECT so.source_file
                    FROM security_operations so
                    WHERE so.id = eal.record_id
                )
                WHEN eal.surface = 'security_policy' THEN (
                    SELECT sp.source_file
                    FROM security_policies sp
                    WHERE sp.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu' THEN (
                    SELECT sm.source_file
                    FROM security_menus sm
                    WHERE sm.id = eal.record_id
                )
                WHEN eal.surface = 'security_menu_item' THEN (
                    SELECT sm.source_file
                    FROM security_menu_items smi
                    JOIN security_menus sm ON sm.id = smi.menu_id
                    WHERE smi.id = eal.record_id
                )
                WHEN eal.surface = 'dbschema_table' THEN (
                    SELECT dt.source_file
                    FROM dbschema_tables dt
                    WHERE dt.id = eal.record_id
                )
                WHEN eal.surface = 'workflow' THEN (
                    SELECT wf.source_file
                    FROM workflows wf
                    WHERE wf.id = eal.record_id
                )
                WHEN eal.surface = 'rest_endpoint' THEN (
                    SELECT f.path
                    FROM rest_endpoints re
                    JOIN files f ON f.id = re.file_id
                    WHERE re.id = eal.record_id
                )
                ELSE NULL
            END AS source_file
        FROM entity_access_links eal
        LEFT JOIN files ef
          ON ef.id = eal.evidence_file_id
        WHERE eal.entity_id = ?
        ORDER BY eal.surface, eal.link_type, label
        """,
        (entity["id"],),
    ).fetchall()

    print_header(f"ENTITY ACCESS GRAPH: {entity_name}")

    if not rows:
        click.echo(
            "No entity access links found. Run build_entity_access_links.py build first."
        )
        return 0

    current_surface = None
    for row in rows:
        if current_surface != row["surface"]:
            current_surface = row["surface"]
            print_section(current_surface.upper())

        source_file = row["source_file"] or ""
        evidence_file = row["evidence_file"] or ""
        click.echo(
            f"  [{row['link_type']:<15}] {row['label']} "
            f"(record_id={row['record_id']}, source={source_file}, evidence={evidence_file})"
        )

        if row["surface"] == "dbschema_table":
            fields = conn.execute(
                """
                SELECT field_name, field_type
                FROM dbschema_fields
                WHERE dbschema_table_id = ?
                ORDER BY field_name
                """,
                (row["record_id"],),
            ).fetchall()
            if fields:
                for f in fields:
                    ftype = f["field_type"] or "?"
                    click.echo(f"    {f['field_name']:<40} {ftype}")

    return 0


def show_openapispec_view(conn: sqlite3.Connection, entity_name: str) -> int:
    entity = get_entity(conn, entity_name)
    if not entity:
        click.echo(f"Entity not found: {entity_name}")
        return 1

    rows = conn.execute(
        """
        SELECT source_text, mapping_type
        FROM entity_mappings
        WHERE entity_id = ?
          AND mapping_type LIKE 'openapispec_%'
        ORDER BY mapping_type, source_text
        """,
        (entity["id"],),
    ).fetchall()

    print_header(f"OPENAPI SPEC FILES: {entity_name}")

    if not rows:
        click.echo("No openapispec mappings found.")
        return 0

    for r in rows:
        click.echo(f"[{r['mapping_type']:<25}] {r['source_text']}")
    return 0


if __name__ == "__main__":
    cli()
