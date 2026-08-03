"""Read source-provenanced actionUI and NextGen catalog evidence."""

from __future__ import annotations

import base64
import sqlite3
import sys
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


DEFAULT_DB = "catalog/catalog.db"
MAX_LIMIT = 100
MAX_NESTED_CALL_LIMIT = 100
DETAIL_RECORD_KINDS = frozenset(
    {"artifacts", "fields", "events", "scripts", "includes", "references", "issues"}
)
RELATED_REFERENCE_KINDS = frozenset({"editor", "form_editor", "lister", "manager"})
DIRECT_REFERENCE_KINDS = frozenset({"direct", "direct_owner", "explicit_mapping"})


class UiQueryError(ValueError):
    """A machine-readable query error suitable for CLI and MCP callers."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def encode_cursor(offset: int) -> str:
    """Encode a non-negative SQLite offset using the catalog cursor format."""

    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """Decode an opaque base64url offset cursor."""

    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor:
        raise UiQueryError("invalid_cursor", "Cursor must be a non-empty opaque string.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded).decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UiQueryError("invalid_cursor", "Cursor is invalid.") from exc
    if value < 0:
        raise UiQueryError("invalid_cursor", "Cursor is invalid.")
    return value


def validate_limit(limit: int) -> int:
    """Keep CLI pagination compatible with the read-only MCP limit bounds."""

    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise UiQueryError(
            "invalid_limit", f"limit must be an integer from 1 through {MAX_LIMIT}.", limit=limit
        )
    return limit


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
        ).fetchone()
        is not None
    )


def _require_tables(conn: sqlite3.Connection, table_names: tuple[str, ...]) -> None:
    missing = [name for name in table_names if not _table_exists(conn, name)]
    if missing:
        raise UiQueryError(
            "ui_catalog_unavailable",
            "UI catalog tables are unavailable; run the UI catalog builder first.",
            missing_tables=sorted(missing),
        )


def _repo_id(conn: sqlite3.Connection, repo_key: str) -> int:
    row = conn.execute("SELECT id FROM repos WHERE repo_key = ?", (repo_key,)).fetchone()
    if row is None:
        raise UiQueryError("repository_not_found", f"Repository not found: {repo_key}", repo_key=repo_key)
    return int(row["id"])


def _entity_occurrence(
    conn: sqlite3.Connection, repo_id: int, entity_name: str
) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT entity_nodes.id AS entity_id, entity_nodes.name AS entity_name,
               entity_occurrences.id AS entity_occurrence_id
        FROM entity_nodes
        JOIN entity_occurrences ON entity_occurrences.entity_id = entity_nodes.id
        WHERE entity_occurrences.repo_id = ?
          AND entity_nodes.name = ?
        """,
        (repo_id, entity_name),
    ).fetchone()
    if row is None:
        raise UiQueryError(
            "entity_not_found",
            f"Entity not found in repository: {entity_name}",
            entity_name=entity_name,
        )
    return row


def _page(rows: list[sqlite3.Row], limit: int, offset: int) -> tuple[list[dict[str, Any]], str | None]:
    return (
        [dict(row) for row in rows[:limit]],
        encode_cursor(offset + limit) if len(rows) > limit else None,
    )


def _surface_from_row(row: dict[str, Any], references: list[dict[str, Any]]) -> dict[str, Any]:
    surface_kind = str(row.pop("surface_kind"))
    surface_family = "actionui" if surface_kind == "actionui_form" else "nextgen"
    return {
        "surface_key": row.pop("surface_key"),
        "surface_family": surface_family,
        "surface_kind": surface_kind,
        "display_name": row.pop("display_name"),
        "source_path": row.pop("source_path"),
        "references": references,
    }


def query_ui_impact(
    conn: sqlite3.Connection,
    *,
    entity_name: str,
    repo_key: str,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return one evidence-backed page of UI surfaces for an entity occurrence."""

    limit = validate_limit(limit)
    offset = decode_cursor(cursor)
    _require_tables(conn, ("repos", "entity_nodes", "entity_occurrences", "ui_surfaces", "ui_entity_references"))
    repo_id = _repo_id(conn, repo_key)
    entity = _entity_occurrence(conn, repo_id, entity_name)
    supported_kinds = tuple(sorted(DIRECT_REFERENCE_KINDS | RELATED_REFERENCE_KINDS))
    placeholders = ", ".join("?" for _ in supported_kinds)
    rows = conn.execute(
        f"""
        SELECT ui_surfaces.id AS surface_id, ui_surfaces.surface_key, ui_surfaces.surface_kind,
               ui_surfaces.display_name, ui_surfaces.source_path
        FROM ui_surfaces
        WHERE ui_surfaces.repo_id = ?
          AND EXISTS (
              SELECT 1
              FROM ui_entity_references
              WHERE ui_entity_references.repo_id = ui_surfaces.repo_id
                AND ui_entity_references.surface_id = ui_surfaces.id
                AND ui_entity_references.entity_occurrence_id = ?
                AND ui_entity_references.reference_kind IN ({placeholders})
          )
        ORDER BY ui_surfaces.surface_key, ui_surfaces.id
        LIMIT ? OFFSET ?
        """,
        (repo_id, entity["entity_occurrence_id"], *supported_kinds, limit + 1, offset),
    ).fetchall()
    page_rows, next_cursor = _page(rows, limit, offset)
    surface_ids = [int(row["surface_id"]) for row in page_rows]
    references_by_surface: dict[int, list[dict[str, Any]]] = {surface_id: [] for surface_id in surface_ids}
    if surface_ids:
        surface_placeholders = ", ".join("?" for _ in surface_ids)
        reference_rows = conn.execute(
            f"""
            SELECT surface_id, reference_kind, confidence, source_line, evidence_text
            FROM ui_entity_references
            WHERE repo_id = ?
              AND entity_occurrence_id = ?
              AND surface_id IN ({surface_placeholders})
              AND reference_kind IN ({placeholders})
            ORDER BY surface_id, reference_kind, id
            """,
            (repo_id, entity["entity_occurrence_id"], *surface_ids, *supported_kinds),
        ).fetchall()
        for reference in reference_rows:
            reference_kind = str(reference["reference_kind"])
            references_by_surface[int(reference["surface_id"])].append(
                {
                    "kind": reference_kind,
                    "relation": "related" if reference_kind in RELATED_REFERENCE_KINDS else "direct",
                    "confidence": reference["confidence"],
                    "source_line": reference["source_line"],
                    "evidence": reference["evidence_text"],
                }
            )
    surfaces = [
        _surface_from_row(row, references_by_surface[int(row.pop("surface_id"))])
        for row in page_rows
    ]
    return {
        "entity": {
            "id": entity["entity_id"],
            "name": entity["entity_name"],
            "occurrence_id": entity["entity_occurrence_id"],
            "repo_key": repo_key,
        },
        "surfaces": surfaces,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {
            "surface_count": len(surfaces),
            "direct_surface_count": sum(
                any(reference["relation"] == "direct" for reference in surface["references"])
                for surface in surfaces
            ),
            "related_surface_count": sum(
                any(reference["relation"] == "related" for reference in surface["references"])
                for surface in surfaces
            ),
        },
    }


def _surface(conn: sqlite3.Connection, repo_id: int, surface_key: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, surface_key, surface_kind, display_name, source_path
        FROM ui_surfaces
        WHERE repo_id = ? AND surface_key = ?
        """,
        (repo_id, surface_key),
    ).fetchone()
    if row is None:
        raise UiQueryError(
            "ui_surface_not_found", f"UI surface not found: {surface_key}", surface_key=surface_key
        )
    return row


def _detail_rows(
    conn: sqlite3.Connection, *, repo_id: int, surface_id: int, record_kind: str, limit: int, offset: int
) -> list[sqlite3.Row]:
    queries: dict[str, tuple[str, tuple[object, ...]]] = {
        "artifacts": (
            """SELECT id AS artifact_id, artifact_key, artifact_kind, source_path, start_line,
                      end_line, evidence_text, payload_json
               FROM ui_artifacts WHERE repo_id = ? AND surface_id = ?
               ORDER BY artifact_key, id LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
        "fields": (
            """SELECT ui_fields.id AS field_id, ui_artifacts.artifact_key, ui_artifacts.source_path,
                      ui_fields.field_key, ui_fields.field_name, ui_fields.field_path, ui_fields.label,
                      ui_fields.field_type, ui_fields.ordinal, ui_fields.source_line, ui_fields.evidence_text
               FROM ui_fields JOIN ui_artifacts ON ui_artifacts.id = ui_fields.artifact_id
                                                AND ui_artifacts.repo_id = ui_fields.repo_id
               WHERE ui_fields.repo_id = ? AND ui_artifacts.surface_id = ?
               ORDER BY ui_artifacts.artifact_key, ui_fields.ordinal, ui_fields.field_key, ui_fields.id
               LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
        "events": (
            """SELECT ui_events.id AS event_id, ui_artifacts.artifact_key, ui_artifacts.source_path,
                      ui_events.event_key, ui_events.event_type, ui_events.handler_name,
                      ui_events.handler_expression, ui_events.source_line, ui_events.evidence_text
               FROM ui_events JOIN ui_artifacts ON ui_artifacts.id = ui_events.artifact_id
                                                AND ui_artifacts.repo_id = ui_events.repo_id
               WHERE ui_events.repo_id = ? AND ui_artifacts.surface_id = ?
               ORDER BY ui_artifacts.artifact_key, ui_events.event_key, ui_events.id
               LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
        "scripts": (
            """SELECT id AS dependency_id, dependency_key, script_path, load_scope, resolution_status,
                      evidence_text, source_line
               FROM ui_script_dependencies
               WHERE repo_id = ? AND surface_id = ?
               ORDER BY dependency_key, id LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
        "includes": (
            """SELECT ui_artifact_includes.id AS include_id, source_artifact.artifact_key AS source_artifact_key,
                      target_artifact.artifact_key AS target_artifact_key, raw_include_path, resolved_path,
                      resolution_status, source_line, ui_artifact_includes.evidence_text
               FROM ui_artifact_includes
               JOIN ui_artifacts source_artifact ON source_artifact.id = ui_artifact_includes.source_artifact_id
               LEFT JOIN ui_artifacts target_artifact ON target_artifact.id = ui_artifact_includes.target_artifact_id
               WHERE ui_artifact_includes.repo_id = ? AND source_artifact.surface_id = ?
               ORDER BY source_artifact.artifact_key, include_key, ui_artifact_includes.id
               LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
        "references": (
            """SELECT ui_entity_references.id AS reference_id, entity_nodes.name AS entity_name,
                      ui_entity_references.reference_kind, ui_entity_references.confidence,
                      ui_artifacts.artifact_key AS evidence_artifact_key,
                      ui_entity_references.source_line, ui_entity_references.evidence_text
               FROM ui_entity_references
               JOIN entity_nodes ON entity_nodes.id = ui_entity_references.entity_id
               JOIN ui_artifacts ON ui_artifacts.id = ui_entity_references.evidence_artifact_id
               WHERE ui_entity_references.repo_id = ? AND ui_entity_references.surface_id = ?
               ORDER BY entity_nodes.name, ui_entity_references.reference_kind, ui_entity_references.id
               LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
        "issues": (
                """SELECT ui_resolution_issues.id AS issue_id, ui_resolution_issues.issue_key,
                      ui_resolution_issues.severity, ui_resolution_issues.issue_code,
                      ui_resolution_issues.message, ui_resolution_issues.evidence_text,
                      ui_artifacts.artifact_key AS artifact_key,
                      ui_events.event_key AS event_key,
                      ui_script_dependencies.dependency_key AS dependency_key
               FROM ui_resolution_issues
               LEFT JOIN ui_artifacts ON ui_artifacts.id = ui_resolution_issues.artifact_id
               LEFT JOIN ui_events ON ui_events.id = ui_resolution_issues.event_id
               LEFT JOIN ui_script_dependencies ON ui_script_dependencies.id = ui_resolution_issues.dependency_id
               WHERE ui_resolution_issues.repo_id = ? AND ui_resolution_issues.surface_id = ?
               ORDER BY severity DESC, issue_code, issue_key, ui_resolution_issues.id
               LIMIT ? OFFSET ?""",
            (repo_id, surface_id, limit + 1, offset),
        ),
    }
    sql, params = queries[record_kind]
    return conn.execute(sql, params).fetchall()


def _event_calls(conn: sqlite3.Connection, repo_id: int, event_id: int) -> tuple[list[dict[str, Any]], bool]:
    rows = conn.execute(
        """
        SELECT ui_event_calls.id AS event_call_id, ui_event_calls.call_key,
               ui_event_calls.handler_name, ui_event_calls.resolution_status,
               ui_event_calls.resolution_reason, ui_event_calls.evidence_text,
               ui_script_dependencies.dependency_key, ui_script_dependencies.script_path,
               symbols.id AS handler_symbol_id, symbols.name AS handler_symbol_name
        FROM ui_event_calls
        LEFT JOIN ui_script_dependencies ON ui_script_dependencies.id = ui_event_calls.dependency_id
        LEFT JOIN symbols ON symbols.id = ui_event_calls.handler_symbol_id
        WHERE ui_event_calls.repo_id = ? AND ui_event_calls.event_id = ?
        ORDER BY ui_event_calls.call_key, ui_event_calls.id
        LIMIT ?
        """,
        (repo_id, event_id, MAX_NESTED_CALL_LIMIT + 1),
    ).fetchall()
    return [dict(row) for row in rows[:MAX_NESTED_CALL_LIMIT]], len(rows) > MAX_NESTED_CALL_LIMIT


def query_ui_surface_detail(
    conn: sqlite3.Connection,
    *,
    surface_key: str,
    repo_key: str,
    record_kind: str,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return one paged evidence family for a precise UI surface."""

    limit = validate_limit(limit)
    offset = decode_cursor(cursor)
    if record_kind not in DETAIL_RECORD_KINDS:
        raise UiQueryError(
            "invalid_record_kind",
            "record_kind must be one of: " + ", ".join(sorted(DETAIL_RECORD_KINDS)),
            record_kind=record_kind,
        )
    _require_tables(
        conn,
        (
            "repos", "ui_surfaces", "ui_artifacts", "ui_entity_references",
            "ui_artifact_includes", "ui_fields", "ui_events", "ui_script_dependencies",
            "ui_event_calls", "ui_resolution_issues",
        ),
    )
    repo_id = _repo_id(conn, repo_key)
    surface = _surface(conn, repo_id, surface_key)
    rows = _detail_rows(
        conn, repo_id=repo_id, surface_id=int(surface["id"]), record_kind=record_kind,
        limit=limit, offset=offset,
    )
    records, next_cursor = _page(rows, limit, offset)
    if record_kind == "events":
        for record in records:
            calls, calls_truncated = _event_calls(conn, repo_id, int(record["event_id"]))
            record["calls"] = calls
            record["calls_truncated"] = calls_truncated
    return {
        "surface": {
            "surface_key": surface["surface_key"],
            "surface_family": "actionui" if surface["surface_kind"] == "actionui_form" else "nextgen",
            "surface_kind": surface["surface_kind"],
            "display_name": surface["display_name"],
            "source_path": surface["source_path"],
            "repo_key": repo_key,
        },
        "record_kind": record_kind,
        "records": records,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {"record_count": len(records)},
    }


def query_ui_source_diagnostics(
    conn: sqlite3.Connection,
    *,
    repo_key: str,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return source-only UI diagnostics without manufacturing a surface link."""

    limit = validate_limit(limit)
    offset = decode_cursor(cursor)
    _require_tables(conn, ("repos", "files", "ui_source_diagnostics"))
    repo_id = _repo_id(conn, repo_key)
    rows = conn.execute(
        """
        SELECT diagnostic_key,source_path,source_kind,source_pointer,severity,
               diagnostic_code,message,evidence_text
        FROM ui_source_diagnostics
        WHERE repo_id=?
        ORDER BY source_path, source_pointer, diagnostic_code, diagnostic_key, id
        LIMIT ? OFFSET ?
        """,
        (repo_id, limit + 1, offset),
    ).fetchall()
    diagnostics, next_cursor = _page(rows, limit, offset)
    return {
        "repo_key": repo_key,
        "diagnostics": diagnostics,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {"diagnostic_count": len(diagnostics)},
    }


def _emit_error(command: str, args: dict[str, object], error: UiQueryError, json_output: bool) -> None:
    if json_output:
        emit_json(
            error_response(
                command=command,
                args=args,
                code=error.code,
                message=str(error),
                details=dict(error.details),
            )
        )
        return
    raise click.ClickException(str(error))


@click.group()
def cli() -> None:
    """Query actionUI and NextGen catalog evidence."""


@cli.command("impact")
@click.argument("entity_name")
@click.option("--repo", "repo_key", required=True, help="Repository key from repository_list.")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=25, show_default=True, type=int)
@click.option("--cursor", default=None, help="Opaque next_cursor from the previous page.")
@click.option("--json", "json_output", is_flag=True, help="Emit stable JSON output.")
def impact_command(
    entity_name: str, repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool
) -> None:
    """Show actionUI and NextGen impact for an entity in one repository."""

    args = {"entity_name": entity_name, "repo_key": repo_key, "db": db, "limit": limit, "cursor": cursor}
    conn = get_connection(db)
    try:
        data = query_ui_impact(
            conn, entity_name=entity_name, repo_key=repo_key, limit=limit, cursor=cursor
        )
    except UiQueryError as error:
        _emit_error("ui_impact", args, error, json_output)
        return
    finally:
        conn.close()
    if json_output:
        emit_json(success_response(command="ui_impact", args=args, data=data, summary=data["summary"]))
        return
    click.echo(f"Entity: {data['entity']['name']} ({repo_key})")
    for surface in data["surfaces"]:
        references = ", ".join(
            f"{reference['relation']}:{reference['kind']}"
            for reference in surface["references"]
        )
        click.echo(
            f"[{surface['surface_family']}] {surface['surface_key']} "
            f"({references or 'no reference evidence'})"
        )


@cli.command("detail")
@click.argument("surface_key")
@click.argument("record_kind")
@click.option("--repo", "repo_key", required=True, help="Repository key from repository_list.")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=25, show_default=True, type=int)
@click.option("--cursor", default=None, help="Opaque next_cursor from the previous page.")
@click.option("--json", "json_output", is_flag=True, help="Emit stable JSON output.")
def detail_command(
    surface_key: str, record_kind: str, repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool
) -> None:
    """Show one paged evidence family for an exact UI surface."""

    args = {"surface_key": surface_key, "record_kind": record_kind, "repo_key": repo_key, "db": db, "limit": limit, "cursor": cursor}
    conn = get_connection(db)
    try:
        data = query_ui_surface_detail(
            conn, surface_key=surface_key, repo_key=repo_key, record_kind=record_kind,
            limit=limit, cursor=cursor,
        )
    except UiQueryError as error:
        _emit_error("ui_surface_detail", args, error, json_output)
        return
    finally:
        conn.close()
    if json_output:
        emit_json(success_response(command="ui_surface_detail", args=args, data=data, summary=data["summary"]))
        return
    click.echo(f"Surface: {data['surface']['surface_key']} ({data['surface']['surface_family']})")
    click.echo(f"{record_kind}: {data['summary']['record_count']}")


@cli.command("source-diagnostics")
@click.option("--repo", "repo_key", required=True, help="Repository key from repository_list.")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", default=25, show_default=True, type=int)
@click.option("--cursor", default=None, help="Opaque next_cursor from the previous page.")
@click.option("--json", "json_output", is_flag=True, help="Emit stable JSON output.")
def source_diagnostics_command(
    repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool
) -> None:
    """Show unattached, source-only UI diagnostics for one repository."""

    args = {"repo_key": repo_key, "db": db, "limit": limit, "cursor": cursor}
    conn = get_connection(db)
    try:
        data = query_ui_source_diagnostics(
            conn, repo_key=repo_key, limit=limit, cursor=cursor
        )
    except UiQueryError as error:
        _emit_error("ui_source_diagnostics", args, error, json_output)
        return
    finally:
        conn.close()
    if json_output:
        emit_json(
            success_response(
                command="ui_source_diagnostics",
                args=args,
                data=data,
                summary=data["summary"],
            )
        )
        return
    click.echo(f"UI source diagnostics: {data['summary']['diagnostic_count']} ({repo_key})")
    for diagnostic in data["diagnostics"]:
        click.echo(
            f"[{diagnostic['severity']}] {diagnostic['diagnostic_code']} "
            f"{diagnostic['source_path']}"
        )


if __name__ == "__main__":
    cli()
