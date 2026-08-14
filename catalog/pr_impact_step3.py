"""Read-only repo-v1 incoming caller tracing for PR impact Step 3.

Step 3 deliberately consumes only the exact Git diff and the target-revision
repo-v1 SQLite catalog.  It does not consume a Step 1 report and it does not
perform catalog, graph, MCP, refresh, or delta work.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from catalog.pr_impact_manifest import resolve_manifest_repo_root
from catalog.pr_impact_step1 import (
    Step1Error,
    _changed_paths,
    _fixture,
    _open_catalog,
    _revision,
    _safe_path,
)
from catalog.repo_v1_symbol_entity import entity_impact_facts, mapping_rows_for_symbols

REPORT_SCHEMA_VERSION = "0.2"
ANALYSIS_KIND = "pr_impact_step_3"
SEED_BASIS = "target_file_all_symbols"
ALLOWED_RELATIONSHIP_TYPES = {"CALLS", "STATIC_CALLS"}
ENTITY_MAPPING_GAP = "entity_context:repo_v1_symbol_entity_mapping_not_modelled"
SEED_FILE_STATES = {
    "available",
    "deleted",
    "parser_failed",
    "symbol_less",
    "missing_target_file",
}
SKIP_REASONS = {
    "non_call_relationship",
    "unresolved_resolution",
    "source_symbol_missing",
    "below_confidence",
}
UNRESOLVED_EDGE_WARNING_THRESHOLD = 5
_SQL_BATCH_SIZE = 400
CALLER_EVIDENCE_REVIEW_REASONS = frozenset(
    {"below_confidence", "unresolved_resolution", "source_symbol_missing"}
)


class Step3Error(Step1Error):
    """A fail-closed Step 3 analysis error."""


def _normalized_symbol_kind(value: Any) -> str | None:
    """Match the repo-v1 relationship extractor's persisted kind normalization."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"", "unknown"}:
        return None
    if normalized in {"cqry", "qry"}:
        return "query"
    return str(value)


def _error(code: str, message: str, **extra: Any) -> Step3Error:
    return Step3Error(code, message, **extra)


def _marks(count: int) -> str:
    return ",".join("?" for _ in range(count)) or "NULL"


def _chunks(values: Iterable[int], size: int = _SQL_BATCH_SIZE) -> Iterable[list[int]]:
    batch: list[int] = []
    for value in values:
        batch.append(int(value))
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _file_record(row: Mapping[str, Any], target: str) -> dict[str, Any]:
    return {
        "catalog_record_id": int(row["id"]),
        "file_id": int(row["id"]),
        "file_path": str(row["path"]),
        "blob_object_id": str(row["blob_object_id"]),
        "file_mode": int(row["file_mode"]),
        "size_bytes": int(row["size_bytes"]),
        "catalog_source_revision": str(row["source_commit_sha"]),
        "fixture_target_revision": target,
    }


def _symbol_record(row: Mapping[str, Any], target: str) -> dict[str, Any]:
    result = {
        "catalog_record_id": int(row["symbol_id"]),
        "repository_id": int(row["repo_id"]),
        "symbol_id": int(row["symbol_id"]),
        "name": str(row["name"]),
        "kind": str(row["kind"]),
        "parent_symbol": row["parent_symbol"],
        "signature": row["signature"],
        "language": str(row["language"]),
        "stable_key": str(row["stable_key"]),
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "declaration_range": {
            "start_line": row["start_line"],
            "end_line": row["end_line"],
        },
        "file_id": int(row["file_id"]),
        "file_path": str(row["file_path"]),
        "blob_object_id": str(row["blob_object_id"]),
        "file_mode": int(row["file_mode"]),
        "size_bytes": int(row["size_bytes"]),
        "catalog_source_revision": str(row["file_source_commit_sha"]),
        "fixture_target_revision": target,
    }
    return result


def _edge_record(row: Mapping[str, Any], target: str, hop: int) -> dict[str, Any]:
    source_id = row["source_symbol_id"]
    target_id = row["target_symbol_id"]
    result = {
        "catalog_record_id": int(row["relationship_id"]),
        "relationship_id": int(row["relationship_id"]),
        "repository_id": int(row["relationship_repo_id"]),
        "file_id": int(row["relationship_file_id"]),
        "file_path": str(row["relationship_file_path"]),
        "blob_object_id": str(row["blob_object_id"]),
        "file_mode": int(row["file_mode"]),
        "catalog_source_revision": str(row["file_source_commit_sha"]),
        "fixture_target_revision": target,
        "source_symbol_id": int(source_id) if source_id is not None else None,
        "source_symbol_name": row["source_name"],
        "source_symbol_kind": row["source_kind"],
        "target_symbol_id": int(target_id) if target_id is not None else None,
        "target_symbol_name": row["target_name"],
        "target_symbol_kind": row["target_kind"],
        "source_file_id": int(row["source_file_id"])
        if row["source_file_id"] is not None
        else None,
        "source_file_path": row["source_actual_file_path"],
        "source_blob_object_id": row["source_blob_object_id"],
        "source_file_mode": row["source_file_mode"],
        "source_size_bytes": row["source_size_bytes"],
        "source_catalog_source_revision": row["source_file_source_commit_sha"],
        "target_file_id": int(row["target_file_id"])
        if row["target_file_id"] is not None
        else None,
        "target_file_path": row["target_actual_file_path"]
        if row["target_file_id"] is not None
        else None,
        "target_blob_object_id": row["target_blob_object_id"]
        if row["target_file_id"] is not None
        else None,
        "target_file_mode": row["target_file_mode"]
        if row["target_file_id"] is not None
        else None,
        "target_size_bytes": row["target_size_bytes"]
        if row["target_file_id"] is not None
        else None,
        "target_catalog_source_revision": row["target_file_source_commit_sha"],
        "source_declaration_range": {
            "start_line": row["source_start_line"],
            "end_line": row["source_end_line"],
        }
        if row["source_symbol_id"] is not None
        else None,
        "target_declaration_range": {
            "start_line": row["target_start_line"],
            "end_line": row["target_end_line"],
        }
        if row["target_symbol_id"] is not None
        else None,
        "relationship_type": row["relationship_type"],
        "confidence": row["confidence"],
        "evidence": _json_value(row["evidence"]),
        "resolution_class": row["resolution_class"],
        "resolution_reason": row["resolution_reason"],
        "extractor": row["extractor"],
        "hop": hop,
    }
    return result


def _input(
    fixture: Path,
    manifest: Path,
    active_db: Path,
    repo_key: str,
    repo_root: Path,
    base: str,
    target: str,
    max_hops: int,
    min_confidence: float,
) -> dict[str, Any]:
    return {
        "fixture": str(fixture),
        "manifest": str(manifest),
        "active_db": str(active_db),
        "repo_key": repo_key,
        "repo_root": str(repo_root),
        "base_revision": base,
        "target_revision": target,
        "max_hops": max_hops,
        "min_confidence": min_confidence,
        "seed_basis": SEED_BASIS,
    }


def _base_report(
    *,
    status: str,
    input_data: dict[str, Any],
    preflight: dict[str, Any],
    changed_files: list[dict[str, Any]],
    seed_files: list[dict[str, Any]],
    seed_symbols: list[dict[str, Any]],
    reached_symbols: list[dict[str, Any]],
    transitive_edges: list[dict[str, Any]],
    skipped_edges: list[dict[str, Any]],
    gaps: list[str],
    warnings: list[str],
    entity_context: dict[str, Any],
) -> dict[str, Any]:
    target = str(input_data.get("target_revision", ""))
    skipped_edge_counts: dict[str, int] = {}
    for edge in skipped_edges:
        reason = edge.get("skip_reason")
        if isinstance(reason, str):
            skipped_edge_counts[reason] = skipped_edge_counts.get(reason, 0) + 1
    review_required = bool(
        CALLER_EVIDENCE_REVIEW_REASONS & set(skipped_edge_counts)
    )
    caller_status = (
        "needs_review"
        if review_required
        else ("empty" if status == "empty" else "complete")
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": status,
        "input": input_data,
        "preflight": preflight,
        "changed_files": changed_files,
        "seed_files": seed_files,
        "seed_symbols": seed_symbols,
        "reached_symbols": reached_symbols,
        "transitive_edges": transitive_edges,
        "skipped_edges": skipped_edges,
        "caller_evidence": {
            "status": caller_status,
            "traversed_edge_count": len(transitive_edges),
            "reached_symbol_count": len(reached_symbols),
            "skipped_edge_counts": dict(sorted(skipped_edge_counts.items())),
        },
        "entity_context": entity_context,
        "business_impact": {
            "status": "deferred",
            "reason": "transitive callers are verified code evidence only",
            "facts": [],
        },
        "gaps": sorted(
            {
                *gaps,
                *(
                    [ENTITY_MAPPING_GAP]
                    if entity_context.get("status") == "unavailable"
                    else []
                ),
            }
        ),
        "warnings": sorted(set(warnings)),
        "provenance": {
            "source": "repo-v1 active SQLite and exact Git diff",
            "read_only": True,
            "catalog_source_revision": preflight.get("catalog_revision"),
            "fixture_target_revision": target,
            "contract": "Git diff validation only; no catalog delta processing.",
        },
    }


def blocked_report(error: Step1Error) -> dict[str, Any]:
    """Build the stable blocked envelope used by the CLI."""
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "blocked",
        "error": {"code": error.code, "message": error.message, **error.extra},
        "input": {},
        "preflight": {},
        "changed_files": [],
        "seed_files": [],
        "seed_symbols": [],
        "reached_symbols": [],
        "transitive_edges": [],
        "skipped_edges": [],
        "caller_evidence": {
            "status": "blocked",
            "traversed_edge_count": 0,
            "reached_symbol_count": 0,
            "skipped_edge_counts": {},
        },
        "entity_context": {
            "status": "unavailable",
            "reason": "repo_v1_symbol_entity_mapping_not_modelled",
            "mappings": [],
            "unavailable_symbol_ids": [],
        },
        "business_impact": {
            "status": "deferred",
            "reason": "transitive callers are verified code evidence only",
            "facts": [],
        },
        "gaps": [],
        "warnings": [],
        "provenance": {"read_only": True},
    }
    context = error.extra.get("blocked_context")
    if isinstance(context, dict):
        for key in ("input", "preflight", "changed_files", "seed_files"):
            if key in context:
                report[key] = context[key]
    return report


def _validate_target_file_ownership(
    conn: sqlite3.Connection, repo_id: int, paths: list[str]
) -> None:
    rows = conn.execute(
        f"SELECT id,path,repo_id FROM files WHERE path IN ({_marks(len(paths))}) ORDER BY id",
        tuple(paths),
    ).fetchall()
    if any(int(row["repo_id"]) != repo_id for row in rows):
        raise _error(
            "catalog_provenance_mismatch",
            "changed file is owned by a different repository",
        )


def _symbol_rows(
    conn: sqlite3.Connection, repo_id: int, file_ids: list[int]
) -> list[sqlite3.Row]:
    return conn.execute(
        f"""SELECT s.id AS symbol_id,s.repo_id,s.file_id,s.name,s.kind,
                   s.parent_symbol,s.start_line,s.end_line,s.signature,
                   s.language,s.stable_key,f.path AS file_path,
                   f.blob_object_id,f.file_mode,f.size_bytes,
                   f.source_commit_sha AS file_source_commit_sha
              FROM symbols s JOIN files f ON f.id=s.file_id
             WHERE s.file_id IN ({_marks(len(file_ids))})
             ORDER BY s.id""",
        tuple(file_ids),
    ).fetchall()


def _edge_rows(conn: sqlite3.Connection, frontier: list[int]) -> list[sqlite3.Row]:
    return conn.execute(
        f"""SELECT r.id AS relationship_id,r.repo_id AS relationship_repo_id,
                   r.source_symbol_id,r.source_name,r.source_kind,
                   r.target_symbol_id,r.target_name,r.target_kind,
                   r.relationship_type,r.file_id AS relationship_file_id,
                   r.file_path AS relationship_file_path,r.language,
                   r.confidence,r.evidence,r.resolution_class,
                   r.resolution_reason,r.extractor,
                   f.repo_id AS file_repo_id,f.path AS actual_file_path,
                   f.blob_object_id,f.file_mode,f.size_bytes,
                   f.source_commit_sha AS file_source_commit_sha,
                   ss.repo_id AS source_repo_id,ss.name AS source_db_name,
                   ss.kind AS source_db_kind,ss.file_id AS source_file_id,
                   ss.parent_symbol AS source_parent_symbol,
                   ss.signature AS source_signature,ss.language AS source_language,
                   ss.stable_key AS source_stable_key,
                   ss.start_line AS source_start_line,ss.end_line AS source_end_line,
                   sf.repo_id AS source_file_repo_id,sf.path AS source_actual_file_path,
                   sf.blob_object_id AS source_blob_object_id,
                   sf.file_mode AS source_file_mode,sf.size_bytes AS source_size_bytes,
                   sf.source_commit_sha AS source_file_source_commit_sha,
                   ts.repo_id AS target_repo_id,ts.name AS target_db_name,
                   ts.kind AS target_db_kind,ts.file_id AS target_file_id,
                   ts.start_line AS target_start_line,ts.end_line AS target_end_line,
                   tf.repo_id AS target_file_repo_id,tf.path AS target_actual_file_path,
                   tf.blob_object_id AS target_blob_object_id,
                   tf.file_mode AS target_file_mode,tf.size_bytes AS target_size_bytes,
                   tf.source_commit_sha AS target_file_source_commit_sha
              FROM relationships r
              LEFT JOIN files f ON f.id=r.file_id
              LEFT JOIN symbols ss ON ss.id=r.source_symbol_id
              LEFT JOIN files sf ON sf.id=ss.file_id
              LEFT JOIN symbols ts ON ts.id=r.target_symbol_id
              LEFT JOIN files tf ON tf.id=ts.file_id
             WHERE r.target_symbol_id IN ({_marks(len(frontier))})
             ORDER BY r.id""",
        tuple(frontier),
    ).fetchall()


def _entity_context(
    conn: sqlite3.Connection, repo_id: int, symbol_ids: list[int]
) -> dict[str, Any]:
    rows = mapping_rows_for_symbols(conn, repo_id, symbol_ids)
    if not rows:
        return {
            "status": "unavailable",
            "reason": "repo_v1_symbol_entity_mapping_not_modelled",
            "mappings": [],
            "unavailable_symbol_ids": sorted(symbol_ids),
        }
    mappings: list[dict[str, Any]] = []
    mapped_ids: set[int] = set()
    resolved_count = 0
    for row in rows:
        symbol_id = int(row["symbol_id"])
        mapped_ids.add(symbol_id)
        item: dict[str, Any] = {
            "symbol_id": symbol_id,
            "symbol_name": row["symbol_name"],
            "symbol_file_path": row["symbol_file_path"],
            "symbol_stable_key": row["symbol_stable_key"],
            "entity_source_path": row["entity_source_path"],
            "entity_source_key": row["entity_source_key"],
            "mapping_type": row["mapping_type"],
            "resolution_status": row["resolution_status"],
            "resolution_reason": row["resolution_reason"],
            "mapping_contract_path": row["mapping_contract_path"],
            "mapping_contract_sha256": row["mapping_contract_sha256"],
            "target_revision": row["target_revision"],
            "contract_entry_key": row["contract_entry_key"],
            "evidence": _json_value(row["evidence"]),
            "extractor": row["extractor"],
        }
        if row["resolution_status"] == "resolved":
            resolved_count += 1
            item["entity_occurrence_id"] = int(row["entity_occurrence_id"])
            item["entity_id"] = int(row["entity_id"])
            item["entity_name"] = row["entity_name"]
            item["entity_impact_facts"] = entity_impact_facts(
                conn, repo_id, int(row["entity_occurrence_id"])
            )
        else:
            item["entity_occurrence_id"] = None
            item["entity_id"] = None
            item["entity_impact_facts"] = {}
        mappings.append(item)
    unavailable = sorted(set(symbol_ids) - mapped_ids)
    return {
        "status": "available"
        if resolved_count
        and not unavailable
        and all(item["resolution_status"] == "resolved" for item in mappings)
        else "partial",
        "reason": "reviewed_symbol_entity_mapping_contract",
        "mappings": mappings,
        "unavailable_symbol_ids": unavailable,
    }


def _check_edge_provenance(row: Mapping[str, Any], repo_id: int, target: str) -> None:
    if (
        row["relationship_repo_id"] is None
        or int(row["relationship_repo_id"]) != repo_id
    ):
        raise _error(
            "catalog_provenance_mismatch", "relationship repository ownership mismatch"
        )
    if row["file_repo_id"] is None or int(row["file_repo_id"]) != repo_id:
        raise _error(
            "catalog_provenance_mismatch",
            "relationship file repository ownership mismatch",
        )
    if (
        not isinstance(row["relationship_file_path"], str)
        or not row["relationship_file_path"]
        or not isinstance(row["actual_file_path"], str)
        or not row["actual_file_path"]
    ):
        raise _error(
            "catalog_provenance_mismatch",
            "relationship evidence file identity is incomplete",
        )
    if str(row["actual_file_path"]) != str(row["relationship_file_path"]):
        raise _error(
            "catalog_provenance_mismatch",
            "relationship file path does not match its catalog file",
        )
    if (
        not isinstance(row["blob_object_id"], str)
        or not row["blob_object_id"]
        or str(row["file_source_commit_sha"]) != target
    ):
        raise _error(
            "catalog_provenance_mismatch",
            "relationship evidence is not from the fixture target revision",
        )
    if (
        row["target_symbol_id"] is None
        or row["target_repo_id"] is None
        or row["target_file_id"] is None
        or int(row["target_repo_id"]) != repo_id
    ):
        raise _error(
            "catalog_provenance_mismatch",
            "relationship target symbol repository mismatch",
        )
    if row["target_file_repo_id"] is None or int(row["target_file_repo_id"]) != repo_id:
        raise _error(
            "catalog_provenance_mismatch",
            "relationship target file repository mismatch",
        )
    if (
        not isinstance(row["target_actual_file_path"], str)
        or not row["target_actual_file_path"]
        or not isinstance(row["target_blob_object_id"], str)
        or not row["target_blob_object_id"]
        or str(row["target_file_source_commit_sha"]) != target
    ):
        raise _error(
            "catalog_provenance_mismatch",
            "relationship target symbol is not from the fixture target revision",
        )
    if row["source_symbol_id"] is not None:
        if row["source_repo_id"] is None or int(row["source_repo_id"]) != repo_id:
            raise _error(
                "catalog_provenance_mismatch",
                "relationship source symbol repository mismatch",
            )
        if (
            row["source_file_repo_id"] is None
            or int(row["source_file_repo_id"]) != repo_id
        ):
            raise _error(
                "catalog_provenance_mismatch",
                "relationship source file repository mismatch",
            )
        if (
            row["source_file_id"] is None
            or not isinstance(row["source_actual_file_path"], str)
            or not row["source_actual_file_path"]
            or not isinstance(row["source_blob_object_id"], str)
            or not row["source_blob_object_id"]
            or str(row["source_file_source_commit_sha"]) != target
        ):
            raise _error(
                "catalog_provenance_mismatch",
                "relationship source symbol is not from the fixture target revision",
            )
        if (
            not isinstance(row["source_db_name"], str)
            or not row["source_db_name"]
            or not isinstance(row["source_db_kind"], str)
            or not row["source_db_kind"]
            or row["source_db_name"] != row["source_name"]
            or row["source_db_kind"] != row["source_kind"]
        ):
            raise _error(
                "catalog_provenance_mismatch",
                "relationship source symbol identity does not match its catalog symbol",
            )
    if (
        not isinstance(row["target_db_name"], str)
        or not row["target_db_name"]
        or row["target_db_name"] != row["target_name"]
        or _normalized_symbol_kind(row["target_db_kind"]) != row["target_kind"]
    ):
        raise _error(
            "catalog_provenance_mismatch",
            "relationship target symbol identity does not match its catalog symbol",
        )


def analyze_document(
    document: Mapping[str, Any],
    manifest: str | Path,
    active_db: str | Path,
    repo_key: str,
    max_hops: int = 2,
    min_confidence: float = 0.7,
    fixture_label: str = "<in-memory>",
) -> dict[str, Any]:
    """Run deterministic, read-only incoming traversal for one fixture."""
    if max_hops not in (1, 2):
        raise _error("malformed_fixture", "max_hops must be 1 or 2", max_hops=max_hops)
    if (
        isinstance(min_confidence, bool)
        or not isinstance(min_confidence, (int, float))
        or not 0 <= min_confidence <= 1
    ):
        raise _error(
            "malformed_fixture",
            "min_confidence must be between 0 and 1",
            min_confidence=min_confidence,
        )
    manifest_path, db_path = Path(manifest), Path(active_db)
    if not isinstance(document, dict) or not isinstance(document.get("pull_request"), dict):
        raise _error("malformed_fixture", "fixture must contain pull_request")
    pr = document["pull_request"]
    try:
        repo = resolve_manifest_repo_root(manifest_path, repo_key)
    except ValueError as exc:
        code, _, message = str(exc).partition(": ")
        raise _error(code or "manifest_invalid", message or str(exc)) from exc

    # Git validation intentionally completes before the SQLite catalog is opened.
    base = _revision(repo, pr.get("base_revision"), "base_revision")
    target = _revision(repo, pr.get("target_revision"), "target_revision")
    changed = _changed_paths(repo, base, target)
    if not changed:
        raise _error("empty_diff", "Step 3 requires a non-empty Git diff")
    declared_rows = document.get("changed_files")
    if not isinstance(declared_rows, list) or not declared_rows:
        raise _error(
            "changed_path_mismatch", "fixture changed_files must be a non-empty list"
        )
    declared = {
        (item.get("path"), item.get("status"), item.get("old_path"))
        for item in declared_rows
        if isinstance(item, dict)
    }
    actual = {(item.path, item.status, item.old_path) for item in changed}
    if len(declared) != len(declared_rows) or declared != actual:
        raise _error(
            "changed_path_mismatch",
            "fixture changed_files do not match the exact Git diff",
        )
    if any(not _safe_path(item.path) for item in changed):
        raise _error("malformed_git_diff", "changed path is unsafe")

    input_data = _input(
        Path(fixture_label),
        manifest_path,
        db_path,
        repo_key,
        repo,
        base,
        target,
        max_hops,
        float(min_confidence),
    )
    changed_files = [item.__dict__ for item in changed]
    conn, repo_id, preflight = _open_catalog(db_path, target, repo_key, repo)
    try:
        paths = [item.path for item in changed]
        _validate_target_file_ownership(conn, repo_id, paths)
        rows = conn.execute(
            f"SELECT * FROM files WHERE repo_id=? AND path IN ({_marks(len(paths))}) ORDER BY path",
            (repo_id, *paths),
        ).fetchall()
        by_path = {str(row["path"]): row for row in rows}
        seed_files: list[dict[str, Any]] = []
        missing: list[str] = []
        for change in changed:
            if change.status == "deleted":
                seed_files.append(
                    {"path": change.path, "status": change.status, "state": "deleted"}
                )
                continue
            row = by_path.get(change.path)
            if row is None:
                seed_files.append(
                    {
                        "path": change.path,
                        "status": change.status,
                        "state": "missing_target_file",
                    }
                )
                missing.append(change.path)
                continue
            if str(row["source_commit_sha"]) != target:
                raise _error(
                    "catalog_provenance_mismatch",
                    "changed target file is not from the fixture target revision",
                    path=change.path,
                    catalog_source_revision=row["source_commit_sha"],
                    fixture_target_revision=target,
                )
            file_id = int(row["id"])
            diagnostic_rows = conn.execute(
                "SELECT source_commit_sha FROM symbol_diagnostics WHERE repo_id=? AND file_id=? ORDER BY id",
                (repo_id, file_id),
            ).fetchall()
            if any(
                str(item["source_commit_sha"]) != target for item in diagnostic_rows
            ):
                raise _error(
                    "catalog_provenance_mismatch",
                    "parser diagnostics are not from the fixture target revision",
                    path=change.path,
                )
            diagnostic_count = len(diagnostic_rows)
            symbol_rows = _symbol_rows(conn, repo_id, [file_id])
            if any(
                int(symbol["repo_id"]) != repo_id or int(symbol["file_id"]) != file_id
                for symbol in symbol_rows
            ):
                raise _error(
                    "catalog_provenance_mismatch",
                    "seed symbol repository ownership mismatch",
                    path=change.path,
                )
            state = (
                "parser_failed"
                if diagnostic_count
                else ("available" if symbol_rows else "symbol_less")
            )
            seed_file = {
                "path": change.path,
                "status": change.status,
                "state": state,
                "symbol_count": 0 if state == "parser_failed" else len(symbol_rows),
                **_file_record(row, target),
            }
            seed_files.append(seed_file)
        if missing:
            raise _error(
                "catalog_provenance_mismatch",
                "non-delete changed target file is missing from the target catalog",
                paths=missing,
                blocked_context={
                    "input": input_data,
                    "preflight": preflight,
                    "changed_files": changed_files,
                    "seed_files": seed_files,
                },
            )

        usable_file_ids = [
            int(item["file_id"]) for item in seed_files if item["state"] == "available"
        ]
        seed_symbols = (
            [
                _symbol_record(row, target)
                for row in _symbol_rows(conn, repo_id, usable_file_ids)
            ]
            if usable_file_ids
            else []
        )
        seed_symbols.sort(key=lambda item: int(item["symbol_id"]))
        seed_ids = {int(item["symbol_id"]) for item in seed_symbols}
        reached_by_id: dict[int, dict[str, Any]] = {}
        transitive_edges: list[dict[str, Any]] = []
        skipped_edges: list[dict[str, Any]] = []
        frontier = sorted(seed_ids)
        partial_reasons: list[str] = []

        for hop in range(1, max_hops + 1):
            if not frontier:
                break
            next_frontier: set[int] = set()
            for batch in _chunks(frontier):
                for row in _edge_rows(conn, batch):
                    _check_edge_provenance(row, repo_id, target)
                    edge = _edge_record(row, target, hop)
                    relation_type = row["relationship_type"]
                    source_id = row["source_symbol_id"]
                    if relation_type not in ALLOWED_RELATIONSHIP_TYPES:
                        edge["skip_reason"] = "non_call_relationship"
                        skipped_edges.append(edge)
                        continue
                    if row["resolution_class"] != "project_resolved":
                        edge["skip_reason"] = "unresolved_resolution"
                        skipped_edges.append(edge)
                        partial_reasons.append("unresolved_resolution")
                        continue
                    if source_id is None or row["source_repo_id"] is None:
                        edge["skip_reason"] = "source_symbol_missing"
                        skipped_edges.append(edge)
                        partial_reasons.append("source_symbol_missing")
                        continue
                    confidence = float(row["confidence"])
                    if confidence <= float(min_confidence):
                        edge["skip_reason"] = "below_confidence"
                        skipped_edges.append(edge)
                        partial_reasons.append("below_confidence")
                        continue
                    source_record = _symbol_record(
                        {
                            "symbol_id": source_id,
                            "repo_id": row["source_repo_id"],
                            "file_id": row["source_file_id"],
                            "name": row["source_db_name"],
                            "kind": row["source_db_kind"],
                            "parent_symbol": row["source_parent_symbol"],
                            "signature": row["source_signature"],
                            "language": row["source_language"],
                            "stable_key": row["source_stable_key"],
                            "start_line": row["source_start_line"],
                            "end_line": row["source_end_line"],
                            "file_path": row["source_actual_file_path"],
                            "blob_object_id": row["source_blob_object_id"],
                            "file_mode": row["source_file_mode"],
                            "size_bytes": row["source_size_bytes"],
                            "file_source_commit_sha": row[
                                "source_file_source_commit_sha"
                            ],
                        },
                        target,
                    )
                    edge["edge_status"] = "traversed"
                    transitive_edges.append(edge)
                    source_int = int(source_id)
                    if source_int in seed_ids:
                        continue
                    existing = reached_by_id.get(source_int)
                    if existing is None or hop < int(existing["minimum_hop"]):
                        source_record["minimum_hop"] = hop
                        source_record["contributing_edge_ids"] = [
                            int(row["relationship_id"])
                        ]
                        reached_by_id[source_int] = source_record
                        next_frontier.add(source_int)
                    elif hop == int(existing["minimum_hop"]):
                        edge_ids = existing["contributing_edge_ids"]
                        if int(row["relationship_id"]) not in edge_ids:
                            edge_ids.append(int(row["relationship_id"]))
            frontier = sorted(next_frontier)

        transitive_edges.sort(key=lambda item: int(item["relationship_id"]))
        skipped_edges.sort(key=lambda item: int(item["relationship_id"]))
        reached_symbols = sorted(
            reached_by_id.values(), key=lambda item: int(item["symbol_id"])
        )
        all_symbol_ids = sorted(seed_ids | set(reached_by_id))
        entity_context = _entity_context(conn, repo_id, all_symbol_ids)
        file_states = {str(item["state"]) for item in seed_files}
        if not seed_symbols and file_states and file_states <= {"symbol_less"}:
            status = "empty"
        else:
            degraded = file_states & {"deleted", "parser_failed", "missing_target_file"}
            degraded.update(
                "symbol_less" for item in seed_files if item["state"] == "symbol_less"
            )
            degraded.update(partial_reasons)
            status = "partial" if degraded else "complete"
        gaps = [
            f"seed_file:{item['path']}:{item['state']}"
            for item in seed_files
            if item["state"] != "available"
        ]
        gaps.extend(f"skipped_edge:{reason}" for reason in sorted(set(partial_reasons)))
        unresolved_edges = [
            item
            for item in skipped_edges
            if item.get("skip_reason") == "unresolved_resolution"
        ]
        gaps.extend(
            f"skipped_edge:unresolved_resolution:{item['target_symbol_name']}"
            for item in unresolved_edges
        )
        warnings = [
            "non_call_relationship rows were intentionally excluded"
            if any(
                item.get("skip_reason") == "non_call_relationship"
                for item in skipped_edges
            )
            else ""
        ]
        if len(unresolved_edges) > UNRESOLVED_EDGE_WARNING_THRESHOLD:
            warnings.append(
                f"{len(unresolved_edges)} unresolved relationship edges were skipped"
            )
        warnings = [item for item in warnings if item]
        return _base_report(
            status=status,
            input_data=input_data,
            preflight=preflight,
            changed_files=changed_files,
            seed_files=seed_files,
            seed_symbols=seed_symbols,
            reached_symbols=reached_symbols,
            transitive_edges=transitive_edges,
            skipped_edges=skipped_edges,
            gaps=gaps,
            warnings=warnings,
            entity_context=entity_context,
        )
    finally:
        conn.close()


def analyze_fixture(
    fixture: str | Path,
    manifest: str | Path,
    active_db: str | Path,
    repo_key: str,
    max_hops: int = 2,
    min_confidence: float = 0.7,
) -> dict[str, Any]:
    """Load a YAML Step 0 fixture and run the in-memory analyzer."""

    fixture_path = Path(fixture)
    return analyze_document(
        _fixture(fixture_path),
        manifest,
        active_db,
        repo_key,
        max_hops=max_hops,
        min_confidence=min_confidence,
        fixture_label=str(fixture_path),
    )
