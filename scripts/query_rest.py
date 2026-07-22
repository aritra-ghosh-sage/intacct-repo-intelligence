#!/usr/bin/env python3

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import click

try:
    from catalog.db import get_connection
    from catalog.rest_coverage import coverage_rows, coverage_summary
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection
    from catalog.rest_coverage import coverage_rows, coverage_summary

try:
    from ._query_json import emit_json, error_response, success_response
except ImportError:
    from _query_json import emit_json, error_response, success_response

DEFAULT_DB = "catalog/catalog.db"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _require_tables(conn: sqlite3.Connection, table_names: list[str]) -> None:
    missing = [name for name in table_names if not _table_exists(conn, name)]
    if missing:
        missing_csv = ", ".join(sorted(missing))
        raise click.ClickException(
            f"Required table(s) missing: {missing_csv}. "
            "Run OpenAPI scan/link and REST build pipeline first."
        )


def _render_endpoint(row: sqlite3.Row) -> None:
    click.echo(f"[{row['method']}] {row['path']}")
    click.echo(f"  entity: {row['entity_name'] or '(unmapped)'}")
    click.echo(
        "  schema/openapi: "
        f"canonical={row['openapi_canonical_name'] or '(unknown)'} | "
        f"mapped_to={row['openapi_mapped_to'] or '(none)'} | "
        f"kind={row['openapi_kind'] or '(unknown)'}"
    )
    click.echo(f"  openapi_file: {row['openapi_file_path'] or '(unknown)'}")
    click.echo(
        f"  handler_symbol: {row['handler_symbol_name'] or '(none)'}"
        + (
            f" ({row['handler_symbol_kind']})"
            if row["handler_symbol_kind"]
            else ""
        )
    )
    click.echo(f"  endpoint_file: {row['endpoint_file_path'] or '(unknown)'}")


def _render_related_handlers(rows: list[sqlite3.Row]) -> None:
    if not rows:
        return
    click.echo("  related_code_handlers:")
    for row in rows:
        click.echo(
            "    - "
            f"{row['symbol_name']} ({row['symbol_kind']}, {row['symbol_language']}) "
            f"via {row['relationship_type']} "
            f"(confidence={row['confidence']})"
        )


def _endpoint_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "endpoint_id": row["endpoint_id"],
        "method": row["method"],
        "path": row["path"],
        "entity_name": row["entity_name"],
        "openapi": {
            "file_path": row["openapi_file_path"],
            "kind": row["openapi_kind"],
            "canonical_name": row["openapi_canonical_name"],
            "mapped_to": row["openapi_mapped_to"],
        },
        "handler_symbol": {
            "id": row["handler_symbol_id"],
            "name": row["handler_symbol_name"],
            "kind": row["handler_symbol_kind"],
        },
        "endpoint_file_path": row["endpoint_file_path"],
    }


def _related_handler_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "symbol_name": row["symbol_name"],
        "symbol_kind": row["symbol_kind"],
        "symbol_language": row["symbol_language"],
        "relationship_type": row["relationship_type"],
        "confidence": row["confidence"],
    }


def _fetch_endpoint_rows(
    conn: sqlite3.Connection,
    where_sql: str,
    params: tuple[object, ...],
    limit: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT
            re.id AS endpoint_id,
            re.method,
            re.path,
            e.name AS entity_name,
            hs.id AS handler_symbol_id,
            hs.name AS handler_symbol_name,
            hs.kind AS handler_symbol_kind,
            f.path AS endpoint_file_path,
            oi.file_path AS openapi_file_path,
            oi.kind AS openapi_kind,
            oi.canonical_name AS openapi_canonical_name,
            oi.x_mapped_to AS openapi_mapped_to
        FROM rest_endpoints re
        LEFT JOIN entity_nodes e
          ON e.id = re.entity_id
        LEFT JOIN symbols hs
          ON hs.id = re.handler_symbol_id
        LEFT JOIN files f
          ON f.id = re.file_id
        LEFT JOIN openapispec_index oi
          ON oi.id = (
              SELECT oi2.id
              FROM openapispec_index oi2
              WHERE oi2.file_id = re.file_id
              ORDER BY
                  CASE
                      WHEN oi2.kind = 'operations' THEN 0
                      WHEN oi2.file_path LIKE '%/paths/%' THEN 1
                      ELSE 2
                  END,
                  oi2.id
              LIMIT 1
          )
        WHERE {where_sql}
        ORDER BY re.path, re.method
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


def _fetch_related_code_handlers(
    conn: sqlite3.Connection,
    handler_symbol_id: int,
    limit: int = 5,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT
            CASE
                WHEN r.source_symbol_id = ? THEN target_s.name
                ELSE source_s.name
            END AS symbol_name,
            CASE
                WHEN r.source_symbol_id = ? THEN target_s.kind
                ELSE source_s.kind
            END AS symbol_kind,
            CASE
                WHEN r.source_symbol_id = ? THEN target_s.language
                ELSE source_s.language
            END AS symbol_language,
            r.relationship_type,
            IFNULL(r.confidence, 0) AS confidence
        FROM relationships r
        LEFT JOIN symbols source_s ON source_s.id = r.source_symbol_id
        LEFT JOIN symbols target_s ON target_s.id = r.target_symbol_id
        WHERE (
            r.source_symbol_id = ?
            AND r.target_symbol_id IS NOT NULL
            AND IFNULL(target_s.language, '') <> 'yaml'
        ) OR (
            r.target_symbol_id = ?
            AND r.source_symbol_id IS NOT NULL
            AND IFNULL(source_s.language, '') <> 'yaml'
        )
        ORDER BY confidence DESC, symbol_kind, symbol_name
        LIMIT ?
        """,
        (
            handler_symbol_id,
            handler_symbol_id,
            handler_symbol_id,
            handler_symbol_id,
            handler_symbol_id,
            limit,
        ),
    ).fetchall()


def _fetch_symbol_matches(conn: sqlite3.Connection, symbol_name: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name, kind, language, parent_symbol
        FROM symbols
        WHERE name = ?
        ORDER BY kind, language, id
        """,
        (symbol_name,),
    ).fetchall()


def _fetch_endpoints_linked_to_symbol(
    conn: sqlite3.Connection,
    symbol_id: int,
    limit: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            re.id AS endpoint_id,
            re.method,
            re.path,
            e.name AS entity_name,
            hs.name AS handler_symbol_name,
            hs.kind AS handler_symbol_kind,
            hs.id AS handler_symbol_id,
            f.path AS endpoint_file_path,
            oi.file_path AS openapi_file_path,
            oi.kind AS openapi_kind,
            oi.canonical_name AS openapi_canonical_name,
            oi.x_mapped_to AS openapi_mapped_to,
            r.relationship_type,
            IFNULL(r.confidence, 0) AS relationship_confidence
        FROM relationships r
        JOIN rest_endpoints re
          ON re.handler_symbol_id = CASE
              WHEN r.source_symbol_id = ? THEN r.target_symbol_id
              ELSE r.source_symbol_id
          END
        LEFT JOIN entity_nodes e
          ON e.id = re.entity_id
        LEFT JOIN symbols hs
          ON hs.id = re.handler_symbol_id
        LEFT JOIN files f
          ON f.id = re.file_id
        LEFT JOIN openapispec_index oi
          ON oi.id = (
              SELECT oi2.id
              FROM openapispec_index oi2
              WHERE oi2.file_id = re.file_id
              ORDER BY
                  CASE
                      WHEN oi2.kind = 'operations' THEN 0
                      WHEN oi2.file_path LIKE '%/paths/%' THEN 1
                      ELSE 2
                  END,
                  oi2.id
              LIMIT 1
          )
        WHERE (
            r.source_symbol_id = ?
            AND r.target_symbol_id IS NOT NULL
        ) OR (
            r.target_symbol_id = ?
            AND r.source_symbol_id IS NOT NULL
        )
        ORDER BY re.path, re.method, relationship_confidence DESC
        LIMIT ?
        """,
        (symbol_id, symbol_id, symbol_id, limit),
    ).fetchall()


def _resolve_entity_by_name(
    conn: sqlite3.Connection,
    entity_name: str,
) -> tuple[sqlite3.Row | None, list[str]]:
    exact_row = conn.execute(
        """
        SELECT id, name
        FROM entity_nodes
        WHERE name = ?
        """,
        (entity_name,),
    ).fetchone()
    if exact_row is not None:
        return exact_row, []

    case_insensitive_rows = conn.execute(
        """
        SELECT id, name
        FROM entity_nodes
        WHERE LOWER(name) = LOWER(?)
        ORDER BY name
        """,
        (entity_name,),
    ).fetchall()
    if not case_insensitive_rows:
        return None, []
    if len(case_insensitive_rows) == 1:
        return case_insensitive_rows[0], []
    return None, [str(row["name"]) for row in case_insensitive_rows]


def _coverage_rows(
    conn: sqlite3.Connection, entity_id: int, version: str | None, limit: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return coverage_rows(conn, entity_id, version, limit)


@click.group()
def cli() -> None:
    """Query REST endpoints and OpenAPI evidence from catalog DB."""


@cli.command("entity")
@click.argument("entity_name")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=100, show_default=True, type=int)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def entity_command(entity_name: str, db: str, limit: int, json_output: bool) -> None:
    """Show endpoints that expose the given entity."""
    conn = get_connection(db)
    try:
        _require_tables(
            conn,
            ["rest_endpoints", "openapispec_index", "entity_nodes", "symbols", "files"],
        )
        entity, ambiguous_matches = _resolve_entity_by_name(conn, entity_name)
        if not entity:
            if ambiguous_matches:
                if json_output:
                    emit_json(
                        error_response(
                            command="entity",
                            args={"entity_name": entity_name, "db": db, "limit": limit},
                            code="ambiguous_entity_lookup",
                            message="Entity lookup is ambiguous.",
                            details={"matches": ambiguous_matches},
                        )
                    )
                    return
                click.echo(
                    f"Entity lookup is ambiguous for '{entity_name}'. "
                    "Use exact case from one of:"
                )
                for match in ambiguous_matches:
                    click.echo(f"  - {match}")
                return
            if json_output:
                emit_json(
                    error_response(
                        command="entity",
                        args={"entity_name": entity_name, "db": db, "limit": limit},
                        code="entity_not_found",
                        message=f"Entity not found: {entity_name}",
                        details={"entity_name": entity_name},
                    )
                )
                return
            click.echo(f"Entity not found: {entity_name}")
            return
        resolved_case_from = entity_name if entity["name"] != entity_name else None
        if entity["name"] != entity_name and not json_output:
            click.echo(
                f"Resolved entity case-insensitively: '{entity_name}' -> '{entity['name']}'"
            )

        rows = _fetch_endpoint_rows(
            conn,
            where_sql="re.entity_id = ?",
            params=(entity["id"],),
            limit=limit,
        )
        endpoint_source = "direct_entity_id"
        if not rows and _table_exists(conn, "entity_mappings"):
            rows = _fetch_endpoint_rows(
                conn,
                where_sql="""
                    re.file_id IN (
                        SELECT DISTINCT em.file_id
                        FROM entity_mappings em
                        WHERE em.entity_id = ?
                          AND em.file_id IS NOT NULL
                          AND em.mapping_type LIKE 'openapispec_%'
                    )
                """,
                params=(entity["id"],),
                limit=limit,
            )
            if rows:
                endpoint_source = "openapispec_file_mapping_fallback"
        if json_output:
            endpoint_rows = []
            for row in rows:
                endpoint_data = _endpoint_row_to_dict(row)
                if row["handler_symbol_id"] is not None:
                    related = _fetch_related_code_handlers(conn, row["handler_symbol_id"])
                    endpoint_data["related_code_handlers"] = [
                        _related_handler_row_to_dict(item) for item in related
                    ]
                else:
                    endpoint_data["related_code_handlers"] = []
                endpoint_rows.append(endpoint_data)
            data: dict[str, object] = {
                "entity": entity["name"],
                "entity_id": entity["id"],
                "endpoint_evidence_source": endpoint_source,
                "endpoints": endpoint_rows,
            }
            if resolved_case_from:
                data["resolved_case_from"] = resolved_case_from
            emit_json(
                success_response(
                    command="entity",
                    args={"entity_name": entity_name, "db": db, "limit": limit},
                    data=data,
                    summary={"endpoint_count": len(rows)},
                )
            )
            return

        click.echo(f"Entity: {entity['name']}")
        click.echo(f"Endpoints: {len(rows)}")
        click.echo(f"Endpoint evidence source: {endpoint_source}")
        click.echo("")
        if not rows:
            click.echo("No REST endpoints mapped for this entity.")
        else:
            for row in rows:
                _render_endpoint(row)
                if row["handler_symbol_id"] is not None:
                    related = _fetch_related_code_handlers(conn, row["handler_symbol_id"])
                    _render_related_handlers(related)
                click.echo("")
    finally:
        conn.close()


@cli.command("coverage")
@click.argument("entity_name")
@click.option("--version", help="Exact OpenAPI endpoint source version (for example s1).")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=500, show_default=True, type=int)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def coverage_command(
    entity_name: str, version: str | None, db: str, limit: int, json_output: bool
) -> None:
    """Show authored Gherkin REST use-case coverage for an entity/version."""
    conn = get_connection(db)
    try:
        _require_tables(
            conn,
            [
                "rest_endpoints", "entity_nodes", "repos",
                "test_cases", "test_requests", "test_endpoint_links",
                "test_entity_links", "test_diagnostics", "api_version_compatibility",
            ],
        )
        entity, ambiguous_matches = _resolve_entity_by_name(conn, entity_name)
        if entity is None:
            if json_output:
                emit_json(error_response(
                    command="coverage", args={"entity_name": entity_name, "version": version},
                    code="ambiguous_entity_lookup" if ambiguous_matches else "entity_not_found",
                    message="Entity lookup is ambiguous." if ambiguous_matches else f"Entity not found: {entity_name}",
                    details={"matches": ambiguous_matches} if ambiguous_matches else {"entity_name": entity_name},
                ))
                return
            if ambiguous_matches:
                raise click.ClickException(
                    f"Entity lookup is ambiguous for '{entity_name}': {', '.join(ambiguous_matches)}"
                )
            raise click.ClickException(f"Entity not found: {entity_name}")

        endpoints, diagnostics = _coverage_rows(conn, int(entity["id"]), version, limit)
        summary = coverage_summary(endpoints, diagnostics)
        args = {"entity_name": entity_name, "version": version, "db": db, "limit": limit}
        if json_output:
            emit_json(success_response(
                command="coverage", args=args,
                data={"entity": {"id": entity["id"], "name": entity["name"]},
                      "endpoint_coverage": endpoints, "diagnostics": diagnostics},
                summary=summary,
            ))
            return

        click.echo(f"Entity: {entity['name']}")
        click.echo(f"Endpoint version: {version or '(all)'}")
        click.echo(
            f"Endpoints: {summary['endpoint_count']} | active: {summary['active_covered_endpoint_count']} "
            f"| uncovered: {summary['uncovered_endpoint_count']}"
        )
        for item in endpoints:
            click.echo(
                f"[{item['coverage']}] {item['source_version'] or '(unknown)'} "
                f"{item['method']} {item['path']}"
            )
            for case in item["cases"]:
                click.echo(
                    f"  - {case['eligibility']}: {case['suite_id']} {case['case_name']} "
                    f"({case['feature_path']}:{case['step_line']})"
                )
        if diagnostics:
            click.echo("Diagnostics:")
            for diagnostic in diagnostics:
                click.echo(
                    f"  - {diagnostic['kind']}: {diagnostic['suite_id']} "
                    f"{diagnostic['feature_path']}:{diagnostic['line']} {diagnostic['message']}"
                )
    finally:
        conn.close()


@cli.command("path")
@click.argument("path_fragment")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=100, show_default=True, type=int)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def path_command(path_fragment: str, db: str, limit: int, json_output: bool) -> None:
    """Find endpoints by case-insensitive path fragment."""
    conn = get_connection(db)
    try:
        _require_tables(
            conn,
            ["rest_endpoints", "openapispec_index", "entity_nodes", "symbols", "files"],
        )
        rows = _fetch_endpoint_rows(
            conn,
            where_sql="LOWER(re.path) LIKE LOWER(?)",
            params=(f"%{path_fragment}%",),
            limit=limit,
        )
        if json_output:
            endpoints = [_endpoint_row_to_dict(row) for row in rows]
            emit_json(
                success_response(
                    command="path",
                    args={"path_fragment": path_fragment, "db": db, "limit": limit},
                    data={"endpoints": endpoints},
                    summary={"match_count": len(endpoints)},
                )
            )
            return
        click.echo(f"Path fragment: {path_fragment}")
        click.echo(f"Matches: {len(rows)}")
        click.echo("")
        if not rows:
            click.echo("No endpoints matched the path fragment.")
            return

        for row in rows:
            _render_endpoint(row)
            click.echo("")
    finally:
        conn.close()


@cli.command("endpoint")
@click.argument("endpoint_path")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def endpoint_command(endpoint_path: str, db: str, limit: int, json_output: bool) -> None:
    """Show all methods and evidence for an exact endpoint path."""
    conn = get_connection(db)
    try:
        _require_tables(
            conn,
            ["rest_endpoints", "openapispec_index", "entity_nodes", "symbols", "files"],
        )
        rows = _fetch_endpoint_rows(
            conn,
            where_sql="re.path = ?",
            params=(endpoint_path,),
            limit=limit,
        )
        if json_output:
            endpoint_rows = []
            for row in rows:
                endpoint_data = _endpoint_row_to_dict(row)
                if row["handler_symbol_id"] is not None:
                    related = _fetch_related_code_handlers(conn, row["handler_symbol_id"])
                    endpoint_data["related_code_handlers"] = [
                        _related_handler_row_to_dict(item) for item in related
                    ]
                else:
                    endpoint_data["related_code_handlers"] = []
                endpoint_rows.append(endpoint_data)
            emit_json(
                success_response(
                    command="endpoint",
                    args={"endpoint_path": endpoint_path, "db": db, "limit": limit},
                    data={"endpoints": endpoint_rows},
                    summary={"method_count": len(rows)},
                )
            )
            return
        click.echo(f"Endpoint path: {endpoint_path}")
        click.echo(f"Methods found: {len(rows)}")
        click.echo("")
        if not rows:
            click.echo("No endpoint rows found for the exact path.")
            return

        for row in rows:
            _render_endpoint(row)
            if row["handler_symbol_id"] is not None:
                related = _fetch_related_code_handlers(conn, row["handler_symbol_id"])
                _render_related_handlers(related)
            click.echo("")
    finally:
        conn.close()


@cli.command("symbol")
@click.argument("symbol_name")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=100, show_default=True, type=int)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def symbol_command(symbol_name: str, db: str, limit: int, json_output: bool) -> None:
    """Find endpoint evidence linked to an exact symbol name."""
    conn = get_connection(db)
    try:
        _require_tables(
            conn,
            [
                "rest_endpoints",
                "openapispec_index",
                "entity_nodes",
                "symbols",
                "files",
                "relationships",
            ],
        )
        symbol_rows = _fetch_symbol_matches(conn, symbol_name)
        if not symbol_rows:
            if json_output:
                emit_json(
                    error_response(
                        command="symbol",
                        args={"symbol_name": symbol_name, "db": db, "limit": limit},
                        code="symbol_not_found",
                        message=f"Symbol not found: {symbol_name}",
                        details={"symbol_name": symbol_name},
                    )
                )
                return
            click.echo(f"Symbol not found: {symbol_name}")
            return

        json_matches: list[dict[str, object]] = []
        if not json_output:
            click.echo(f"Symbol matches: {len(symbol_rows)} for name '{symbol_name}'")
            click.echo("")

        seen_endpoint_ids: set[int] = set()
        rendered_any = False

        for symbol_row in symbol_rows:
            match_links: list[dict[str, object]] = []
            if not json_output:
                click.echo(
                    f"[{symbol_row['id']}] {symbol_row['name']} "
                    f"({symbol_row['kind']}, {symbol_row['language']}) "
                    f"parent={symbol_row['parent_symbol'] or '(none)'}"
                )

            direct_rows = _fetch_endpoint_rows(
                conn,
                where_sql="re.handler_symbol_id = ?",
                params=(symbol_row["id"],),
                limit=limit,
            )
            for row in direct_rows:
                endpoint_id = row["endpoint_id"]
                if endpoint_id in seen_endpoint_ids:
                    continue
                rendered_any = True
                seen_endpoint_ids.add(endpoint_id)
                match_links.append(
                    {
                        "link_type": "direct_handler_link",
                        "endpoint": _endpoint_row_to_dict(row),
                    }
                )
                if not json_output:
                    click.echo("  direct_handler_link:")
                    _render_endpoint(row)

            linked_rows = _fetch_endpoints_linked_to_symbol(
                conn,
                symbol_id=symbol_row["id"],
                limit=limit,
            )
            for row in linked_rows:
                endpoint_id = row["endpoint_id"]
                if endpoint_id in seen_endpoint_ids:
                    continue
                rendered_any = True
                seen_endpoint_ids.add(endpoint_id)
                match_links.append(
                    {
                        "link_type": "relationship_link",
                        "relationship_type": row["relationship_type"],
                        "relationship_confidence": row["relationship_confidence"],
                        "endpoint": _endpoint_row_to_dict(row),
                    }
                )
                if not json_output:
                    click.echo(
                        "  relationship_link: "
                        f"{row['relationship_type']} "
                        f"(confidence={row['relationship_confidence']}) "
                        f"to handler={row['handler_symbol_name'] or '(none)'}"
                    )
                    _render_endpoint(row)

            if not json_output:
                click.echo("")
            if json_output:
                json_matches.append(
                    {
                        "id": symbol_row["id"],
                        "name": symbol_row["name"],
                        "kind": symbol_row["kind"],
                        "language": symbol_row["language"],
                        "parent_symbol": symbol_row["parent_symbol"],
                        "links": match_links,
                    }
                )

        if json_output:
            emit_json(
                success_response(
                    command="symbol",
                    args={"symbol_name": symbol_name, "db": db, "limit": limit},
                    data={"matches": json_matches},
                    summary={
                        "symbol_match_count": len(symbol_rows),
                        "linked_endpoint_count": len(seen_endpoint_ids),
                    },
                )
            )
            return
        if not rendered_any:
            click.echo("No REST endpoints linked to this symbol.")
    finally:
        conn.close()


if __name__ == "__main__":
    cli()
