"""Read exact API Registry evidence without traversing OpenAPI-derived facts."""

from __future__ import annotations

import base64
import json
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
REGISTRY_RELEASES = frozenset({"V1", "Beta", "V2i"})
_TABLES = ("repos", "files", "api_registry_entries", "api_registry_entry_links", "api_registry_issues")


class ApiRegistryQueryError(ValueError):
    """A machine-readable error shared by the Registry CLI and MCP tool."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def encode_cursor(offset: int) -> str:
    """Encode a non-negative offset using the catalog's opaque cursor form."""

    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """Decode an opaque next_cursor returned by a prior Registry query."""

    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor:
        raise ApiRegistryQueryError("invalid_cursor", "Cursor must be a non-empty opaque string.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded).decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ApiRegistryQueryError("invalid_cursor", "Cursor is invalid.") from exc
    if value < 0:
        raise ApiRegistryQueryError("invalid_cursor", "Cursor is invalid.")
    return value


def validate_limit(limit: int) -> int:
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ApiRegistryQueryError(
            "invalid_limit", f"limit must be an integer from 1 through {MAX_LIMIT}.", limit=limit
        )
    return limit


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _require_tables(conn: sqlite3.Connection) -> None:
    missing = sorted(table for table in _TABLES if not _table_exists(conn, table))
    if missing:
        raise ApiRegistryQueryError(
            "api_registry_unavailable",
            "API Registry catalog tables are unavailable; run the Registry builder first.",
            missing_tables=missing,
        )


def _repo_id(conn: sqlite3.Connection, repo_key: str) -> int:
    row = conn.execute("SELECT id FROM repos WHERE repo_key = ?", (repo_key,)).fetchone()
    if row is None:
        raise ApiRegistryQueryError(
            "repository_not_found", f"Repository not found: {repo_key}", repo_key=repo_key
        )
    return int(row["id"])


def _validate_release(registry_release: str | None) -> str | None:
    if registry_release is None:
        return None
    if registry_release not in REGISTRY_RELEASES:
        raise ApiRegistryQueryError(
            "invalid_release",
            "release must be one of: V1, Beta, V2i.",
            release=registry_release,
        )
    return registry_release


def _json_value(value: str) -> object:
    """Expose stored JSON structurally while retaining malformed historic evidence verbatim."""

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _page(rows: list[sqlite3.Row], limit: int, offset: int) -> tuple[list[sqlite3.Row], str | None]:
    return rows[:limit], encode_cursor(offset + limit) if len(rows) > limit else None


def _entry_payload(row: sqlite3.Row, links: list[dict[str, object]]) -> dict[str, object]:
    return {
        "entry_id": row["entry_id"],
        "release": row["registry_release"],
        "module": row["module"],
        "resource_kind": row["resource_kind"],
        "resource_path": row["resource_path"],
        "revision": row["revision"],
        "declared_hash": row["declared_hash"],
        "api_type": row["api_type"],
        "runtime_owner": row["runtime_owner"],
        "ui_metadata_hash": row["ui_metadata_hash"],
        "source_optional": bool(row["source_optional"]),
        "registry_provenance": {
            "file_path": row["registry_file_path"],
            "json_pointer": row["json_pointer"],
        },
        "payload": _json_value(row["payload_json"]),
        "source_components": links,
    }


def _entry_links(conn: sqlite3.Connection, repo_id: int, entry_ids: list[int]) -> dict[int, list[dict[str, object]]]:
    grouped: dict[int, list[dict[str, object]]] = {entry_id: [] for entry_id in entry_ids}
    if not entry_ids:
        return grouped
    placeholders = ", ".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"""
        SELECT link.entry_id, link.link_kind, link.component_hash, link.evidence_json,
               file.path AS file_path, link.source_pointer
        FROM api_registry_entry_links link
        JOIN files file ON file.id = link.source_file_id AND file.repo_id = link.repo_id
        WHERE link.repo_id = ? AND link.entry_id IN ({placeholders})
        ORDER BY link.entry_id, file.path, link.source_pointer, link.link_kind, link.id
        """,
        (repo_id, *entry_ids),
    ).fetchall()
    for row in rows:
        grouped[int(row["entry_id"])].append(
            {
                "kind": row["link_kind"],
                "component_hash": row["component_hash"],
                "provenance": {
                    "file_path": row["file_path"],
                    "json_pointer": row["source_pointer"],
                },
                "evidence": _json_value(row["evidence_json"]),
            }
        )
    return grouped


def _entry_rows(
    conn: sqlite3.Connection, *, repo_id: int, where_sql: str, params: tuple[object, ...], limit: int, offset: int
) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT entry.id AS entry_id, entry.registry_release, entry.json_pointer,
               entry.module, entry.resource_kind, entry.resource_path, entry.revision,
               entry.declared_hash, entry.api_type, entry.runtime_owner, entry.ui_metadata_hash,
               entry.source_optional, entry.payload_json, registry_file.path AS registry_file_path
        FROM api_registry_entries entry
        JOIN files registry_file
          ON registry_file.id = entry.registry_file_id AND registry_file.repo_id = entry.repo_id
        WHERE entry.repo_id = ? AND ({where_sql})
        ORDER BY entry.registry_release, entry.module, entry.resource_kind, entry.resource_path,
                 entry.json_pointer, entry.id
        LIMIT ? OFFSET ?
        """,
        (repo_id, *params, limit + 1, offset),
    ).fetchall()


def query_api_registry_releases(
    conn: sqlite3.Connection, *, repo_key: str, release: str | None = None, limit: int = 25, cursor: str | None = None
) -> dict[str, object]:
    """Return Registry release summaries scoped to one configured repository."""

    limit, offset, release = validate_limit(limit), decode_cursor(cursor), _validate_release(release)
    _require_tables(conn)
    repo_id = _repo_id(conn, repo_key)
    where_sql, params = "1 = 1", () if release is None else (release,)
    if release is not None:
        where_sql = "entry.registry_release = ?"
    rows = conn.execute(
        f"""
        WITH entries AS (
            SELECT entry.id, entry.repo_id, entry.registry_release, entry.source_optional,
                   registry_file.path AS registry_file_path
            FROM api_registry_entries entry
            JOIN files registry_file
              ON registry_file.id = entry.registry_file_id AND registry_file.repo_id = entry.repo_id
            WHERE entry.repo_id = ? AND ({where_sql})
        ), link_counts AS (
            SELECT link.repo_id, link.entry_id, COUNT(*) AS component_count
            FROM api_registry_entry_links link
            GROUP BY link.repo_id, link.entry_id
        ), issue_counts AS (
            SELECT issue.repo_id, issue.entry_id,
                   SUM(CASE WHEN issue.severity = 'error' THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN issue.severity = 'warning' THEN 1 ELSE 0 END) AS warning_count
            FROM api_registry_issues issue
            WHERE issue.entry_id IS NOT NULL
            GROUP BY issue.repo_id, issue.entry_id
        )
        SELECT entries.registry_release AS release, entries.registry_file_path,
               COUNT(*) AS entry_count,
               SUM(CASE WHEN entries.source_optional = 1 THEN 1 ELSE 0 END) AS source_optional_entry_count,
               SUM(CASE WHEN link_counts.component_count IS NOT NULL THEN 1 ELSE 0 END) AS linked_entry_count,
               COALESCE(SUM(link_counts.component_count), 0) AS source_component_count,
               COALESCE(SUM(issue_counts.error_count), 0) AS error_count,
               COALESCE(SUM(issue_counts.warning_count), 0) AS warning_count
        FROM entries
        LEFT JOIN link_counts ON link_counts.entry_id = entries.id AND link_counts.repo_id = entries.repo_id
        LEFT JOIN issue_counts ON issue_counts.entry_id = entries.id AND issue_counts.repo_id = entries.repo_id
        GROUP BY entries.registry_release, entries.registry_file_path
        ORDER BY entries.registry_release, entries.registry_file_path
        LIMIT ? OFFSET ?
        """,
        (repo_id, *params, limit + 1, offset),
    ).fetchall()
    page_rows, next_cursor = _page(rows, limit, offset)
    releases = [
        {
            "release": row["release"],
            "registry_provenance": {"file_path": row["registry_file_path"]},
            "entry_count": row["entry_count"],
            "source_optional_entry_count": row["source_optional_entry_count"],
            "linked_entry_count": row["linked_entry_count"],
            "source_component_count": row["source_component_count"],
            "issue_counts": {"error": row["error_count"] or 0, "warning": row["warning_count"] or 0},
        }
        for row in page_rows
    ]
    return {
        "repo_key": repo_key,
        "releases": releases,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {"release_count": len(releases)},
    }


def query_api_registry_resource(
    conn: sqlite3.Connection,
    *,
    repo_key: str,
    release: str,
    module: str,
    resource_kind: str,
    resource_path: str,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, object]:
    """Return exact Registry resource entries and their component-file evidence."""

    limit, offset = validate_limit(limit), decode_cursor(cursor)
    _validate_release(release)
    _require_tables(conn)
    repo_id = _repo_id(conn, repo_key)
    rows = _entry_rows(
        conn,
        repo_id=repo_id,
        where_sql="entry.registry_release = ? AND entry.module = ? AND entry.resource_kind = ? AND entry.resource_path = ?",
        params=(release, module, resource_kind, resource_path),
        limit=limit,
        offset=offset,
    )
    page_rows, next_cursor = _page(rows, limit, offset)
    entry_ids = [int(row["entry_id"]) for row in page_rows]
    links = _entry_links(conn, repo_id, entry_ids)
    entries = [_entry_payload(row, links[int(row["entry_id"])]) for row in page_rows]
    return {
        "repo_key": repo_key,
        "resource": {"release": release, "module": module, "kind": resource_kind, "path": resource_path},
        "entries": entries,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {"entry_count": len(entries), "source_component_count": sum(len(entry["source_components"]) for entry in entries)},
    }


def query_api_registry_file(
    conn: sqlite3.Connection,
    *,
    repo_key: str,
    file_path: str,
    release: str | None = None,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, object]:
    """Return Registry entries proven by one exact Registry source file."""

    limit, offset, release = validate_limit(limit), decode_cursor(cursor), _validate_release(release)
    _require_tables(conn)
    repo_id = _repo_id(conn, repo_key)
    conditions, params = ["registry_file.path = ?"], [file_path]
    if release is not None:
        conditions.append("entry.registry_release = ?")
        params.append(release)
    rows = _entry_rows(
        conn, repo_id=repo_id, where_sql=" AND ".join(conditions), params=tuple(params), limit=limit, offset=offset
    )
    page_rows, next_cursor = _page(rows, limit, offset)
    entry_ids = [int(row["entry_id"]) for row in page_rows]
    links = _entry_links(conn, repo_id, entry_ids)
    entries = [_entry_payload(row, links[int(row["entry_id"])]) for row in page_rows]
    return {
        "repo_key": repo_key,
        "registry_file": {"file_path": file_path, "release": release},
        "entries": entries,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {"entry_count": len(entries), "source_component_count": sum(len(entry["source_components"]) for entry in entries)},
    }


def query_api_registry_issues(
    conn: sqlite3.Connection, *, repo_key: str, release: str | None = None, limit: int = 25, cursor: str | None = None
) -> dict[str, object]:
    """Return Registry-local extraction and resolution diagnostics with provenance."""

    limit, offset, release = validate_limit(limit), decode_cursor(cursor), _validate_release(release)
    _require_tables(conn)
    repo_id = _repo_id(conn, repo_key)
    conditions, params = ["1 = 1"], []
    if release is not None:
        conditions.append("entry.registry_release = ?")
        params.append(release)
    rows = conn.execute(
        f"""
        SELECT issue.id AS issue_id, issue.issue_key, issue.severity, issue.issue_code,
               issue.message, issue.details_json, issue.source_pointer,
               source_file.path AS source_file_path,
               entry.registry_release, entry.json_pointer AS entry_pointer,
               registry_file.path AS registry_file_path, entry.module, entry.resource_kind,
               entry.resource_path
        FROM api_registry_issues issue
        JOIN files source_file ON source_file.id = issue.source_file_id AND source_file.repo_id = issue.repo_id
        LEFT JOIN api_registry_entries entry ON entry.id = issue.entry_id AND entry.repo_id = issue.repo_id
        LEFT JOIN files registry_file ON registry_file.id = entry.registry_file_id AND registry_file.repo_id = entry.repo_id
        WHERE issue.repo_id = ? AND ({' AND '.join(conditions)})
        ORDER BY issue.severity DESC, source_file.path, issue.source_pointer, issue.issue_code, issue.issue_key, issue.id
        LIMIT ? OFFSET ?
        """,
        (repo_id, *params, limit + 1, offset),
    ).fetchall()
    page_rows, next_cursor = _page(rows, limit, offset)
    issues = [
        {
            "issue_id": row["issue_id"],
            "issue_key": row["issue_key"],
            "severity": row["severity"],
            "code": row["issue_code"],
            "message": row["message"],
            "details": _json_value(row["details_json"]),
            "source_provenance": {"file_path": row["source_file_path"], "json_pointer": row["source_pointer"]},
            "entry": None if row["registry_release"] is None else {
                "release": row["registry_release"], "module": row["module"],
                "resource_kind": row["resource_kind"], "resource_path": row["resource_path"],
                "registry_provenance": {"file_path": row["registry_file_path"], "json_pointer": row["entry_pointer"]},
            },
        }
        for row in page_rows
    ]
    return {
        "repo_key": repo_key,
        "release": release,
        "issues": issues,
        "page": {"next_cursor": next_cursor, "truncated": next_cursor is not None},
        "summary": {"issue_count": len(issues), "error_count": sum(item["severity"] == "error" for item in issues), "warning_count": sum(item["severity"] == "warning" for item in issues)},
    }


def _emit_error(command: str, args: dict[str, object], error: ApiRegistryQueryError, json_output: bool) -> None:
    if json_output:
        emit_json(error_response(command=command, args=args, code=error.code, message=str(error), details=dict(error.details)))
        return
    raise click.ClickException(str(error))


@click.group()
def cli() -> None:
    """Query exact API Registry evidence and Registry-local diagnostics."""


def _run(command: str, args: dict[str, object], json_output: bool, query: Any) -> None:
    conn = get_connection(str(args["db"]))
    try:
        data = query(conn)
    except ApiRegistryQueryError as error:
        _emit_error(command, args, error, json_output)
        return
    finally:
        conn.close()
    if json_output:
        emit_json(success_response(command=command, args=args, data=data, summary=data["summary"]))
        return
    click.echo(json.dumps(data, ensure_ascii=True, indent=2))


def _common_options(command: Any) -> Any:
    command = click.option("--repo", "repo_key", required=True, help="Repository key from repository_list.")(command)
    command = click.option("--db", default=DEFAULT_DB, show_default=True)(command)
    command = click.option("--limit", default=25, show_default=True, type=int)(command)
    command = click.option("--cursor", default=None, help="Opaque next_cursor from the previous page.")(command)
    return click.option("--json", "json_output", is_flag=True, help="Emit stable JSON v1 output.")(command)


@cli.command("releases")
@click.option("--release", default=None, help="Exact Registry release: V1, Beta, or V2i.")
@_common_options
def releases_command(repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool, release: str | None) -> None:
    """List Registry releases with exact Registry-file provenance."""
    args = {"repo_key": repo_key, "release": release, "db": db, "limit": limit, "cursor": cursor}
    _run("api_registry_releases", args, json_output, lambda conn: query_api_registry_releases(conn, repo_key=repo_key, release=release, limit=limit, cursor=cursor))


@cli.command("resource")
@click.option("--release", required=True, help="Exact Registry release: V1, Beta, or V2i.")
@click.option("--module", required=True, help="Exact Registry module.")
@click.option("--resource-kind", required=True, help="Exact Registry resource kind.")
@click.option("--resource-path", required=True, help="Exact Registry resource path.")
@_common_options
def resource_command(repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool, release: str, module: str, resource_kind: str, resource_path: str) -> None:
    """Show one exact Registry resource and its component-file evidence."""
    args = {"repo_key": repo_key, "release": release, "module": module, "resource_kind": resource_kind, "resource_path": resource_path, "db": db, "limit": limit, "cursor": cursor}
    _run("api_registry_resource", args, json_output, lambda conn: query_api_registry_resource(conn, repo_key=repo_key, release=release, module=module, resource_kind=resource_kind, resource_path=resource_path, limit=limit, cursor=cursor))


@cli.command("file")
@click.option("--file-path", required=True, help="Exact repository-relative Registry source file path.")
@click.option("--release", default=None, help="Optional exact Registry release filter.")
@_common_options
def file_command(repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool, file_path: str, release: str | None) -> None:
    """Show entries emitted from one exact Registry source file."""
    args = {"repo_key": repo_key, "file_path": file_path, "release": release, "db": db, "limit": limit, "cursor": cursor}
    _run("api_registry_file", args, json_output, lambda conn: query_api_registry_file(conn, repo_key=repo_key, file_path=file_path, release=release, limit=limit, cursor=cursor))


@cli.command("issues")
@click.option("--release", default=None, help="Optional exact Registry release filter.")
@_common_options
def issues_command(repo_key: str, db: str, limit: int, cursor: str | None, json_output: bool, release: str | None) -> None:
    """List Registry-local diagnostics with source-file and pointer provenance."""
    args = {"repo_key": repo_key, "release": release, "db": db, "limit": limit, "cursor": cursor}
    _run("api_registry_issues", args, json_output, lambda conn: query_api_registry_issues(conn, repo_key=repo_key, release=release, limit=limit, cursor=cursor))


if __name__ == "__main__":
    cli()
