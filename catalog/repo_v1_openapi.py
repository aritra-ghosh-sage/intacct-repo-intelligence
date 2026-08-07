"""Immutable OpenAPI and REST facts for the repo-v1 full snapshot build.

This module deliberately has no dependency on the legacy OpenAPI scanners or
the mutable checkout.  It parses only bytes materialized by ``SourceSnapshot``
and writes candidate-local facts after the inventory and entity-occurrence
slices have completed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from catalog.source_snapshot import SourceSnapshot, SourceSnapshotError

OPENAPI_EXTRACTOR = "repo_v1_openapi_v1"
OPENAPI_KINDS = frozenset(
    {
        "history",
        "schema",
        "operations",
        "view",
        "uimeta",
        "viewmeta",
        "paths",
        "components",
        "security",
        "resource",
        "actions",
        "events",
        "unknown",
    }
)
HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
)
OPENAPI_REQUIRED_DIAGNOSTIC_CODES = frozenset(
    {
        "OPENAPI_X_MAPPEDTO_BLANK",
        "OPENAPI_X_MAPPEDTO_CUSTOM",
        "OPENAPI_X_MAPPEDTO_INVALID",
        "OPENAPI_X_MAPPEDTO_ZERO_MATCHES",
        "OPENAPI_X_MAPPEDTO_MULTIPLE_MATCHES",
    }
)
OPENAPI_DIAGNOSTIC_CODES = OPENAPI_REQUIRED_DIAGNOSTIC_CODES | frozenset(
    {
        "OPENAPI_YAML_INVALID_UTF8",
        "OPENAPI_YAML_NON_MAPPING",
        "OPENAPI_YAML_DUPLICATE_KEY",
        "OPENAPI_YAML_MALFORMED",
        "OPENAPI_PATHS_INVALID",
        "OPENAPI_PATH_KEY_INVALID",
        "OPENAPI_OPERATION_INVALID",
    }
)


class OpenAPIValidationError(RuntimeError):
    """A repo-v1 OpenAPI candidate violates its source/fact contract."""


class OpenAPIParseError(ValueError):
    """A source YAML file cannot produce an indexed mapping document."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def document_key(repo_key: str, path: str) -> str:
    return hashlib.sha256(
        _canonical({"repo_key": repo_key, "path": path}).encode()
    ).hexdigest()


def endpoint_key(
    repo_key: str,
    document_path: str,
    path_template: str,
    http_method: str,
    source_pointer: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "repo_key": repo_key,
                "document_path": document_path,
                "path_template": path_template,
                "http_method": http_method,
                "source_pointer": source_pointer,
            }
        ).encode()
    ).hexdigest()


def _link_key(
    repo_key: str,
    document_path: str,
    occurrence_path: str,
    occurrence_source_key: str,
    mapped_value: str,
    match_key: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "repo_key": repo_key,
                "document_path": document_path,
                "entity_occurrence_path": occurrence_path,
                "entity_occurrence_source_key": occurrence_source_key,
                "mapped_value": mapped_value,
                "match_key": match_key,
            }
        ).encode()
    ).hexdigest()


def diagnostic_key(
    repo_key: str, path: str, phase: str, code: str, evidence: str
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "repository": repo_key,
                "path": path,
                "phase": phase,
                "code": code,
                "evidence": evidence,
            }
        ).encode()
    ).hexdigest()


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""

    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None, None, "expected a mapping node", node.start_mark
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    None, None, "unhashable mapping key", key_node.start_mark
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class _Document:
    file_id: int
    path: str
    source_sha: str
    source: bytes
    value: dict[Any, Any]
    document_id: int


@dataclass(frozen=True)
class OpenAPIExtractionStats:
    document_count: int
    link_count: int
    endpoint_count: int
    diagnostic_count: int


def _in_scope(path: str) -> bool:
    lowered = path.lower()
    return (
        lowered.startswith("app/source/openapispec/")
        and lowered.endswith(".yaml")
        and "template" not in lowered
        and not PurePosixPath(path).name.lower().startswith("template")
    )


def _classify(path: str) -> str:
    lowered_parts = tuple(part.lower() for part in PurePosixPath(path).parts)
    filename = lowered_parts[-1]
    if "history" in lowered_parts or filename.endswith(".schema.history.yaml"):
        return "history"
    for directory, kind in (
        ("models", "schema"),
        ("operations", "operations"),
        ("views", "view"),
        ("uimeta", "uimeta"),
        ("viewmeta", "viewmeta"),
        ("paths", "paths"),
        ("components", "components"),
        ("security", "security"),
        ("resources", "resource"),
        ("resource", "resource"),
        ("actions", "actions"),
        ("events", "events"),
    ):
        if directory in lowered_parts:
            return kind
    for suffix, kind in (
        (".viewmeta.yaml", "viewmeta"),
        (".uimeta.yaml", "uimeta"),
        (".view.yaml", "view"),
        (".api.yaml", "operations"),
        (".schema.yaml", "schema"),
    ):
        if filename.endswith(suffix):
            return kind
    return "unknown"


def _load_yaml(source: bytes) -> dict[Any, Any]:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenAPIParseError("invalid UTF-8") from exc
    try:
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise OpenAPIParseError(str(exc)) from exc
    if not isinstance(value, dict):
        raise OpenAPIParseError("YAML document is not a mapping")
    return value


def _source_evidence(path: str, source: bytes, **fields: object) -> str:
    return _canonical(
        {"path": path, "source_sha256": hashlib.sha256(source).hexdigest(), **fields}
    )


def _diagnostic_evidence(path: str, **fields: object) -> str:
    return _canonical({"path": path, **fields})


def _add_diagnostic(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    repo_key: str,
    file_id: int,
    document_id: int | None,
    path: str,
    phase: str,
    code: str,
    message: str,
    source_sha: str,
    evidence: str,
) -> None:
    key = diagnostic_key(repo_key, path, phase, code, evidence)
    conn.execute(
        """INSERT OR IGNORE INTO openapi_diagnostics(
               repo_id,file_id,document_id,phase,diagnostic_key,severity,code,
               message,source_commit_sha,evidence,extractor
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            file_id,
            document_id,
            phase,
            key,
            "error",
            code,
            message,
            source_sha,
            evidence,
            OPENAPI_EXTRACTOR,
        ),
    )


def _pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _parse_documents(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    repo_key: str,
    snapshot: SourceSnapshot,
    file_rows: dict[str, sqlite3.Row],
) -> list[_Document]:
    documents: list[_Document] = []
    for path in sorted(path for path in file_rows if _in_scope(path)):
        row = file_rows[path]
        if path not in {entry.path for entry in snapshot.entries}:
            raise SourceSnapshotError(
                f"snapshot entry is not present in candidate files: {path}"
            )
        try:
            source = (snapshot.snapshot_root / Path(path)).read_bytes()
            value = _load_yaml(source)
        except SourceSnapshotError:
            raise
        except (
            OpenAPIParseError
        ) as exc:  # per-file parse failures are non-blocking diagnostics
            message = str(exc) or exc.__class__.__name__
            lowered_message = message.casefold()
            code = (
                "OPENAPI_YAML_INVALID_UTF8"
                if "invalid utf-8" in lowered_message
                else (
                    "OPENAPI_YAML_NON_MAPPING"
                    if "not a mapping" in lowered_message
                    else (
                        "OPENAPI_YAML_DUPLICATE_KEY"
                        if "duplicate key" in lowered_message
                        else "OPENAPI_YAML_MALFORMED"
                    )
                )
            )
            _add_diagnostic(
                conn,
                repo_id=repo_id,
                repo_key=repo_key,
                file_id=int(row["id"]),
                document_id=None,
                path=path,
                phase="6A",
                code=code,
                message=message,
                source_sha=str(row["source_commit_sha"]),
                evidence=_diagnostic_evidence(path, error=message),
            )
            continue
        key = document_key(repo_key, path)
        cursor = conn.execute(
            """INSERT INTO openapi_documents(
                   repo_id,file_id,path,kind,document_key,source_commit_sha,evidence,extractor
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                repo_id,
                int(row["id"]),
                path,
                _classify(path),
                key,
                str(row["source_commit_sha"]),
                _source_evidence(path, source, kind=_classify(path), document_key=key),
                OPENAPI_EXTRACTOR,
            ),
        )
        documents.append(
            _Document(
                int(row["id"]),
                path,
                str(row["source_commit_sha"]),
                source,
                value,
                int(cursor.lastrowid),
            )
        )
    return documents


def _entity_links(
    conn: sqlite3.Connection,
    *,
    documents: list[_Document],
    repo_id: int,
    repo_key: str,
) -> int:
    occurrence_rows = conn.execute(
        """SELECT eo.id,eo.source_key,f.path
           FROM entity_occurrences eo JOIN files f ON f.id=eo.source_file_id
           WHERE eo.repo_id=? AND f.repo_id=? AND LOWER(f.path) LIKE '%.ent'
           ORDER BY f.path,eo.source_key,eo.id""",
        (repo_id, repo_id),
    ).fetchall()
    by_stem: dict[str, list[sqlite3.Row]] = {}
    for row in occurrence_rows:
        by_stem.setdefault(PurePosixPath(str(row["path"])).stem.casefold(), []).append(
            row
        )
    count = 0
    for document in documents:
        has_mapping = "x-mappedTo" in document.value
        raw = document.value.get("x-mappedTo")
        if not has_mapping or (isinstance(raw, str) and not raw.strip()):
            code = "OPENAPI_X_MAPPEDTO_BLANK"
            message = "top-level x-mappedTo is blank or absent"
            evidence = _diagnostic_evidence(
                document.path, pointer="/x-mappedTo", value=None if raw is None else raw
            )
        elif not isinstance(raw, str):
            code = "OPENAPI_X_MAPPEDTO_INVALID"
            message = "top-level x-mappedTo is not a string scalar"
            evidence = _diagnostic_evidence(
                document.path,
                pointer="/x-mappedTo",
                value=repr(raw),
                reason="non-string",
            )
        else:
            mapped_value = raw.strip()
            match_key = mapped_value.casefold()
            if match_key == "__custom__":
                code = "OPENAPI_X_MAPPEDTO_CUSTOM"
                message = "top-level x-mappedTo is the reserved custom value"
                evidence = _diagnostic_evidence(
                    document.path, pointer="/x-mappedTo", value=mapped_value
                )
            elif not mapped_value or "/" in mapped_value or "\\" in mapped_value:
                code = "OPENAPI_X_MAPPEDTO_INVALID"
                message = "top-level x-mappedTo is malformed or path-like"
                evidence = _diagnostic_evidence(
                    document.path, pointer="/x-mappedTo", value=mapped_value
                )
            else:
                matches = by_stem.get(match_key, [])
                if len(matches) == 0:
                    code = "OPENAPI_X_MAPPEDTO_ZERO_MATCHES"
                    message = (
                        "top-level x-mappedTo has no exact committed .ent stem match"
                    )
                    evidence = _diagnostic_evidence(
                        document.path,
                        pointer="/x-mappedTo",
                        value=mapped_value,
                        match_key=match_key,
                    )
                elif len(matches) > 1:
                    code = "OPENAPI_X_MAPPEDTO_MULTIPLE_MATCHES"
                    message = "top-level x-mappedTo has multiple exact committed .ent stem matches"
                    evidence = _diagnostic_evidence(
                        document.path,
                        pointer="/x-mappedTo",
                        value=mapped_value,
                        match_key=match_key,
                        matches=[str(row["path"]) for row in matches],
                    )
                else:
                    occurrence = matches[0]
                    link = _link_key(
                        repo_key,
                        document.path,
                        str(occurrence["path"]),
                        str(occurrence["source_key"]),
                        mapped_value,
                        match_key,
                    )
                    conn.execute(
                        """INSERT INTO openapi_entity_links(
                               repo_id,document_id,entity_occurrence_id,mapped_value,match_key,
                               link_key,source_commit_sha,evidence,extractor
                           ) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (
                            repo_id,
                            document.document_id,
                            int(occurrence["id"]),
                            mapped_value,
                            match_key,
                            link,
                            document.source_sha,
                            _source_evidence(
                                document.path,
                                document.source,
                                pointer="/x-mappedTo",
                                value=mapped_value,
                                match_key=match_key,
                            ),
                            OPENAPI_EXTRACTOR,
                        ),
                    )
                    count += 1
                    continue
        _add_diagnostic(
            conn,
            repo_id=repo_id,
            repo_key=repo_key,
            file_id=document.file_id,
            document_id=document.document_id,
            path=document.path,
            phase="6B",
            code=code,
            message=message,
            source_sha=document.source_sha,
            evidence=evidence,
        )
    return count


def _rest_endpoints(
    conn: sqlite3.Connection,
    *,
    documents: list[_Document],
    repo_id: int,
    repo_key: str,
) -> int:
    count = 0
    for document in documents:
        parts = tuple(part.casefold() for part in PurePosixPath(document.path).parts)
        if "paths" not in parts:
            continue
        paths = document.value.get("paths")
        if not isinstance(paths, dict):
            _add_diagnostic(
                conn,
                repo_id=repo_id,
                repo_key=repo_key,
                file_id=document.file_id,
                document_id=document.document_id,
                path=document.path,
                phase="6C",
                code="OPENAPI_PATHS_INVALID",
                message="top-level paths is not a mapping",
                source_sha=document.source_sha,
                evidence=_diagnostic_evidence(
                    document.path, pointer="/paths", reason="non-mapping"
                ),
            )
            continue
        for raw_path, path_item in paths.items():
            if not isinstance(raw_path, str) or not raw_path.startswith("/"):
                _add_diagnostic(
                    conn,
                    repo_id=repo_id,
                    repo_key=repo_key,
                    file_id=document.file_id,
                    document_id=document.document_id,
                    path=document.path,
                    phase="6C",
                    code="OPENAPI_PATH_KEY_INVALID",
                    message="path key is not a string with a leading slash",
                    source_sha=document.source_sha,
                    evidence=_diagnostic_evidence(
                        document.path, pointer="/paths", value=repr(raw_path)
                    ),
                )
                continue
            if not isinstance(path_item, dict):
                _add_diagnostic(
                    conn,
                    repo_id=repo_id,
                    repo_key=repo_key,
                    file_id=document.file_id,
                    document_id=document.document_id,
                    path=document.path,
                    phase="6C",
                    code="OPENAPI_OPERATION_INVALID",
                    message="path item is not a mapping",
                    source_sha=document.source_sha,
                    evidence=_diagnostic_evidence(
                        document.path,
                        pointer=f"/paths/{_pointer_token(raw_path)}",
                        reason="non-mapping",
                    ),
                )
                continue
            for raw_method, operation in path_item.items():
                method = raw_method if isinstance(raw_method, str) else ""
                if method not in HTTP_METHODS:
                    continue
                pointer = (
                    f"/paths/{_pointer_token(raw_path)}/{_pointer_token(raw_method)}"
                )
                if not isinstance(operation, dict):
                    _add_diagnostic(
                        conn,
                        repo_id=repo_id,
                        repo_key=repo_key,
                        file_id=document.file_id,
                        document_id=document.document_id,
                        path=document.path,
                        phase="6C",
                        code="OPENAPI_OPERATION_INVALID",
                        message="operation is not a mapping",
                        source_sha=document.source_sha,
                        evidence=_diagnostic_evidence(
                            document.path, pointer=pointer, reason="non-mapping"
                        ),
                    )
                    continue
                operation_id: str | None = None
                raw_operation_id = operation.get("operationId")
                if (
                    isinstance(raw_operation_id, (str, int, float, bool))
                    and str(raw_operation_id).strip()
                ):
                    operation_id = str(raw_operation_id).strip()
                key = endpoint_key(repo_key, document.path, raw_path, method, pointer)
                try:
                    conn.execute(
                        """INSERT INTO rest_endpoints(
                               repo_id,document_id,endpoint_key,path_template,http_method,
                               operation_id,source_pointer,source_commit_sha,evidence,extractor
                           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            repo_id,
                            document.document_id,
                            key,
                            raw_path,
                            method,
                            operation_id,
                            pointer,
                            document.source_sha,
                            _source_evidence(
                                document.path,
                                document.source,
                                operation_id=operation_id,
                                pointer=pointer,
                                path_template=raw_path,
                                http_method=method,
                            ),
                            OPENAPI_EXTRACTOR,
                        ),
                    )
                    count += 1
                except sqlite3.IntegrityError as exc:
                    raise OpenAPIValidationError(
                        f"duplicate REST operation in {document.path}: {pointer}"
                    ) from exc
    return count


def extract_snapshot_openapi(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> OpenAPIExtractionStats:
    """Extract all Phase 6 facts from one committed source snapshot."""
    del show_progress
    repo_row = conn.execute(
        "SELECT repo_key,target_commit_sha FROM repos WHERE id=?", (repo_id,)
    ).fetchone()
    if repo_row is None:
        raise OpenAPIValidationError(f"unknown candidate repository: {repo_id}")
    repo_key = str(repo_row["repo_key"])
    file_rows = {
        str(row["path"]): row
        for row in conn.execute(
            "SELECT id,path,source_commit_sha FROM files WHERE repo_id=?", (repo_id,)
        ).fetchall()
    }
    conn.execute("DELETE FROM openapi_entity_links WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM rest_endpoints WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM openapi_diagnostics WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM openapi_documents WHERE repo_id=?", (repo_id,))
    documents = _parse_documents(
        conn, repo_id=repo_id, repo_key=repo_key, snapshot=snapshot, file_rows=file_rows
    )
    link_count = _entity_links(
        conn, documents=documents, repo_id=repo_id, repo_key=repo_key
    )
    endpoint_count = _rest_endpoints(
        conn, documents=documents, repo_id=repo_id, repo_key=repo_key
    )
    return OpenAPIExtractionStats(
        document_count=len(documents),
        link_count=link_count,
        endpoint_count=endpoint_count,
        diagnostic_count=int(
            conn.execute(
                "SELECT COUNT(*) FROM openapi_diagnostics WHERE repo_id=?", (repo_id,)
            ).fetchone()[0]
        ),
    )


def _recompute_diagnostic_key(row: sqlite3.Row, repo_key: str) -> str:
    return diagnostic_key(
        repo_key,
        str(row["path"]),
        str(row["phase"]),
        str(row["code"]),
        str(row["evidence"]),
    )


def validate_openapi_candidate(
    conn: sqlite3.Connection, *, repo_id: int, repo_key: str, target_commit_sha: str
) -> None:
    """Validate Phase 6 ownership, provenance, facts, and stable identities."""
    invalid_docs: list[sqlite3.Row] = []
    for row in conn.execute(
        """SELECT d.*,f.repo_id AS file_repo,f.path AS file_path,
                                     f.source_commit_sha AS file_sha
                              FROM openapi_documents d LEFT JOIN files f ON f.id=d.file_id
                              WHERE d.repo_id=?""",
        (repo_id,),
    ):
        expected = document_key(repo_key, str(row["path"]))
        if (
            row["file_id"] is None
            or row["file_repo"] != repo_id
            or str(row["document_key"]) != expected
            or str(row["source_commit_sha"]) != target_commit_sha
            or str(row["source_commit_sha"]) != str(row["file_sha"])
            or str(row["file_path"]) != str(row["path"])
            or not _in_scope(str(row["path"]))
            or str(row["kind"]) not in OPENAPI_KINDS
            or not str(row["evidence"])
            or str(row["extractor"]) != OPENAPI_EXTRACTOR
        ):
            invalid_docs.append(row)
    if invalid_docs:
        raise OpenAPIValidationError(
            "candidate OpenAPI document ownership, provenance, or key validation failed"
        )
    invalid_links = conn.execute(
        """SELECT l.id,l.repo_id,l.source_commit_sha,l.evidence,l.extractor,d.path AS document_path,
                  d.repo_id AS document_repo,eo.repo_id AS occurrence_repo,f.path AS occurrence_path,
                      eo.source_key, r.id AS link_repo, r.target_commit_sha
           FROM openapi_entity_links l
           LEFT JOIN openapi_documents d ON d.id=l.document_id
           LEFT JOIN entity_occurrences eo ON eo.id=l.entity_occurrence_id
           LEFT JOIN files f ON f.id=eo.source_file_id
           LEFT JOIN repos r ON r.id=l.repo_id
           WHERE l.repo_id<>? OR d.id IS NULL OR eo.id IS NULL OR d.repo_id<>l.repo_id
              OR eo.repo_id<>l.repo_id OR f.repo_id<>l.repo_id OR l.source_commit_sha<>?
              OR l.source_commit_sha<>d.source_commit_sha OR l.source_commit_sha<>r.target_commit_sha
              OR l.mapped_value='' OR l.match_key='' OR l.evidence='' OR l.extractor<>?""",
        (repo_id, target_commit_sha, OPENAPI_EXTRACTOR),
    ).fetchall()
    if invalid_links:
        raise OpenAPIValidationError(
            "candidate OpenAPI entity-link ownership or provenance validation failed"
        )
    occurrence_stems = [
        PurePosixPath(str(row[0])).stem.casefold()
        for row in conn.execute(
            """SELECT f.path FROM entity_occurrences eo JOIN files f ON f.id=eo.source_file_id
               WHERE eo.repo_id=? AND f.repo_id=? AND LOWER(f.path) LIKE '%.ent'""",
            (repo_id, repo_id),
        )
    ]
    for row in conn.execute(
        """SELECT l.*,d.path AS document_path,eo.source_key,f.path AS occurrence_path
                              FROM openapi_entity_links l JOIN openapi_documents d ON d.id=l.document_id
                              JOIN entity_occurrences eo ON eo.id=l.entity_occurrence_id
                              JOIN files f ON f.id=eo.source_file_id WHERE l.repo_id=?""",
        (repo_id,),
    ):
        expected = _link_key(
            repo_key,
            str(row["document_path"]),
            str(row["occurrence_path"]),
            str(row["source_key"]),
            str(row["mapped_value"]),
            str(row["match_key"]),
        )
        match_key = str(row["match_key"])
        if (
            str(row["link_key"]) != expected
            or match_key != str(row["mapped_value"]).strip().casefold()
            or occurrence_stems.count(
                PurePosixPath(str(row["occurrence_path"])).stem.casefold()
            )
            != 1
            or match_key != PurePosixPath(str(row["occurrence_path"])).stem.casefold()
        ):
            raise OpenAPIValidationError(
                "candidate OpenAPI entity-link key validation failed"
            )
    invalid_endpoints = conn.execute(
        """SELECT e.id,e.repo_id,e.source_commit_sha,e.path_template,e.http_method,e.source_pointer,
                  e.operation_id,e.endpoint_key,e.evidence,e.extractor,d.path AS document_path,
                  d.repo_id AS document_repo,r.target_commit_sha
           FROM rest_endpoints e LEFT JOIN openapi_documents d ON d.id=e.document_id
           LEFT JOIN repos r ON r.id=e.repo_id
           WHERE e.repo_id<>? OR d.id IS NULL OR d.repo_id<>e.repo_id OR e.source_commit_sha<>?
              OR e.source_commit_sha<>d.source_commit_sha OR e.source_commit_sha<>r.target_commit_sha
              OR e.path_template NOT LIKE '/%' OR e.http_method NOT IN ('get','post','put','patch','delete','head','options','trace')
              OR e.source_pointer NOT LIKE '/%' OR e.evidence='' OR e.extractor<>?""",
        (repo_id, target_commit_sha, OPENAPI_EXTRACTOR),
    ).fetchall()
    if invalid_endpoints:
        raise OpenAPIValidationError(
            "candidate REST endpoint ownership, provenance, or fact validation failed"
        )
    for row in conn.execute(
        "SELECT e.*,d.path AS document_path FROM rest_endpoints e JOIN openapi_documents d ON d.id=e.document_id WHERE e.repo_id=?",
        (repo_id,),
    ):
        expected = endpoint_key(
            repo_key,
            str(row["document_path"]),
            str(row["path_template"]),
            str(row["http_method"]),
            str(row["source_pointer"]),
        )
        if (
            str(row["endpoint_key"]) != expected
            or "paths"
            not in tuple(
                part.casefold()
                for part in PurePosixPath(str(row["document_path"])).parts
            )
            or not str(row["source_pointer"]).startswith("/paths/")
            or str(row["source_pointer"]).rsplit("/", 1)[-1].casefold()
            != str(row["http_method"])
        ):
            raise OpenAPIValidationError(
                "candidate REST endpoint key validation failed"
            )
        try:
            endpoint_evidence = json.loads(str(row["evidence"]))
        except (TypeError, ValueError) as exc:
            raise OpenAPIValidationError(
                "candidate REST endpoint evidence validation failed"
            ) from exc
        if (
            not isinstance(endpoint_evidence, dict)
            or endpoint_evidence.get("operation_id") != row["operation_id"]
        ):
            raise OpenAPIValidationError(
                "candidate REST endpoint operation_id provenance validation failed"
            )
    for row in conn.execute(
        """SELECT d.*,f.path AS path,f.repo_id AS file_repo,
                                     f.source_commit_sha AS file_sha
                              FROM openapi_diagnostics d JOIN files f ON f.id=d.file_id
                              WHERE d.repo_id=?""",
        (repo_id,),
    ):
        if (
            row["file_repo"] != repo_id
            or str(row["source_commit_sha"]) != target_commit_sha
            or str(row["source_commit_sha"]) != str(row["file_sha"])
            or row["extractor"] != OPENAPI_EXTRACTOR
            or row["severity"] != "error"
            or str(row["phase"]) not in {"6A", "6B", "6C"}
            or str(row["code"]) not in OPENAPI_DIAGNOSTIC_CODES
        ):
            raise OpenAPIValidationError(
                "candidate OpenAPI diagnostic provenance validation failed"
            )
        if row["document_id"] is not None:
            document = conn.execute(
                "SELECT repo_id,file_id FROM openapi_documents WHERE id=?",
                (row["document_id"],),
            ).fetchone()
            if (
                document is None
                or int(document["repo_id"]) != repo_id
                or int(document["file_id"]) != int(row["file_id"])
            ):
                raise OpenAPIValidationError(
                    "candidate OpenAPI diagnostic ownership validation failed"
                )
        if _recompute_diagnostic_key(row, repo_key) != str(row["diagnostic_key"]):
            raise OpenAPIValidationError(
                "candidate OpenAPI diagnostic key validation failed"
            )
    duplicate_endpoints = conn.execute(
        """SELECT document_id,path_template,http_method,COUNT(*) c
                                          FROM rest_endpoints WHERE repo_id=?
                                          GROUP BY document_id,path_template,http_method HAVING c<>1""",
        (repo_id,),
    ).fetchone()
    if duplicate_endpoints is not None:
        raise OpenAPIValidationError("candidate contains duplicate REST operations")
