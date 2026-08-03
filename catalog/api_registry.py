"""Deterministic extraction for the three API Registry source documents.

Registry is intentionally a source-evidence family of its own.  In particular,
this module does not read or write ``openapispec_index`` (which is reset by the
OpenAPI scanner) and it never derives entity, endpoint, or UI facts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Any

REGISTRY_SOURCES: tuple[tuple[str, str], ...] = (
    ("V1", "app/source/api/registries/RegistryV1.json"),
    ("Beta", "app/source/api/registries/RegistryBeta.json"),
    ("V2i", "app/source/api/registries/RegistryV2i.json"),
)


@dataclass(frozen=True)
class RegistryDiagnostic:
    """A source-only failure record that is safe to persist without an entry.

    Registry parsing deliberately stops before it can manufacture a partial
    entry.  These records retain the real Registry file and RFC 6901 pointer
    which caused that stop, so a standalone build can leave actionable
    evidence without publishing partial Registry entries or links.
    """

    registry_release: str
    registry_path: str
    source_pointer: str
    issue_code: str
    message: str
    details: dict[str, object]

    @property
    def issue_key(self) -> str:
        """Return a stable identity for exactly one source-visible failure."""
        return (
            f"api_registry:{self.registry_release}:{self.registry_path}:"
            f"{self.source_pointer}:{self.issue_code}"
        )


class RegistryExtractionError(RuntimeError):
    """Raised when Registry source cannot be represented without weak evidence."""

    def __init__(
        self, message: str, *, diagnostics: Iterable[RegistryDiagnostic] = ()
    ) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)
        # The standalone command uses this only after ``build_api_registry``
        # has replaced Registry diagnostics with these source-only facts.
        self.diagnostics_persisted = False


def _registry_diagnostic(
    *,
    registry_release: str,
    registry_path: str,
    source_pointer: str,
    issue_code: str,
    message: str,
    details: dict[str, object],
) -> RegistryDiagnostic:
    return RegistryDiagnostic(
        registry_release=registry_release,
        registry_path=registry_path,
        source_pointer=source_pointer,
        issue_code=issue_code,
        message=message,
        details=details,
    )


@dataclass(frozen=True)
class RegistryEntry:
    registry_release: str
    registry_path: str
    json_pointer: str
    module: str
    resource_kind: str
    resource_path: str
    revision: str | None
    declared_hash: str | None
    api_type: str | None
    runtime_owner: str | None
    ui_metadata_hash: str | None
    source_optional: bool
    payload_json: str


def _json_pointer(parts: Iterable[str]) -> str:
    """Encode a RFC 6901 pointer without relying on JSON object order."""
    encoded = "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)
    return f"/{encoded}" if encoded else ""


def _optional_text(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    return value if isinstance(value, str) else None


def _is_registry_leaf(value: object) -> bool:
    return (
        isinstance(value, dict)
        and "revision" in value
        and "hash" in value
    )


def _is_source_optional(payload: dict[str, Any]) -> bool:
    """The only source-optional Registry records are core-service sentinels."""
    return (
        payload.get("revision") == "ALL"
        and payload.get("hash") == "-1"
        and payload.get("type") == "coreService"
    )


def extract_registry_entries(
    registry_release: str, registry_path: str, document: object
) -> list[RegistryEntry]:
    """Return the exact leaf entries in a Registry document in stable order.

    A Registry leaf is a mapping with both ``revision`` and ``hash``.  Some
    historical system-view entries intentionally have no ``type``; preserving
    the whole payload avoids manufacturing that absent classification.
    """
    if not isinstance(document, dict):
        message = f"{registry_path} must contain a top-level JSON object"
        raise RegistryExtractionError(
            message,
            diagnostics=(
                _registry_diagnostic(
                    registry_release=registry_release,
                    registry_path=registry_path,
                    source_pointer="",
                    issue_code="invalid_registry_document",
                    message=message,
                    details={"expected": "object", "actual_type": type(document).__name__},
                ),
            ),
        )

    entries: list[RegistryEntry] = []

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            required_fields = ("revision", "hash")
            present_fields = tuple(field for field in required_fields if field in value)
            if present_fields:
                missing_fields = tuple(
                    field for field in required_fields if field not in value
                )
                invalid_fields = tuple(
                    field
                    for field in present_fields
                    if not isinstance(value[field], str)
                )
                if missing_fields or invalid_fields:
                    problems = [
                        *(f"missing required field {field!r}" for field in missing_fields),
                        *(f"field {field!r} must be a string" for field in invalid_fields),
                    ]
                    source_pointer = _json_pointer(path)
                    message = (
                        f"{registry_path}{source_pointer} has an invalid Registry leaf: "
                        + "; ".join(problems)
                    )
                    raise RegistryExtractionError(
                        message,
                        diagnostics=(
                            _registry_diagnostic(
                                registry_release=registry_release,
                                registry_path=registry_path,
                                source_pointer=source_pointer,
                                issue_code="invalid_registry_leaf",
                                message=message,
                                details={
                                    "missing_fields": list(missing_fields),
                                    "invalid_fields": list(invalid_fields),
                                },
                            ),
                        ),
                    )
        if _is_registry_leaf(value):
            if len(path) < 3:
                source_pointer = _json_pointer(path)
                message = (
                    f"{registry_path}{source_pointer} has no module/resource identity"
                )
                raise RegistryExtractionError(
                    message,
                    diagnostics=(
                        _registry_diagnostic(
                            registry_release=registry_release,
                            registry_path=registry_path,
                            source_pointer=source_pointer,
                            issue_code="missing_registry_resource_identity",
                            message=message,
                            details={"path_depth": len(path)},
                        ),
                    ),
                )
            assert isinstance(value, dict)
            entries.append(
                RegistryEntry(
                    registry_release=registry_release,
                    registry_path=registry_path,
                    json_pointer=_json_pointer(path),
                    module=path[0],
                    resource_kind=path[1],
                    resource_path="/".join(path[2:]),
                    revision=_optional_text(value, "revision"),
                    declared_hash=_optional_text(value, "hash"),
                    api_type=_optional_text(value, "type"),
                    runtime_owner=_optional_text(value, "runtimeOwner"),
                    ui_metadata_hash=_optional_text(value, "uiMetadataHash"),
                    source_optional=_is_source_optional(value),
                    payload_json=json.dumps(
                        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                    ),
                )
            )
            return
        if not isinstance(value, dict):
            return
        for key in sorted(value):
            child = value[key]
            if not isinstance(key, str):
                source_pointer = _json_pointer(path)
                message = f"{registry_path}{source_pointer} has a non-string object key"
                raise RegistryExtractionError(
                    message,
                    diagnostics=(
                        _registry_diagnostic(
                            registry_release=registry_release,
                            registry_path=registry_path,
                            source_pointer=source_pointer,
                            issue_code="non_string_registry_key",
                            message=message,
                            details={"key_type": type(key).__name__},
                        ),
                    ),
                )
            walk(child, (*path, key))

    walk(document, ())
    return entries


def read_registry_entries(repo_root: PathLike) -> list[RegistryEntry]:
    """Read only the three declared Registry files from an explicit source root."""
    root = Path(repo_root)
    entries: list[RegistryEntry] = []
    for release, relative_path in REGISTRY_SOURCES:
        source_path = root / relative_path
        try:
            document = json.loads(source_path.read_text(encoding="utf-8"))
        except OSError as exc:
            message = f"unable to read Registry source {relative_path}: {exc}"
            raise RegistryExtractionError(
                message,
                diagnostics=(
                    _registry_diagnostic(
                        registry_release=release,
                        registry_path=relative_path,
                        source_pointer="",
                        issue_code="registry_source_unreadable",
                        message=message,
                        details={"error_type": type(exc).__name__},
                    ),
                ),
            ) from exc
        except json.JSONDecodeError as exc:
            message = f"invalid JSON in Registry source {relative_path}: {exc.msg}"
            raise RegistryExtractionError(
                message,
                diagnostics=(
                    _registry_diagnostic(
                        registry_release=release,
                        registry_path=relative_path,
                        source_pointer="",
                        issue_code="invalid_registry_json",
                        message=message,
                        details={
                            "line": exc.lineno,
                            "column": exc.colno,
                            "json_message": exc.msg,
                        },
                    ),
                ),
            ) from exc
        entries.extend(extract_registry_entries(release, relative_path, document))
    return entries


def _component_prefixes(entry: RegistryEntry) -> tuple[str, ...]:
    """Return filename prefixes which are the Registry's exact component grammar.

    The fallback without ``objects.``/``services.`` is intentional: a small,
    source-visible subset of OpenAPI components omits that namespace in its
    filename.  This is filename grammar only; Registry's declared hash is never
    compared to raw YAML bytes.
    """
    if not entry.revision:
        return ()
    parts = entry.resource_path.split("/")
    if entry.resource_kind == "systemViews":
        if len(parts) != 2:
            return ()
        return (
            f"objects.{entry.module}.{parts[0]}.{parts[1]}.{entry.revision}.view.",
        )
    dotted = ".".join(parts)
    return (
        f"{entry.resource_kind}.{entry.module}.{dotted}.{entry.revision}.",
        f"{entry.module}.{dotted}.{entry.revision}.",
    )


def _link_kind(path: str) -> str | None:
    parent = PurePosixPath(path).parent.name
    return {
        "models": "openapi_schema",
        "paths": "openapi_operations",
        "history": "openapi_history",
        "views": "openapi_view",
    }.get(parent)


def _row_value(row: sqlite3.Row | tuple[object, ...], name: str, index: int) -> object:
    """Read DB-API rows without requiring callers to configure ``row_factory``."""
    if isinstance(row, sqlite3.Row):
        return row[name]
    return row[index]


def resolve_source_components(
    entry: RegistryEntry, files: Iterable[sqlite3.Row | tuple[object, ...]]
) -> list[tuple[int, str, str, str | None]]:
    """Resolve exact OpenAPI component files from repository-scoped ``files``.

    A returned tuple is ``(file_id, source_pointer, link_kind, file_sha1)``.
    The empty source pointer is the RFC 6901 root pointer: Registry evidence
    is at the component-file level and does not claim a narrower YAML node.
    """
    prefixes = _component_prefixes(entry)
    if not prefixes:
        return []
    matches: list[tuple[int, str, str, str | None]] = []
    for row in files:
        path = str(_row_value(row, "path", 1))
        kind = _link_kind(path)
        if kind is None:
            continue
        filename = PurePosixPath(path).name
        if any(filename.startswith(prefix) for prefix in prefixes):
            component_hash = _row_value(row, "sha1", 2)
            matches.append(
                (
                    int(_row_value(row, "id", 0)),
                    "",
                    kind,
                    str(component_hash) if component_hash is not None else None,
                )
            )
    return sorted(set(matches), key=lambda item: (item[2], item[0]))


@dataclass(frozen=True)
class RegistryBuildStats:
    entries_written: int
    links_written: int
    issues_written: int
    source_optional: int
    diagnostics: tuple[dict[str, str], ...] = ()

    @property
    def entries(self) -> int:
        """Compatibility alias for the standalone build command."""
        return self.entries_written

    @property
    def source_links(self) -> int:
        """Compatibility alias for the standalone build command."""
        return self.links_written


def _clear_registry_facts(conn: sqlite3.Connection, *, repo_id: int) -> None:
    """Remove one repository's Registry family in FK-safe order."""
    conn.execute("DELETE FROM api_registry_issues WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM api_registry_entry_links WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM api_registry_entries WHERE repo_id=?", (repo_id,))


def _replace_with_source_issues(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    registry_file_rows: dict[str, int],
    diagnostics: Iterable[RegistryDiagnostic],
) -> int:
    """Replace only Registry diagnostics with source-only failure evidence.

    This is called only after every declared Registry source has a proven
    ``files`` identity.  It intentionally never creates an entry or a link.
    Existing successful entries and links are left intact until a future
    successful replacement build; a failed standalone invocation must not
    erase previously published evidence merely to retain its failure record.
    """
    diagnostics = tuple(diagnostics)
    if not diagnostics:
        return 0
    unknown_paths = sorted(
        {record.registry_path for record in diagnostics} - registry_file_rows.keys()
    )
    if unknown_paths:
        raise RuntimeError(
            "Registry diagnostic source is absent from files evidence: "
            + ", ".join(unknown_paths)
        )
    conn.execute("DELETE FROM api_registry_issues WHERE repo_id=?", (repo_id,))
    for record in diagnostics:
        conn.execute(
            """INSERT INTO api_registry_issues(
                   repo_id, entry_id, source_file_id, source_pointer, issue_key,
                   severity, issue_code, message, details_json
               ) VALUES (?, NULL, ?, ?, ?, 'error', ?, ?, ?)""",
            (
                repo_id,
                registry_file_rows[record.registry_path],
                record.source_pointer,
                record.issue_key,
                record.issue_code,
                record.message,
                json.dumps(
                    {"registry_release": record.registry_release, **record.details},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            ),
        )
    return len(diagnostics)


def build_api_registry(
    conn: sqlite3.Connection, *, repo_id: int, repo_root: PathLike
) -> RegistryBuildStats:
    """Replace one repository's Registry evidence inside the caller transaction."""
    file_rows = conn.execute(
        """SELECT id, path, sha1 FROM files
           WHERE repo_id=? AND path LIKE 'app/source/openapispec/%'
           ORDER BY path, id""",
        (repo_id,),
    ).fetchall()
    registry_file_rows = {
        str(_row_value(row, "path", 1)): int(_row_value(row, "id", 0))
        for row in conn.execute(
            """SELECT id, path FROM files
               WHERE repo_id=? AND path IN (?, ?, ?)
               ORDER BY path, id""",
            (repo_id, *(path for _release, path in REGISTRY_SOURCES)),
        ).fetchall()
    }
    missing_registry_files = [
        path for _release, path in REGISTRY_SOURCES if path not in registry_file_rows
    ]
    if missing_registry_files:
        raise RegistryExtractionError(
            "Registry source files are absent from files evidence: "
            + ", ".join(missing_registry_files)
        )

    try:
        entries = read_registry_entries(repo_root)
        resolved: list[
            tuple[RegistryEntry, list[tuple[int, str, str, str | None]]]
        ] = []
        unresolved: list[RegistryEntry] = []
        for entry in entries:
            components = resolve_source_components(entry, file_rows)
            if not entry.source_optional and not components:
                unresolved.append(entry)
            resolved.append((entry, components))
        if unresolved:
            sample = ", ".join(
                f"{entry.registry_release}{entry.json_pointer}" for entry in unresolved[:5]
            )
            message = (
                "Registry entries have no exact source component "
                f"({len(unresolved)}): {sample}"
            )
            raise RegistryExtractionError(
                message,
                diagnostics=tuple(
                    _registry_diagnostic(
                        registry_release=entry.registry_release,
                        registry_path=entry.registry_path,
                        source_pointer=entry.json_pointer,
                        issue_code="unresolved_registry_component",
                        message=(
                            f"{entry.registry_path}{entry.json_pointer} has no exact "
                            "source component"
                        ),
                        details={
                            "module": entry.module,
                            "resource_kind": entry.resource_kind,
                            "resource_path": entry.resource_path,
                            "revision": entry.revision,
                        },
                    )
                    for entry in unresolved
                ),
            )
    except RegistryExtractionError as error:
        if error.diagnostics:
            _replace_with_source_issues(
                conn,
                repo_id=repo_id,
                registry_file_rows=registry_file_rows,
                diagnostics=error.diagnostics,
            )
            error.diagnostics_persisted = True
        raise

    # Registry data has no parent outside its own three tables.  Do not touch
    # OpenAPI index, entity, REST, compatibility, UI, or graph evidence here.
    _clear_registry_facts(conn, repo_id=repo_id)

    source_links = 0
    source_optional = 0
    for entry, components in resolved:
        cursor = conn.execute(
            """INSERT INTO api_registry_entries(
                   repo_id, registry_release, registry_file_id, json_pointer,
                   module, resource_kind, resource_path, revision, declared_hash,
                   api_type, runtime_owner, ui_metadata_hash, source_optional,
                   payload_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo_id,
                entry.registry_release,
                registry_file_rows[entry.registry_path],
                entry.json_pointer,
                entry.module,
                entry.resource_kind,
                entry.resource_path,
                entry.revision,
                entry.declared_hash,
                entry.api_type,
                entry.runtime_owner,
                entry.ui_metadata_hash,
                int(entry.source_optional),
                entry.payload_json,
            ),
        )
        entry_id = int(cursor.lastrowid)
        if entry.source_optional:
            source_optional += 1
        for source_file_id, source_pointer, link_kind, component_hash in components:
            conn.execute(
                """INSERT INTO api_registry_entry_links(
                       repo_id, entry_id, source_file_id, source_pointer, link_kind,
                       component_hash, evidence_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    repo_id,
                    entry_id,
                    source_file_id,
                    source_pointer,
                    link_kind,
                    component_hash,
                    json.dumps(
                        {"match": "registry_component_filename", "registry_pointer": entry.json_pointer},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            source_links += 1
    return RegistryBuildStats(
        entries_written=len(entries),
        links_written=source_links,
        issues_written=0,
        source_optional=source_optional,
    )


def build_registry(
    conn: sqlite3.Connection, *, repo_id: int, repo_root: PathLike
) -> RegistryBuildStats:
    """Backward-compatible alias for the initial standalone builder surface."""
    return build_api_registry(conn, repo_id=repo_id, repo_root=repo_root)
