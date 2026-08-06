"""Safely refresh one registered repository through a SQLite candidate.

The active catalog is never modified until all selected builders succeed and
the checked-out source revision is still the one that was validated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from catalog.content_fingerprint import (
    CATALOG_CONTENT_VERSION,
    logical_content_fingerprint,
)
from catalog.db import migrate_multi_repo
from catalog.delta import (
    DELTA_CONTRACT_VERSION,
    ChangeType,
    DeltaUnavailable,
    RepositoryChangeSet,
    collect_repository_change_set,
    path_is_in_scan_scope,
)
from catalog.migrations import (
    LEGACY_REPO_KEY,
    apply_delta_refresh_migration,
)
from catalog.refresh_attempts import (
    bootstrap_refresh_ledger,
    create_refresh_attempt,
    record_attempt_event,
    record_attempt_failure,
)
from catalog.refresh_contract import (
    EVIDENCE_COMPARISON_VERSION,
    RUNTIME_CONTRACT_VERSION,
    runtime_fingerprint,
)
from catalog.refresh_quality import (
    QUALITY_QUERIES,
    RefreshQualityError,
    collect_global_counts,
    collect_repository_counts,
    resolve_reference_quality_run,
    validate_quality_run,
)
from catalog.refresh_transaction import (
    ParentDescriptor as TransactionParentDescriptor,
)
from catalog.refresh_transaction import (
    assert_parent_unchanged as transaction_assert_parent_unchanged,
)
from catalog.refresh_transaction import (
    backup_database as transaction_backup_database,
)
from catalog.refresh_transaction import (
    parent_descriptor as transaction_parent_descriptor,
)
from catalog.refresh_transaction import (
    promote_catalog_candidate as transaction_promote_catalog_candidate,
)
from catalog.refresh_transaction import (
    refresh_lock as transaction_refresh_lock,
)
from catalog.repositories import (
    get_repository,
    load_workspace_manifest,
    register_manifest,
    rest_automation_paths,
)
from catalog.repository_evidence import repository_evidence_fingerprint
from catalog.repository_lifecycle import (
    RepositoryArchivedError,
    canonical_git_url,
    require_checkout_remote_identity,
    require_repository_extractable,
    verify_github_repository_active,
)
from catalog.rest_automation_contract import CONTRACT_V1, resolve_contract_v1_paths
from catalog.source_revisions import active_source_revisions
from catalog.source_snapshot import SourceSnapshot, materialize_source_snapshot
from scripts.builder_outcome import BuilderDiagnostic, BuilderOutcome
from scripts.builder_registry import (
    build_plan,
    repository_matcher_overrides,
    stage_execution_modes,
)
from validation.validate_catalog_integrity import validate_catalog_connection


class RefreshError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RefreshError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def source_revision(
    root: Path, tracked_branch: str, expected_remote_url: str | None = None
) -> str:
    """Validate a clean checkout and return the configured branch revision."""
    if _git(root, "status", "--porcelain"):
        raise RefreshError(f"repository checkout is dirty: {root}")
    require_checkout_remote_identity(root, expected_remote_url)
    head = _git(root, "rev-parse", "HEAD")
    try:
        current_branch = _git(root, "symbolic-ref", "--short", "HEAD")
    except RefreshError as exc:
        raise RefreshError(
            f"repository checkout is detached; expected branch {tracked_branch}: {root}"
        ) from exc
    if current_branch != tracked_branch:
        raise RefreshError(
            f"checkout branch {current_branch!r} does not match configured branch {tracked_branch!r}"
        )
    try:
        branch_sha = _git(root, "rev-parse", "--verify", tracked_branch)
    except RefreshError as exc:
        raise RefreshError(
            f"configured branch {tracked_branch!r} is unavailable in {root}: {exc}"
        ) from exc
    if head != branch_sha:
        raise RefreshError(
            f"HEAD {head} does not match configured branch {tracked_branch} ({branch_sha})"
        )
    return head


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(_stable_json(manifest).encode()).hexdigest()


def _builder_plan_hash(
    plans: dict[str, list[str]], runtime_hash: str | None = None
) -> str:
    contract = {
        "plans": plans,
        "runtime_fingerprint": runtime_hash or runtime_fingerprint(),
    }
    return hashlib.sha256(_stable_json(contract).encode()).hexdigest()


def _repository_manifest_hash(entry: dict) -> str:
    """Hash evidence-affecting repository configuration, not checkout location."""

    contract = {
        "repo_key": entry["repo_key"],
        "name": entry.get("name"),
        "kind": entry.get("kind"),
        "language": entry.get("language"),
        "remote_url": entry.get("remote_url"),
        "tracked_branch": entry["tracked_branch"],
        "enabled": bool(entry.get("enabled", True)),
        "profile": entry.get("profile") or "generic",
        "builders": list(entry.get("builders") or []),
        "storage": entry.get("storage", "central"),
        "depends_on": entry.get("depends_on"),
        "rest_automation": entry.get("rest_automation"),
    }
    return hashlib.sha256(_stable_json(contract).encode()).hexdigest()


def _repository_plan_hash(plan: list[str], runtime_hash: str | None = None) -> str:
    contract = {
        "plan": plan,
        "runtime_fingerprint": runtime_hash or runtime_fingerprint(),
    }
    return hashlib.sha256(_stable_json(contract).encode()).hexdigest()


def _backup_database(source: Path, target: Path) -> None:
    transaction_backup_database(source, target)


ParentDescriptor = TransactionParentDescriptor


def _parent_descriptor(active: Path) -> ParentDescriptor:
    return transaction_parent_descriptor(active)


def _assert_parent_unchanged(active: Path, expected: ParentDescriptor) -> None:
    try:
        transaction_assert_parent_unchanged(active, expected)
    except Exception as exc:
        raise RefreshError(str(exc)) from exc


@contextmanager
def _refresh_lock(active: Path):
    with transaction_refresh_lock(active) as lock_path:
        yield lock_path


def _manifest_repository(manifest: dict, repo_key: str) -> dict:
    entry = next(
        (item for item in manifest["repositories"] if item["repo_key"] == repo_key),
        None,
    )
    if entry is None:
        raise RefreshError(f"repository not found in manifest: {repo_key}")
    return entry


def _dependency_keys(entry: dict) -> tuple[str, ...]:
    depends_on = entry.get("depends_on")
    if depends_on is None:
        return ()
    return tuple(str(value) for value in depends_on)


def _closure_manifest(manifest: dict, repo_keys: set[str]) -> dict:
    return {
        "version": manifest["version"],
        "repositories": [
            entry
            for entry in manifest["repositories"]
            if str(entry["repo_key"]) in repo_keys
        ],
    }


def _record_run(
    conn: sqlite3.Connection,
    repo_id: int,
    branch: str,
    sha: str,
    plan: list[str],
    *,
    manifest_hash: str | None = None,
    builder_plan_hash: str | None = None,
    stage_modes: dict[str, tuple[str, str]] | None = None,
    source_identity: dict[str, str] | None = None,
    commit: bool = True,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO repo_index_runs(repo_id, tracked_branch, commit_sha, builder_plan_hash, source_identity_json, status)
        VALUES (?, ?, ?, ?, ?, 'building')
        """,
        (
            repo_id,
            branch,
            sha,
            builder_plan_hash or hashlib.sha256(json.dumps(plan).encode()).hexdigest(),
            _stable_json(source_identity) if source_identity is not None else None,
        ),
    )
    run_id = int(cursor.lastrowid)
    conn.executemany(
        """INSERT INTO repo_index_stages(
               run_id,builder_name,status,execution_mode,invalidation_reason
           ) VALUES (?,?,'pending',?,?)""",
        [
            (
                run_id,
                builder,
                (stage_modes or {}).get(builder, (None, None))[0],
                (stage_modes or {}).get(builder, (None, None))[1],
            )
            for builder in plan
        ],
    )
    if manifest_hash is not None:
        conn.execute(
            "UPDATE repo_index_runs SET manifest_hash=? WHERE id=?",
            (manifest_hash, run_id),
        )
    if commit:
        conn.commit()
    return run_id


def _stage(
    conn: sqlite3.Connection,
    run_id: int,
    builder: str,
    status: str,
    error: str | None = None,
    *,
    execution_mode: str | None = None,
    reason: str | None = None,
    affected_count: int | None = None,
    outcome: BuilderOutcome | None = None,
    commit: bool = True,
) -> None:
    timestamp = datetime.now(UTC).isoformat()
    if status == "running":
        conn.execute(
            """UPDATE repo_index_stages SET status=?,started_at=?,
                   execution_mode=COALESCE(?,execution_mode),
                   invalidation_reason=COALESCE(?,invalidation_reason)
               WHERE run_id=? AND builder_name=?""",
            (status, timestamp, execution_mode, reason, run_id, builder),
        )
    else:
        conn.execute(
            """UPDATE repo_index_stages SET status=?,completed_at=?,diagnostic_error=?,
                   execution_mode=COALESCE(?,execution_mode),
                   invalidation_reason=COALESCE(?,invalidation_reason),
                   affected_record_count=?,record_count=?,result_summary=?
               WHERE run_id=? AND builder_name=?""",
            (
                status,
                timestamp,
                error,
                execution_mode,
                reason,
                outcome.affected_count if outcome is not None else affected_count,
                outcome.affected_count if outcome is not None else affected_count,
                outcome.to_json() if outcome is not None else None,
                run_id,
                builder,
            ),
        )
    if commit:
        conn.commit()


_DIAGNOSTIC_FREEFORM_FIELDS = frozenset(
    {"reason", "detail", "message", "timestamp", "context", "source", "stage"}
)


def _source_blob_sha(
    delta_context: dict[str, object], source_path: str | None
) -> str | None:
    if source_path is None:
        return None
    for change in delta_context.get("changed_paths", ()):
        if getattr(change, "new_path", None) == source_path:
            return getattr(change, "new_blob_sha", None)
        if getattr(change, "old_path", None) == source_path:
            return getattr(change, "old_blob_sha", None)
    return None


def _builder_diagnostic(
    *,
    builder: str,
    code: str,
    severity: str,
    record: dict,
    delta_context: dict[str, object],
) -> BuilderDiagnostic:
    raw_path = (
        record.get("source_path")
        or record.get("file_path")
        or record.get("source_file")
    )
    source_path = str(raw_path) if isinstance(raw_path, str) and raw_path else None
    if source_path == "<unknown>" or (source_path and Path(source_path).is_absolute()):
        source_path = None
    identity = {
        str(key): str(value)
        for key, value in sorted(record.items())
        if key not in _DIAGNOSTIC_FREEFORM_FIELDS
        and key not in {"source_path", "file_path", "source_file", "code"}
        and isinstance(value, (str, int))
        and not isinstance(value, bool)
    }
    return BuilderDiagnostic(
        builder=builder,
        code=code,
        severity="error" if severity == "error" else "warning",
        source_path=source_path,
        source_blob_sha=_source_blob_sha(delta_context, source_path),
        identity=identity,
    )


def _read_json_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    if path.suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return (
            [item for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            records.append(value)
    return records


def _outcome_with_diagnostics(
    outcome: BuilderOutcome, diagnostics: list[BuilderDiagnostic]
) -> BuilderOutcome:
    return BuilderOutcome(outcome.affected_count, outcome.metrics, tuple(diagnostics))


def _run_builder(
    builder: str,
    repo_key: str,
    repo_id: int,
    root: Path,
    candidate_db: str,
    manifest_entry: dict,
    *,
    git_root: Path,
    output_root: Path,
    execution_mode: str = "full",
    delta_context: dict[str, object] | None = None,
    coverage_build_context: dict[str, object] | None = None,
) -> BuilderOutcome:
    admission_conn = sqlite3.connect(candidate_db)
    admission_conn.row_factory = sqlite3.Row
    try:
        require_repository_extractable(admission_conn, repo_key)
    finally:
        admission_conn.close()
    delta_context = delta_context if delta_context is not None else {}
    if builder == "scan":
        if execution_mode == "delta":
            from parser.scan_repo import apply_changed_paths

            result = apply_changed_paths(
                delta_context.get("changed_paths", ()),
                repo_key=repo_key,
                db_path=candidate_db,
                source_root=root,
                git_root=git_root,
            )
            delta_context["scan_result"] = result
            return BuilderOutcome(
                result.affected_count,
                {
                    "affected_file_ids": len(result.affected_file_ids),
                    "deleted_files": len(result.deleted_files),
                },
            )
        from parser.scan_repo import scan

        result = scan(repo_key=repo_key, db_path=candidate_db)
        return BuilderOutcome(
            result.affected_count,
            {
                "scanned": result.scanned,
                "added": result.added,
                "updated": result.updated,
                "unchanged": result.unchanged,
                "removed": result.removed,
            },
        )
    elif builder == "symbols":
        from parser.extract_symbols import extract_all

        file_ids = None
        if execution_mode == "delta":
            scan_result = delta_context.get("scan_result")
            file_ids = getattr(scan_result, "affected_file_ids", ())
        summary = extract_all(
            only_changed=False,
            repo_key=repo_key,
            db_path=candidate_db,
            write_logs=True,
            yaml_parse_failures_log=output_root / "yaml_parse_failures.jsonl",
            javascript_parse_failures_log=output_root / "javascript_parse_failures.jsonl",
            file_ids=file_ids,
        )
        delta_context["symbol_summary"] = summary
        return BuilderOutcome(
            summary.affected_count,
            {
                "added": len(summary.added_ids),
                "changed": len(summary.changed_ids),
                "deleted": len(summary.deleted_ids),
            },
        )
    elif builder == "relationships":
        from catalog.db import get_connection
        from parser.extract_relationships import extract_all, relationship_file_closure

        file_ids = None
        reset = execution_mode != "delta"
        if execution_mode == "delta":
            scan_result = delta_context.get("scan_result")
            symbol_summary = delta_context.get("symbol_summary")
            direct_ids = getattr(scan_result, "affected_file_ids", ())
            prior_ids = tuple(delta_context.get("prior_symbol_ids", ()))
            prior_names = tuple(delta_context.get("prior_symbol_names", ()))
            changed_ids = prior_ids + tuple(
                getattr(symbol_summary, "added_ids", ())
                + getattr(symbol_summary, "changed_ids", ())
                + getattr(symbol_summary, "deleted_ids", ())
            )
            changed_names = prior_names + tuple(
                getattr(symbol_summary, "added_names", ())
                + getattr(symbol_summary, "changed_names", ())
                + getattr(symbol_summary, "deleted_names", ())
            )
            closure_conn = get_connection(candidate_db)
            try:
                file_ids = relationship_file_closure(
                    closure_conn,
                    repo_id=repo_id,
                    direct_file_ids=direct_ids,
                    changed_symbol_ids=changed_ids,
                    changed_symbol_names=changed_names,
                )
            finally:
                closure_conn.close()
        inserted = extract_all(
            only_changed=False,
            repo_key=repo_key,
            db_path=candidate_db,
            reset=reset,
            file_ids=file_ids,
        )
        metrics = (
            {"relationships_inserted": inserted}
            if isinstance(inserted, int) and not isinstance(inserted, bool)
            else {}
        )
        return BuilderOutcome(None, metrics)
    elif builder == "entities":
        from scripts import scan_ent_files
        from scripts.build_entities import build

        entities_path = Path(candidate_db).with_name(
            f"{Path(candidate_db).name}.{repo_key}.entities.jsonl"
        )
        try:
            scan_ent_files._scan_repo_root(
                root,
                entities_path,
                missing_metadata_log=output_root / "entity_missing_metadata.jsonl",
            )
            result = build(
                candidate_db,
                entities_path,
                reset=True,
                repo_key=repo_key,
                missing_symbols_path=output_root / f"missing_symbols_{repo_key}.json",
            )
            outcome = BuilderOutcome(
                None,
                {
                    "entities_upserted": result.entities_upserted,
                    "mappings_inserted": result.mappings_inserted,
                    "missing_symbols": result.missing_symbols,
                },
            )
            diagnostics = [
                _builder_diagnostic(
                    builder="entities",
                    code="entity_symbol_unresolved",
                    severity="warning",
                    record=record,
                    delta_context=delta_context,
                )
                for record in _read_json_records(
                    output_root / f"missing_symbols_{repo_key}.json"
                )
            ]
            return _outcome_with_diagnostics(outcome, diagnostics)
        finally:
            entities_path.unlink(missing_ok=True)
    elif builder == "entity_roots":
        from catalog.db import get_connection
        from scripts.build_entity_roots import build_entity_roots

        conn = get_connection(candidate_db)
        try:
            inserted = build_entity_roots(conn, reset=True, repo_id=repo_id)
            return BuilderOutcome(inserted, {"rows_inserted": inserted})
        finally:
            conn.close()
    elif builder == "openapi_scan":
        from catalog.db import get_connection
        from scripts.scan_openapispec import scan_openapispec

        conn = get_connection(candidate_db)
        try:
            result = scan_openapispec(conn, root, repo_id)
            conn.commit()
            outcome = BuilderOutcome(
                result.rows_indexed,
                {
                    "files_processed": result.files_processed,
                    "rows_indexed": result.rows_indexed,
                    "files_missing_in_catalog": result.files_missing_in_catalog,
                    "yaml_parse_failures": result.yaml_parse_failures,
                    "template_files_skipped": result.template_files_skipped,
                },
            )
            diagnostics = [
                _builder_diagnostic(
                    builder="openapi_scan",
                    code=record["code"],
                    severity="error",
                    record=record,
                    delta_context=delta_context,
                )
                for record in result.diagnostics
            ]
            return _outcome_with_diagnostics(outcome, diagnostics)
        finally:
            conn.close()
    elif builder == "api_registry":
        from catalog.api_registry import build_api_registry
        from catalog.db import get_connection

        conn = get_connection(candidate_db)
        try:
            result = build_api_registry(conn, repo_id=repo_id, repo_root=root)
            conn.commit()
            outcome = BuilderOutcome(
                result.entries_written + result.links_written + result.issues_written,
                {
                    "entries_written": result.entries_written,
                    "links_written": result.links_written,
                    "issues_written": result.issues_written,
                },
            )
            diagnostics = [
                _builder_diagnostic(
                    builder="api_registry",
                    code=record["issue_code"],
                    severity=record["severity"],
                    record=record,
                    delta_context=delta_context,
                )
                for record in result.diagnostics
            ]
            return _outcome_with_diagnostics(outcome, diagnostics)
        finally:
            conn.close()
    elif builder == "openapi_link":
        from catalog.db import get_connection
        from scripts.link_openapispec import OPENAPI_MAPPING_TYPES, _link_openapispec

        conn = get_connection(candidate_db)
        try:
            placeholders = ",".join("?" for _ in OPENAPI_MAPPING_TYPES)
            conn.execute(
                f"DELETE FROM entity_mappings WHERE repo_id=? AND mapping_type IN ({placeholders})",
                (repo_id, *OPENAPI_MAPPING_TYPES),
            )
            result = _link_openapispec(
                conn,
                root,
                repo_id,
                None,
                missing_metadata_log=output_root / "openapi_missing_metadata.jsonl",
            )
            conn.commit()
            outcome = BuilderOutcome(
                result.mappings_inserted,
                {
                    "mappings_inserted": result.mappings_inserted,
                    "unmatched_rows": result.unmatched_rows,
                    "mapped_to_matches": result.mapped_to_matches,
                    "mapped_to_unresolved": result.mapped_to_unresolved,
                    "mapped_to_suppressed": result.mapped_to_suppressed,
                    "mapped_to_invalid": result.mapped_to_invalid,
                    "heuristic_total": result.heuristic_total,
                    "heuristic_suppressed_expected_missing_mapped_to": result.heuristic_suppressed_expected_missing_mapped_to,
                    "heuristic_logged": result.heuristic_logged,
                },
            )
            diagnostics = [
                _builder_diagnostic(
                    builder="openapi_link",
                    code="openapi_mapping_unresolved",
                    severity="warning",
                    record=record,
                    delta_context=delta_context,
                )
                for record in _read_json_records(
                    output_root / "openapi_missing_metadata.jsonl"
                )
            ]
            return _outcome_with_diagnostics(outcome, diagnostics)
        finally:
            conn.close()
    elif builder == "ui_surfaces":
        from catalog.db import get_connection
        from catalog.ui_sync import assemble_ui_snapshot, synchronize_ui_snapshot

        conn = get_connection(candidate_db)
        try:
            snapshot = assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=root)
            parser_rows = []
            for diagnostic in snapshot.diagnostics:
                if "parse_error" not in diagnostic.code:
                    continue
                source = root / diagnostic.source_file
                parser_rows.append(
                    {
                        "repo_key": repo_key,
                        "builder": "ui_surfaces",
                        "code": diagnostic.code,
                        "severity": diagnostic.severity,
                        "source_path": diagnostic.source_file,
                        "start_line": diagnostic.start_line,
                        "end_line": diagnostic.end_line,
                        "message": diagnostic.message,
                        "evidence": diagnostic.evidence,
                        "source_sha256": (
                            hashlib.sha256(source.read_bytes()).hexdigest()
                            if source.is_file()
                            else None
                        ),
                    }
                )
            parser_log = output_root / "ui_parser_failures.jsonl"
            parser_log.parent.mkdir(parents=True, exist_ok=True)
            with parser_log.open("w", encoding="utf-8") as handle:
                for row in sorted(
                    parser_rows,
                    key=lambda item: (
                        str(item["source_path"]),
                        int(item["start_line"]),
                        str(item["code"]),
                    ),
                ):
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
            synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
            metrics = {
                table: len(snapshot.rows.get(table, ()))
                for table in (
                    "ui_surfaces",
                    "ui_artifacts",
                    "ui_entity_references",
                    "ui_artifact_includes",
                    "ui_fields",
                    "ui_events",
                    "ui_script_dependencies",
                    "ui_event_calls",
                    "ui_resolution_issues",
                    "ui_source_diagnostics",
                )
            }
            diagnostics = [
                _builder_diagnostic(
                    builder="ui_surfaces",
                    code=str(row["diagnostic_code"]),
                    severity=str(row["severity"]),
                    record=dict(row),
                    delta_context=delta_context,
                )
                for row in conn.execute(
                    """SELECT source_path,source_pointer,diagnostic_key,severity,
                               diagnostic_code,message
                       FROM ui_source_diagnostics WHERE repo_id=?
                       ORDER BY diagnostic_key""",
                    (repo_id,),
                )
            ]
            return _outcome_with_diagnostics(
                BuilderOutcome(sum(metrics.values()), metrics), diagnostics
            )
        finally:
            conn.close()
    elif builder == "workflows":
        from scripts.build_workflows import build

        result = build(
            candidate_db,
            root,
            repo_id,
            reset=True,
            unresolved_log=output_root / "workflows_unresolved_file_ids.jsonl",
            parse_failures_log=output_root / "workflows_parse_failures.jsonl",
        )
        affected = (
            result.workflows_inserted
            + result.workflow_nodes_inserted
            + result.workflow_edges_inserted
            + result.openapi_ref_edges_inserted
        )
        outcome = BuilderOutcome(
            affected,
            {
                "entities_processed": result.entities_processed,
                "workflows_inserted": result.workflows_inserted,
                "file_ids_backfilled": result.file_ids_backfilled,
                "unresolved_source_files": result.unresolved_source_files,
                "workflow_nodes_inserted": result.workflow_nodes_inserted,
                "workflow_edges_inserted": result.workflow_edges_inserted,
                "openapi_ref_edges_inserted": result.openapi_ref_edges_inserted,
                "parse_failures_p0": result.parse_failures_p0,
                "parse_failures_p1": result.parse_failures_p1,
                "parse_failures_p2": result.parse_failures_p2,
            },
        )
        diagnostics = [
            _builder_diagnostic(
                builder="workflows",
                code="workflow_parse_failure",
                severity="error",
                record=record,
                delta_context=delta_context,
            )
            for record in _read_json_records(
                output_root / "workflows_parse_failures.jsonl"
            )
        ]
        diagnostics.extend(
            _builder_diagnostic(
                builder="workflows",
                code="workflow_source_file_unresolved",
                severity="warning",
                record=record,
                delta_context=delta_context,
            )
            for record in _read_json_records(
                output_root / "workflows_unresolved_file_ids.jsonl"
            )
        )
        return _outcome_with_diagnostics(outcome, diagnostics)
    elif builder == "security":
        from scripts.build_security_mappings import build

        result = build(
            candidate_db,
            repo_key=repo_key,
            reset=True,
            max_parse_failures=-1,
            max_unresolved=-1,
            parse_failures_log=output_root / "security_parse_failures.jsonl",
            unresolved_log=output_root / "security_unresolved_keys.jsonl",
            conflicts_log=output_root / "security_conflicts.jsonl",
            unresolved_sources_log=output_root / "security_unresolved_file_ids.jsonl",
        )
        inserted_fields = (
            "operations_inserted",
            "allowops_inserted",
            "policies_inserted",
            "policy_values_inserted",
            "policy_eops_inserted",
            "menus_inserted",
            "menu_items_inserted",
            "menu_links_inserted",
            "dbschema_tables_inserted",
            "dbschema_fields_inserted",
        )
        metric_fields = (
            "files_discovered",
            "files_parsed",
            "files_failed",
            "files_skipped",
            *inserted_fields,
            "unresolved_policy_keys",
            "unresolved_menu_keys",
            "conflicts_detected",
            "missing_includes",
            "allowops_resolved",
            "allowops_unresolved",
            "file_ids_backfilled",
            "unresolved_source_files",
        )
        metrics = {name: int(getattr(result, name)) for name in metric_fields}
        outcome = BuilderOutcome(
            sum(metrics[name] for name in inserted_fields), metrics
        )
        code_map = {
            "parse_error": ("security_parse_error", "error"),
            "extraction_error": ("security_extraction_error", "error"),
            "missing_include": ("security_missing_include", "error"),
            "unsupported_construct": ("security_unsupported_construct", "error"),
            "allowops_unresolved": ("security_allowops_unresolved", "warning"),
            "policy_eop_unresolved": ("security_policy_eop_unresolved", "warning"),
            "menu_key_unresolved": ("security_menu_key_unresolved", "warning"),
            "conflict": ("security_conflict", "warning"),
            "source_file_unresolved": ("security_source_file_unresolved", "warning"),
        }
        diagnostics: list[BuilderDiagnostic] = []
        for name in (
            "security_parse_failures.jsonl",
            "security_unresolved_keys.jsonl",
            "security_conflicts.jsonl",
            "security_unresolved_file_ids.jsonl",
        ):
            for record in _read_json_records(output_root / name):
                category = str(record.get("category") or "")
                code, severity = code_map.get(
                    category,
                    (
                        "security_source_file_unresolved"
                        if name == "security_unresolved_file_ids.jsonl"
                        else "security_conflict"
                        if name == "security_conflicts.jsonl"
                        else "security_parse_error"
                        if name == "security_parse_failures.jsonl"
                        else "security_menu_key_unresolved",
                        "error"
                        if name == "security_parse_failures.jsonl"
                        else "warning",
                    ),
                )
                diagnostics.append(
                    _builder_diagnostic(
                        builder="security",
                        code=code,
                        severity=severity,
                        record=record,
                        delta_context=delta_context,
                    )
                )
        return _outcome_with_diagnostics(outcome, diagnostics)
    elif builder == "rest_endpoints":
        from scripts.build_rest_endpoints import build

        result = build(candidate_db, root, repo_id, reset=True)
        metrics = {
            "specs_processed": result.specs_processed,
            "endpoints_inserted": result.endpoints_inserted,
            "endpoints_updated": result.endpoints_updated,
            "yaml_parse_failures": result.yaml_parse_failures,
            "no_paths_found": result.no_paths_found,
            "symbol_fallback_files": result.symbol_fallback_files,
            "symbol_fallback_endpoints": result.symbol_fallback_endpoints,
            "schema_bridge_hits": result.schema_bridge_hits,
            "schema_bridge_overrides": result.schema_bridge_overrides,
        }
        outcome = BuilderOutcome(
            result.endpoints_inserted + result.endpoints_updated, metrics
        )
        diagnostics = [
            _builder_diagnostic(
                builder="rest_endpoints",
                code=record["code"],
                severity=(
                    "warning" if record["code"] == "rest_no_paths_found" else "error"
                ),
                record=record,
                delta_context=delta_context,
            )
            for record in result.diagnostics
        ]
        return _outcome_with_diagnostics(outcome, diagnostics)
    elif builder == "entity_semantics":
        from scripts.build_entity_semantics import build

        result = build(candidate_db, root, repo_key, reset=True)
        metrics = {
            "occurrences": result["occurrences"],
            "components": result["components"],
            "facts": result["facts"],
            "partial": result["partial"],
            "failed": result["failed"],
            "conflicts": result["conflicts"],
        }
        outcome = BuilderOutcome(
            metrics["components"] + metrics["facts"] + metrics["conflicts"],
            metrics,
        )
        diagnostic_conn = sqlite3.connect(candidate_db)
        diagnostic_conn.row_factory = sqlite3.Row
        try:
            records = [
                dict(row)
                for row in diagnostic_conn.execute(
                    """SELECT source_path,declaration_family,status
                       FROM entity_extraction_coverage
                       WHERE repo_id=? AND status='failed' ORDER BY source_path,declaration_family""",
                    (repo_id,),
                )
            ]
        finally:
            diagnostic_conn.close()
        diagnostics = [
            _builder_diagnostic(
                builder="entity_semantics",
                code="entity_semantics_source_read_error",
                severity="error",
                record=record,
                delta_context=delta_context,
            )
            for record in records
        ]
        return _outcome_with_diagnostics(outcome, diagnostics)
    elif builder == "entity_access_links":
        from scripts.build_entity_access_links import build

        result = build(
            candidate_db,
            reset=True,
            repo_key=repo_key,
            unresolved_security_log=output_root
            / "entity_access_unresolved_security.jsonl",
        )
        outcome = BuilderOutcome(
            result.rows_inserted,
            {
                "rows_inserted": result.rows_inserted,
                "security_keys_linked": result.security_keys_linked,
                "security_keys_unresolved": result.security_keys_unresolved,
            },
        )
        diagnostics = [
            _builder_diagnostic(
                builder="entity_access_links",
                code="entity_security_key_unresolved",
                severity="warning",
                record=record,
                delta_context=delta_context,
            )
            for record in _read_json_records(
                output_root / "entity_access_unresolved_security.jsonl"
            )
        ]
        return _outcome_with_diagnostics(outcome, diagnostics)
    elif builder == "gherkin_coverage":
        from catalog.db import get_connection
        from scripts.build_gherkin_coverage import build

        features_root, object_mapping = rest_automation_paths(manifest_entry, root)
        # Contract-V1 evidence is catalog-owned; target checkouts contribute
        # only feature text and allowlisted properties metadata.
        settings = manifest_entry["rest_automation"]
        legacy_contract_v1 = (
            settings.get("coverage_contract_version") == CONTRACT_V1
            and "object_mapping" in settings
        )
        contract_v1_paths = (
            resolve_contract_v1_paths(settings, root) if legacy_contract_v1 else None
        )
        static_contract_v1 = (
            settings.get("coverage_contract_version") == CONTRACT_V1
            and not legacy_contract_v1
        )
        conn = get_connection(candidate_db)
        try:
            production_endpoints = conn.execute(
                """
                SELECT COUNT(*) FROM rest_endpoints re
                JOIN repos r ON r.id = re.repo_id
                WHERE r.repo_key = ? AND re.source_version IS NOT NULL
                """,
                (LEGACY_REPO_KEY,),
            ).fetchone()[0]
            if not production_endpoints:
                raise RefreshError(
                    f"{LEGACY_REPO_KEY} REST endpoints are absent; refresh {LEGACY_REPO_KEY} before REST automation coverage"
                )
            result = build(
                conn,
                repo_key=repo_key,
                suite_root=root,
                object_mapping_path=object_mapping,
                features_root=features_root,
                contract_v1_paths=contract_v1_paths,
                static_contract_v1=static_contract_v1,
                candidate_build_token=(coverage_build_context or {}).get("build_token"),
                indexed_suite_target_sha=(coverage_build_context or {}).get("target_sha"),
                dependency_revisions=(coverage_build_context or {}).get("dependency_revisions"),
            )
            metrics = {
                "features": result["features"],
                "cases": result["cases"],
                "requests": result["requests"],
                "links": result["links"],
                "diagnostics": result["diagnostics"],
                "compatibility_rows": result["compatibility_rows"],
            }
            outcome = BuilderOutcome(
                metrics["cases"] + metrics["requests"] + metrics["links"], metrics
            )
            diagnostic_rows = conn.execute(
                """SELECT td.kind,f.path,td.source_line
                   FROM test_diagnostics td LEFT JOIN files f ON f.id=td.file_id
                   WHERE td.repo_id=? AND td.kind IN ('feature_parse_error','duplicate_object_alias')
                   ORDER BY td.kind,f.path,td.source_line""",
                (repo_id,),
            ).fetchall()
            diagnostics = [
                _builder_diagnostic(
                    builder="gherkin_coverage",
                    code=(
                        "gherkin_feature_parse_error"
                        if row[0] == "feature_parse_error"
                        else "gherkin_duplicate_object_alias"
                    ),
                    severity="error" if row[0] == "feature_parse_error" else "warning",
                    record={"source_path": row[1], "source_line": row[2] or 0},
                    delta_context=delta_context,
                )
                for row in diagnostic_rows
            ]
            return _outcome_with_diagnostics(outcome, diagnostics)
        finally:
            conn.close()
    else:
        raise RefreshError(
            f"builder {builder!r} has no repository-scoped runner yet; "
            "do not enable it until its builder migration is installed"
        )


def _validate_candidate(conn: sqlite3.Connection, repo_id: int) -> None:
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RefreshError(f"candidate foreign key violations: {violations[:3]}")
    duplicate_paths = conn.execute(
        "SELECT path FROM files WHERE repo_id=? GROUP BY path HAVING COUNT(*) > 1",
        (repo_id,),
    ).fetchall()
    if duplicate_paths:
        raise RefreshError(
            f"candidate has duplicate repository paths: {duplicate_paths[:3]}"
        )


def _record_failed_refresh(
    active: Path,
    manifest: dict | None,
    repo_key: str,
    error: Exception,
    failed_step: str | None = None,
    requested_mode: str = "auto",
    effective_mode: str = "not_started",
) -> None:
    """Record compatibility history after the required attempt-ledger event.

    This is not the authoritative failure record; ``refresh_attempts`` is.
    It is deliberately not best-effort: a caller that elects to update legacy
    run/build history must observe a write failure rather than hiding it.
    """
    conn = sqlite3.connect(active)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        try:
            repo = get_repository(conn, repo_key)
        except Exception:
            active_build_exists = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_builds'"
                ).fetchone()
                and conn.execute(
                    "SELECT 1 FROM catalog_builds WHERE status='active' LIMIT 1"
                ).fetchone()
            )
            if manifest is None or active_build_exists:
                raise
            register_manifest(conn, _closure_manifest(manifest, {repo_key}))
            repo = get_repository(conn, repo_key)
        run_id = conn.execute(
                """INSERT INTO repo_index_runs(
                       repo_id, tracked_branch, status, diagnostic_error, completed_at
                   ) VALUES (?, ?, 'failed', ?, CURRENT_TIMESTAMP)""",
                (int(repo["id"]), str(repo["tracked_branch"]), str(error)),
        ).lastrowid
        if failed_step is not None:
            conn.execute(
                    """INSERT INTO repo_index_stages(
                           run_id,builder_name,status,started_at,completed_at,
                           diagnostic_error
                       ) VALUES (?,?,'failed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?)""",
                    (run_id, failed_step, str(error)),
            )
        bundles = getattr(error, "error_bundle_by_repo", {})
        if repo_key in bundles:
            _write_error_bundle(
                conn,
                int(repo["id"]),
                getattr(error, "error_bundle_target_shas", {}).get(repo_key),
                list(bundles[repo_key]),
                output_root=getattr(error, "error_output_root", None),
            )
        conn.execute(
                """UPDATE repos SET
                       last_attempt_status='failed', last_attempted_at=CURRENT_TIMESTAMP,
                       last_attempt_error=?
                   WHERE id=?""",
                (str(error), int(repo["id"])),
        )
        if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_builds'"
        ).fetchone():
            parent = conn.execute(
                    "SELECT id FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            revisions = active_source_revisions(conn)
            conn.execute(
                    """INSERT INTO catalog_builds(
                           build_token,parent_catalog_build_id,catalog_path,
                           requested_mode,effective_mode,status,source_revisions_json,
                           delta_contract_version,completed_at,diagnostic_error
                       ) VALUES (?,?,?,?,?,'failed',?,?,CURRENT_TIMESTAMP,?)""",
                    (
                        str(uuid.uuid4()),
                        int(parent[0]) if parent else None,
                        str(active),
                        requested_mode,
                        effective_mode,
                        _stable_json(revisions),
                        DELTA_CONTRACT_VERSION,
                        f"{repo_key}:{failed_step}: {error}",
                    ),
            )
        conn.commit()
    finally:
        conn.close()


def _resolve_refresh_order(manifest: dict, repo_key: str) -> list[str]:
    """Return a validated, dependency-first refresh order."""
    selected = _manifest_repository(manifest, repo_key)
    if selected.get("storage", "central") == "sidecar":
        raise RefreshError(
            f"repository {repo_key} uses sidecar storage; run scripts/refresh_gateway_sidecar.py instead"
        )
    ordered: list[str] = []
    completed: set[str] = set()
    visiting: list[str] = []
    visiting_set: set[str] = set()

    def visit(current_key: str) -> None:
        if current_key in completed:
            return
        if current_key in visiting_set:
            cycle_start = visiting.index(current_key)
            cycle = visiting[cycle_start:] + [current_key]
            raise RefreshError(f"cyclic repository dependency at {' -> '.join(cycle)}")

        entry = _manifest_repository(manifest, current_key)
        if not entry.get("enabled", True):
            if current_key == repo_key:
                raise RefreshError(f"repository is disabled: {current_key}")
            raise RefreshError(
                f"repository {repo_key} depends on disabled repository: {current_key}"
            )

        visiting.append(current_key)
        visiting_set.add(current_key)
        try:
            for dependency in _dependency_keys(entry):
                visit(dependency)
        finally:
            visiting.pop()
            visiting_set.remove(current_key)
        completed.add(current_key)
        ordered.append(current_key)

    visit(repo_key)
    return ordered


def _validate_refresh_preconditions(
    manifest: dict, refresh_order: list[str], active_db: Path | None = None
) -> dict[str, str]:
    """Validate lifecycle and every checkout before candidate/snapshot work."""
    revisions: dict[str, str] = {}
    conn = sqlite3.connect(active_db) if active_db is not None else None
    if conn is not None:
        conn.row_factory = sqlite3.Row
    try:
        for repo_key in refresh_order:
            entry = _manifest_repository(manifest, repo_key)
            if conn is not None:
                # A first refresh has no persisted row yet; otherwise stored
                # manual archival wins without touching its checkout.
                exists = conn.execute(
                    "SELECT 1 FROM repos WHERE repo_key=?", (repo_key,)
                ).fetchone()
                if exists is not None:
                    require_repository_extractable(conn, repo_key)
            root = Path(entry["local_root"]).expanduser()
            if not root.is_dir():
                raise RefreshError(
                    f"repository {repo_key} checkout root does not exist: {root}"
                )
            resolved_root = root.resolve()
            try:
                verify_github_repository_active(
                    remote_url=entry.get("remote_url"),
                    root=resolved_root,
                    branch=entry.get("tracked_branch"),
                )
            except RepositoryArchivedError as exc:
                exc.repository_key = repo_key
                raise
            revisions[repo_key] = source_revision(
                resolved_root, str(entry["tracked_branch"]), entry.get("remote_url")
            )
            if entry.get("profile") == "rest_automation":
                rest_automation_paths(entry, resolved_root)
    finally:
        if conn is not None:
            conn.close()
    return revisions


def _persist_github_archive(active: Path, repo_key: str, detail: str) -> None:
    """Retain provider-confirmed archival evidence without changing manifest."""
    conn = sqlite3.connect(active)
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(repos)")}
        if {"lifecycle_state", "archive_source", "archive_reason", "archived_at"}.issubset(columns):
            conn.execute(
                """UPDATE repos SET lifecycle_state='archived',archive_source='github',
                   archive_reason=?,archived_at=CURRENT_TIMESTAMP WHERE repo_key=?""",
                (detail, repo_key),
            )
            repo = conn.execute("SELECT id FROM repos WHERE repo_key=?", (repo_key,)).fetchone()
            if repo is not None:
                archive_error = {
                    "builder": "repository_admission", "code": "github_repository_archived",
                    "severity": "error", "source_path": None, "source_blob_sha": None,
                    "identity": {"repo_key": repo_key}, "message": detail,
                }
                conn.execute(
                    """INSERT INTO repo_error_bundles(repo_id,errors_json)
                       VALUES (?,?) ON CONFLICT(repo_id) DO UPDATE SET
                       errors_json=excluded.errors_json,updated_at=CURRENT_TIMESTAMP""",
                    (int(repo[0]), _stable_json([archive_error])),
                )
                conn.execute(
                    """INSERT INTO repo_error_bundle_carry_forwards(repo_id,archive_error_json)
                       VALUES (?,?) ON CONFLICT(repo_id) DO UPDATE SET
                       archive_error_json=excluded.archive_error_json,updated_at=CURRENT_TIMESTAMP""",
                    (int(repo[0]), _stable_json(archive_error)),
                )
            conn.commit()
    finally:
        conn.close()


def _semantic_blockers(outcomes: dict[str, BuilderOutcome]) -> list[dict[str, object]]:
    """Only parser failures are explicitly tolerated during promotion."""
    return [
        diagnostic.to_dict()
        for outcome in outcomes.values()
        for diagnostic in outcome.diagnostics
        if diagnostic.severity == "error" and "parse_error" not in diagnostic.code
    ]


def _write_error_bundle(
    conn: sqlite3.Connection, repo_id: int, target_commit_sha: str | None,
    errors: list[dict[str, object]], *, output_root: Path | None = None,
) -> None:
    carry_rows = conn.execute(
        "SELECT archive_error_json FROM repo_error_bundle_carry_forwards WHERE repo_id=?",
        (repo_id,),
    ).fetchall()
    carried = [json.loads(str(row[0])) for row in carry_rows]
    payload = list(errors) + carried
    if output_root is not None:
        payload.append({
            "builder": "refresh", "code": "raw_error_output",
            "severity": "error", "source_path": None, "source_blob_sha": None,
            "identity": {"output_root": str(output_root)},
        })
    conn.execute(
        """INSERT INTO repo_error_bundles(repo_id,target_commit_sha,errors_json)
           VALUES (?,?,?) ON CONFLICT(repo_id) DO UPDATE SET
           target_commit_sha=excluded.target_commit_sha,errors_json=excluded.errors_json,
           updated_at=CURRENT_TIMESTAMP""",
        (repo_id, target_commit_sha, _stable_json(payload)),
    )


def _record_stale_parser_evidence(
    candidate: sqlite3.Connection, active_db: Path, repo_id: int,
    target_commit_sha: str, change: RepositoryChangeSet,
    outcomes: dict[str, BuilderOutcome],
) -> list[dict[str, object]]:
    """Mark only parser-failed files with exact prior-file provenance stale."""
    diagnostics = [
        diagnostic.to_dict()
        for outcome in outcomes.values()
        for diagnostic in outcome.diagnostics
        if diagnostic.severity == "error" and "parse_error" in diagnostic.code
        and diagnostic.source_path is not None
    ]
    by_new_path = {
        item.new_path: item.old_path for item in change.changed_paths
        if item.new_path is not None and item.old_path is not None
    }
    active = sqlite3.connect(f"file:{active_db}?mode=ro", uri=True)
    active.row_factory = sqlite3.Row
    retained: list[dict[str, object]] = []
    try:
        for diagnostic in diagnostics:
            path = str(diagnostic["source_path"])
            prior_path = by_new_path.get(path, path)
            prior = active.execute(
                "SELECT sha1 FROM files WHERE repo_id=? AND path=?", (repo_id, prior_path)
            ).fetchone()
            if prior is None:
                # There is no last-known-good source evidence to retain.
                continue
            candidate.execute(
                """INSERT INTO repo_stale_evidence(
                       repo_id,source_path,prior_source_path,prior_source_blob_sha,
                       target_commit_sha,diagnostic_json)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(repo_id,source_path) DO UPDATE SET
                       prior_source_path=excluded.prior_source_path,
                       prior_source_blob_sha=excluded.prior_source_blob_sha,
                       target_commit_sha=excluded.target_commit_sha,
                       diagnostic_json=excluded.diagnostic_json,retained_at=CURRENT_TIMESTAMP""",
                (repo_id, path, prior_path, prior["sha1"], target_commit_sha, _stable_json(diagnostic)),
            )
            retained.append({
                "source_path": path, "prior_source_path": prior_path,
                "prior_source_blob_sha": prior["sha1"], "diagnostic": diagnostic,
            })
        failed_paths = {str(item["source_path"]) for item in diagnostics}
        if not failed_paths:
            candidate.execute("DELETE FROM repo_stale_evidence WHERE repo_id=?", (repo_id,))
    finally:
        active.close()
    return retained


def _recheck_source_revisions(
    manifest: dict, expected_revisions: dict[str, str]
) -> None:
    for repo_key, expected in expected_revisions.items():
        entry = _manifest_repository(manifest, repo_key)
        root = Path(entry["local_root"]).expanduser().resolve()
        actual = source_revision(root, str(entry["tracked_branch"]), entry.get("remote_url"))
        if actual != expected:
            raise RefreshError(
                f"repository revision changed while refresh was running: {repo_key} expected={expected} actual={actual}"
            )


def _verify_candidate_sources(
    conn: sqlite3.Connection, repo_id: int, source_root: Path
) -> None:
    failures: list[str] = []
    catalog_rows = conn.execute(
        "SELECT path,sha1 FROM files WHERE repo_id=? ORDER BY path", (repo_id,)
    ).fetchall()
    catalog_paths = {str(row[0]) for row in catalog_rows}
    snapshot_paths = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*")
        if path.is_file()
        and path_is_in_scan_scope(path.relative_to(source_root).as_posix())
    }
    for path in sorted(snapshot_paths - catalog_paths):
        failures.append(f"target snapshot file missing from catalog: {path}")
    for row in catalog_rows:
        path = str(row[0])
        if not path_is_in_scan_scope(path):
            failures.append(f"out-of-scope catalog file: {path}")
            continue
        source = source_root / path
        if not source.is_file():
            failures.append(f"catalog file missing from target snapshot: {path}")
            continue
        digest = hashlib.sha1(source.read_bytes()).hexdigest()
        if digest != row[1]:
            failures.append(
                f"catalog/source hash mismatch: {path} catalog={row[1]} snapshot={digest}"
            )
    if failures:
        raise RefreshError(
            "candidate source verification failed: " + "; ".join(failures)
        )


def _quality_parent_state(
    active: Path, repo_keys: list[str]
) -> tuple[dict[str, dict[str, int]], dict[str, set[str]], dict[str, tuple[int, dict]]]:
    counts: dict[str, dict[str, int]] = {}
    diagnostic_keys: dict[str, set[str]] = {}
    baselines: dict[str, tuple[int, dict]] = {}
    conn = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for repo_key in repo_keys:
            repo = conn.execute(
                "SELECT id FROM repos WHERE repo_key=?", (repo_key,)
            ).fetchone()
            if repo is None:
                counts[repo_key] = {name: 0 for name in QUALITY_QUERIES}
                diagnostic_keys[repo_key] = set()
                continue
            repo_id = int(repo[0])
            counts[repo_key] = collect_repository_counts(conn, repo_id)
            rows = conn.execute(
                """SELECT id,validation_summary FROM repo_index_runs
                   WHERE repo_id=? AND status='active' AND validation_summary IS NOT NULL
                   ORDER BY id DESC""",
                (repo_id,),
            ).fetchall()
            materialized: tuple[int, dict] | None = None
            for row in rows:
                try:
                    summary = json.loads(str(row[1]))
                    validate_quality_run(summary)
                except (TypeError, json.JSONDecodeError, RefreshQualityError):
                    continue
                if summary.get("kind") == "materialized":
                    materialized = (int(row[0]), summary)
                    break
                if summary.get("kind") == "reference":
                    try:
                        materialized = resolve_reference_quality_run(
                            conn, repo_id, int(row[0]), summary
                        )
                    except RefreshQualityError:
                        continue
                    break
            if materialized is not None:
                baselines[repo_key] = materialized
                diagnostic_keys[repo_key] = {
                    str(item["diagnostic_key"])
                    for item in materialized[1].get("diagnostics", [])
                }
            else:
                diagnostic_keys[repo_key] = set()
    finally:
        conn.close()
    return counts, diagnostic_keys, baselines


def _quality_parent_global_state(active: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
    try:
        return collect_global_counts(conn)
    finally:
        conn.close()


def _quality_diagnostics(
    outcomes: dict[str, BuilderOutcome],
) -> list[dict[str, object]]:
    diagnostics = [
        diagnostic.to_dict()
        for outcome in outcomes.values()
        for diagnostic in outcome.diagnostics
    ]
    return sorted(diagnostics, key=lambda item: str(item["diagnostic_key"]))


def _changed_input_paths(change: RepositoryChangeSet) -> tuple[str, ...]:
    """Return both sides of every change for builder invalidation."""

    return tuple(
        sorted(
            {
                candidate
                for changed_path in change.changed_paths
                for candidate in (changed_path.old_path, changed_path.new_path)
                if candidate is not None
            }
        )
    )


def _missing_builder_hydrations(
    active: Path, repo_key: str, plan: list[str], target_commit_sha: str
) -> set[str]:
    """Return builders that cannot safely be skipped for this source identity."""
    conn = sqlite3.connect(f"file:{active}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT id FROM repos WHERE repo_key=?", (repo_key,)).fetchone()
        if row is None:
            return set(plan)
        state = {str(item[0]): (str(item[1]), item[2]) for item in conn.execute(
            "SELECT builder_name,coverage_status,last_attempt_commit_sha "
            "FROM repo_builder_hydrations WHERE repo_id=?", (int(row[0]),)
        )}
        return {
            builder for builder in plan
            if builder not in state or (
                state[builder][0] != "complete" and state[builder][1] != target_commit_sha
            )
        }
    except sqlite3.Error:
        # Missing metadata is never inferred as hydrated.
        return set(plan)
    finally:
        conn.close()


def _indexed_revisions(active: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(active)
    conn.row_factory = sqlite3.Row
    try:
        return active_source_revisions(conn)
    finally:
        conn.close()


def _active_catalog_contract(
    active: Path,
    repo_keys: list[str],
) -> tuple[sqlite3.Row | None, dict[str, str | None], dict[str, sqlite3.Row]]:
    conn = sqlite3.connect(active)
    conn.row_factory = sqlite3.Row
    try:
        indexed_revisions = active_source_revisions(conn)
        build = conn.execute(
            "SELECT * FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if build is not None and build["content_fingerprint"]:
            actual = logical_content_fingerprint(conn)
            if str(build["content_fingerprint"]) != actual:
                raise RefreshError(
                    "active catalog logical fingerprint does not match stored generation"
                )

        contracts: dict[str, sqlite3.Row] = {}
        for repo_key in repo_keys:
            row = conn.execute(
                """SELECT rir.manifest_hash,rir.builder_plan_hash,rir.commit_sha
                   FROM repo_index_runs rir
                   JOIN repos r ON r.id=rir.repo_id
                   WHERE r.repo_key=? AND rir.status='active'
                     AND rir.commit_sha IS r.indexed_commit_sha
                   ORDER BY rir.id DESC LIMIT 1""",
                (repo_key,),
            ).fetchone()
            if row is not None:
                contracts[repo_key] = row
        return build, indexed_revisions, contracts
    finally:
        conn.close()


def _plan_repository_changes(
    active: Path,
    manifest: dict,
    refresh_order: list[str],
    requested_mode: str,
    start_revisions: dict[str, str],
    plans: dict[str, list[str]],
    runtime_hash: str | None = None,
) -> list[RepositoryChangeSet]:
    # Mode is source identity only. Runtime/project/manifest/plan hashes are
    # audit metadata and must never turn an otherwise valid Git range into a
    # full refresh.
    if requested_mode == "full":
        active_build = None
        indexed_revisions = _indexed_revisions(active)
        contracts: dict[str, sqlite3.Row] = {}
        source_revisions: dict[str, object] = {}
        global_reason = None
    else:
        try:
            active_build, indexed_revisions, contracts = _active_catalog_contract(
                active, refresh_order
            )
        except (sqlite3.Error, KeyError, IndexError, TypeError, ValueError) as exc:
            active_build = None
            indexed_revisions = _indexed_revisions(active)
            contracts = {}
            source_revisions = {}
            global_reason = f"compatibility metadata unavailable: {exc}"
        else:
            global_reason: str | None = None
            source_revisions = {}
            if active_build is None:
                global_reason = (
                    "compatibility metadata unavailable: no active catalog build"
                )
            else:
                if not active_build["content_fingerprint"]:
                    global_reason = "active content fingerprint unavailable"
                if global_reason is None:
                    try:
                        parsed = json.loads(str(active_build["source_revisions_json"]))
                        if not isinstance(parsed, dict):
                            raise TypeError("source revisions are not a mapping")
                        source_revisions = parsed
                    except (
                        KeyError,
                        IndexError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        global_reason = f"compatibility metadata unavailable: {exc}"

    changes: list[RepositoryChangeSet] = []
    for repo_key in refresh_order:
        entry = _manifest_repository(manifest, repo_key)
        root = Path(entry["local_root"]).expanduser().resolve()
        compatibility_reason = global_reason
        if requested_mode != "full" and compatibility_reason is None:
            indexed_sha = indexed_revisions.get(repo_key)
            if source_revisions.get(repo_key) != indexed_sha:
                compatibility_reason = (
                    "active generation revision metadata is inconsistent"
                )
            else:
                contract = contracts.get(repo_key)
                if contract is None:
                    compatibility_reason = "compatibility metadata unavailable"
        if compatibility_reason is not None:
            if requested_mode == "delta":
                raise DeltaUnavailable(f"{repo_key}: {compatibility_reason}")
            changes.append(
                RepositoryChangeSet(
                    repo_key,
                    indexed_revisions.get(repo_key),
                    start_revisions[repo_key],
                    requested_mode,
                    "full",
                    (),
                    compatibility_reason,
                )
            )
            continue
        try:
            change = collect_repository_change_set(
                repo_key=repo_key,
                root=root,
                tracked_branch=str(entry["tracked_branch"]),
                base_commit_sha=indexed_revisions.get(repo_key),
                requested_mode=requested_mode,
                target_commit_sha=start_revisions[repo_key],
            )
        except DeltaUnavailable as exc:
            if requested_mode == "delta":
                raise
            change = RepositoryChangeSet(
                repo_key,
                indexed_revisions.get(repo_key),
                start_revisions[repo_key],
                requested_mode,
                "full",
                (),
                str(exc),
            )
        changes.append(change)
    return changes


def _record_change_set(
    conn: sqlite3.Connection,
    *,
    catalog_build_id: int,
    run_id: int,
    repo_id: int,
    change: RepositoryChangeSet,
) -> int:
    counts = {kind: 0 for kind in ChangeType}
    for path in change.changed_paths:
        counts[path.change_type] += 1
    cursor = conn.execute(
        """INSERT INTO repo_change_sets(
               catalog_build_id,repo_index_run_id,repo_id,base_commit_sha,
               target_commit_sha,requested_mode,effective_mode,status,
               fallback_reason,added_count,modified_count,deleted_count,renamed_count
           ) VALUES (?,?,?,?,?,?,?,'planned',?,?,?,?,?)""",
        (
            catalog_build_id,
            run_id,
            repo_id,
            change.base_commit_sha,
            change.target_commit_sha,
            change.requested_mode,
            change.effective_mode,
            change.fallback_reason,
            counts[ChangeType.ADDED],
            counts[ChangeType.MODIFIED],
            counts[ChangeType.DELETED],
            counts[ChangeType.RENAMED],
        ),
    )
    change_set_id = int(cursor.lastrowid)
    conn.executemany(
        """INSERT INTO repo_changed_paths(
               change_set_id,change_type,old_path,new_path,old_mode,new_mode,
               old_blob_sha,new_blob_sha,rename_score
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        [
            (
                change_set_id,
                path.change_type.value,
                path.old_path,
                path.new_path,
                path.old_mode,
                path.new_mode,
                path.old_blob_sha,
                path.new_blob_sha,
                path.rename_score,
            )
            for path in change.changed_paths
        ],
    )
    return change_set_id


def _promote_catalog_candidate(
    active: Path, candidate: Path, previous: Path, token: str
) -> None:
    transaction_promote_catalog_candidate(active, candidate, previous, token)


def _record_noop_attempts(
    active: Path,
    manifest: dict,
    changes: list[RepositoryChangeSet],
    plans: dict[str, list[str]],
    *,
    parent: ParentDescriptor,
    start_revisions: dict[str, str],
) -> None:
    conn = sqlite3.connect(active)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        register_manifest(
            conn,
            _closure_manifest(manifest, {change.repo_key for change in changes}),
        )
        build = conn.execute(
            "SELECT id FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if build is None:
            raise RefreshError("cannot record no-op without an active catalog build")
        for change in changes:
            entry = _manifest_repository(manifest, change.repo_key)
            repo = get_repository(conn, change.repo_key)
            modes = {
                name: ("skipped", "repository revision unchanged")
                for name in plans[change.repo_key]
            }
            run_id = _record_run(
                conn,
                int(repo["id"]),
                str(repo["tracked_branch"]),
                change.target_commit_sha,
                plans[change.repo_key],
                manifest_hash=_repository_manifest_hash(entry),
                builder_plan_hash=_repository_plan_hash(plans[change.repo_key]),
                stage_modes=modes,
                source_identity={
                    "canonical_git_url": canonical_git_url(entry.get("remote_url")) or "",
                    "tracked_branch": str(repo["tracked_branch"]),
                    "target_commit_sha": change.target_commit_sha,
                },
                commit=False,
            )
            change_id = _record_change_set(
                conn,
                catalog_build_id=int(build["id"]),
                run_id=run_id,
                repo_id=int(repo["id"]),
                change=change,
            )
            for builder in plans[change.repo_key]:
                _stage(
                    conn,
                    run_id,
                    builder,
                    "skipped",
                    execution_mode="skipped",
                    reason="repository revision unchanged",
                    outcome=BuilderOutcome(0, {}),
                    commit=False,
                )
            conn.execute(
                "UPDATE repo_index_runs SET validation_summary=? WHERE id=?",
                (_stable_json({"kind": "audit", "mode": "noop"}), run_id),
            )
            conn.execute(
                "UPDATE repo_change_sets SET status='succeeded',started_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (change_id,),
            )
            conn.execute(
                "UPDATE repo_index_runs SET status='active',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (run_id,),
            )
            conn.execute(
                """UPDATE repos SET last_attempt_status='active',
                       last_attempted_at=CURRENT_TIMESTAMP,last_attempt_error=NULL
                   WHERE id=?""",
                (int(repo["id"]),),
            )
        # These checks are deliberately inside the same metadata transaction
        # and immediately precede COMMIT.  A moving source or parent rolls all
        # no-op history back instead of leaving a false active run behind.
        _recheck_source_revisions(manifest, start_revisions)
        _assert_parent_unchanged(active, parent)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _refresh_repository_closure(
    active_db: Path,
    manifest: dict,
    refresh_order: list[str],
    requested_mode: str,
    *,
    start_revisions: dict[str, str] | None = None,
    manifest_path: Path | None = None,
    attempt_token: str | None = None,
) -> None:
    """Refresh one dependency closure through one candidate and one promotion."""

    if requested_mode not in {"auto", "full", "delta"}:
        raise RefreshError(f"unsupported refresh mode: {requested_mode}")
    runtime_hash = runtime_fingerprint()
    parent = _parent_descriptor(active_db)
    start_revisions = start_revisions or _validate_refresh_preconditions(
        manifest, refresh_order, active_db
    )
    plans: dict[str, list[str]] = {}
    for repo_key in refresh_order:
        entry = _manifest_repository(manifest, repo_key)
        plans[repo_key] = build_plan(
            str(entry.get("profile") or "generic"), entry.get("builders") or []
        )
    try:
        changes = _plan_repository_changes(
            active_db,
            manifest,
            refresh_order,
            requested_mode,
            start_revisions,
            plans,
            runtime_hash,
        )
    except Exception as exc:
        exc.refresh_stage = "delta_preflight"
        raise
    change_by_repo = {change.repo_key: change for change in changes}
    stage_modes: dict[str, dict[str, tuple[str, str]]] = {}
    endpoint_invalidated = False
    for repo_key in refresh_order:
        change = change_by_repo[repo_key]
        paths = _changed_input_paths(change)
        modes = stage_execution_modes(
            plans[repo_key],
            repository_mode=change.effective_mode,
            changed_paths=paths,
            matcher_overrides=repository_matcher_overrides(
                _manifest_repository(manifest, repo_key)
            ),
        )
        # A builder introduced after the last successful run is hydrated in
        # full even when the repository commit itself is unchanged. An
        # incomplete first attempt remains skipped until source changes (or an
        # explicit full), preventing automatic retry loops on bad evidence.
        missing = _missing_builder_hydrations(
            active_db, repo_key, plans[repo_key], change.target_commit_sha
        )
        if missing:
            for builder in missing:
                if builder in modes:
                    modes[builder] = ("full", "builder hydration required")
        stage_modes[repo_key] = modes
        if modes.get("rest_endpoints", ("skipped", ""))[0] != "skipped":
            endpoint_invalidated = True
    if endpoint_invalidated:
        for repo_key in refresh_order:
            entry = _manifest_repository(manifest, repo_key)
            if entry.get("profile") == "rest_automation":
                change = change_by_repo[repo_key]
                stage_modes[repo_key] = stage_execution_modes(
                    plans[repo_key],
                    repository_mode=(
                        "delta"
                        if change.effective_mode == "noop"
                        else change.effective_mode
                    ),
                    changed_paths=_changed_input_paths(change),
                    forced=("gherkin_coverage",),
                    matcher_overrides=repository_matcher_overrides(entry),
                )
                if change.effective_mode == "noop":
                    changed = replace(change, effective_mode="delta")
                    change_by_repo[repo_key] = changed

    if (
        LEGACY_REPO_KEY in refresh_order
        and stage_modes[LEGACY_REPO_KEY].get("rest_endpoints", ("skipped", ""))[0]
        != "skipped"
    ):
        for dependent in manifest["repositories"]:
            dependent_key = str(dependent["repo_key"])
            if (
                not dependent.get("enabled", True)
                or dependent.get("profile") != "rest_automation"
                or dependent_key in refresh_order
            ):
                continue
            if LEGACY_REPO_KEY not in _resolve_refresh_order(manifest, dependent_key):
                continue
            replacement_manifest = manifest_path or Path("config/workspace_repos.yaml")
            raise RefreshError(
                "main-only refresh would invalidate REST automation coverage before candidate creation; "
                "run exactly: PYTHONPATH=. ./.venv/bin/python -m scripts.refresh_workspace "
                f"--db {active_db} --manifest {replacement_manifest} "
                f"--repo {dependent_key} --mode {requested_mode}"
            )

    if attempt_token is not None:
        effective = sorted({change.effective_mode for change in changes})
        fallback = sorted(
            {str(change.fallback_reason) for change in changes if change.fallback_reason}
        )
        record_attempt_event(
            active_db,
            attempt_token,
            stage="planning",
            status="succeeded",
            detail="repository changes planned",
            fields={
                "closure_json": _stable_json(refresh_order),
                "target_revisions_json": _stable_json(start_revisions),
                "requested_effective_mode": requested_mode,
                "effective_mode": "hybrid" if len(effective) > 1 else (effective[0] if effective else None),
                "manifest_hash": _manifest_hash(manifest),
                "builder_plan_hash": _builder_plan_hash(plans, runtime_hash),
                "delta_contract_version": DELTA_CONTRACT_VERSION,
                "content_contract_version": CATALOG_CONTENT_VERSION,
                "runtime_contract_version": RUNTIME_CONTRACT_VERSION,
                "evidence_comparison_version": EVIDENCE_COMPARISON_VERSION,
                "fallback_reason": "; ".join(fallback) if fallback else None,
            },
        )
    if changes and all(change.is_noop for change in changes) and all(
        all(mode == "skipped" for mode, _reason in stage_modes[key].values())
        for key in refresh_order
    ):
        if attempt_token is not None:
            record_attempt_event(active_db, attempt_token, stage="no_op", detail="recording no-op metadata")
        try:
            _record_noop_attempts(
                active_db,
                manifest,
                changes,
                plans,
                parent=parent,
                start_revisions=start_revisions,
            )
        except Exception as exc:
            exc.refresh_stage = (
                "source_revision_final"
                if "revision changed while refresh" in str(exc)
                else "parent_cas"
                if "compare-and-swap" in str(exc)
                else "no_op"
            )
            raise
        return

    effective_modes = {change_by_repo[key].effective_mode for key in refresh_order}
    non_noop_modes = effective_modes - {"noop"}
    effective_catalog_mode = (
        "hybrid" if len(non_noop_modes) > 1 else next(iter(non_noop_modes), "delta")
    )
    build_token = str(uuid.uuid4())
    candidate = active_db.with_name(f"{active_db.name}.candidate.{build_token}")
    previous = active_db.with_name(active_db.name + ".previous")
    candidate_run_ids: list[int] = []
    run_ids_by_repo: dict[str, int] = {}
    outcomes_by_repo: dict[str, dict[str, BuilderOutcome]] = {}
    manifest_digest = _manifest_hash(manifest)
    plan_digest = _builder_plan_hash(plans, runtime_hash)
    failed_step = "backup_database"
    with ExitStack() as resources:
        snapshots: dict[str, SourceSnapshot] = {}
        git_roots: dict[str, Path] = {}
        if attempt_token is not None:
            record_attempt_event(active_db, attempt_token, stage="snapshot", detail="materializing target snapshots")
        for repo_key in refresh_order:
            entry = _manifest_repository(manifest, repo_key)
            git_root = Path(entry["local_root"]).expanduser().resolve()
            git_roots[repo_key] = git_root
            if any(
                mode != "skipped" for mode, _reason in stage_modes[repo_key].values()
            ):
                try:
                    snapshots[repo_key] = resources.enter_context(
                        materialize_source_snapshot(
                            repo_key,
                            git_root,
                            change_by_repo[repo_key].target_commit_sha,
                        )
                    )
                except Exception as exc:
                    exc.refresh_stage = "snapshot"
                    raise
        try:
            if attempt_token is not None:
                record_attempt_event(active_db, attempt_token, stage="candidate_build", detail="building candidate")
            _backup_database(active_db, candidate)
            _outputs_base = active_db.parent.parent / "outputs"
            _outputs_base.mkdir(parents=True, exist_ok=True)
            # Retain candidate-local raw diagnostics. They are linked from the
            # active error bundle if this candidate is rejected.
            output_root = Path(tempfile.mkdtemp(prefix=f"{build_token}.", dir=_outputs_base))
            legacy_entry = next(
                (
                    entry
                    for entry in manifest["repositories"]
                    if entry["repo_key"] == LEGACY_REPO_KEY
                ),
                None,
            )
            if legacy_entry is not None:
                failed_step = "migrate_multi_repo"
                migrate_multi_repo(
                    db_path=str(candidate),
                    local_root=str(legacy_entry["local_root"]),
                    tracked_branch=str(legacy_entry["tracked_branch"]),
                )
            else:
                migration_conn = sqlite3.connect(candidate)
                try:
                    apply_delta_refresh_migration(migration_conn)
                finally:
                    migration_conn.close()

            conn = sqlite3.connect(candidate)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            resources.callback(conn.close)
            failed_step = "register_manifest"
            register_manifest(conn, _closure_manifest(manifest, set(refresh_order)))
            for repo_key, snapshot in snapshots.items():
                conn.execute(
                    "UPDATE repos SET local_root=? WHERE repo_key=?",
                    (str(snapshot.snapshot_root), repo_key),
                )
            parent_row = conn.execute(
                "SELECT id FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            source_revisions = active_source_revisions(conn)
            source_revisions.update(start_revisions)
            build_id = int(
                conn.execute(
                    """INSERT INTO catalog_builds(
                           build_token,parent_catalog_build_id,catalog_path,
                           requested_mode,effective_mode,status,source_revisions_json,
                           manifest_hash,builder_plan_hash,delta_contract_version,
                           runtime_fingerprint,refresh_readiness,
                           content_contract_version,runtime_contract_version,
                           evidence_comparison_version
                       ) VALUES (?,?,?,?,?,'building',?,?,?,?,?,?,?,?,?)""",
                    (
                        build_token,
                        int(parent_row["id"]) if parent_row else None,
                        str(active_db),
                        requested_mode,
                        effective_catalog_mode,
                        _stable_json(source_revisions),
                        manifest_digest,
                        plan_digest,
                        DELTA_CONTRACT_VERSION,
                        runtime_hash,
                        "full_recovery_required",
                        CATALOG_CONTENT_VERSION,
                        RUNTIME_CONTRACT_VERSION,
                        EVIDENCE_COMPARISON_VERSION,
                    ),
                ).lastrowid
            )
            conn.commit()

            for repo_key in refresh_order:
                entry = _manifest_repository(manifest, repo_key)
                repo = get_repository(conn, repo_key)
                repo_id = int(repo["id"])
                change = change_by_repo[repo_key]
                run_id = _record_run(
                    conn,
                    repo_id,
                    str(repo["tracked_branch"]),
                    change.target_commit_sha,
                    plans[repo_key],
                    manifest_hash=_repository_manifest_hash(entry),
                    builder_plan_hash=_repository_plan_hash(
                        plans[repo_key], runtime_hash
                    ),
                    stage_modes=stage_modes[repo_key],
                    source_identity={
                        "canonical_git_url": canonical_git_url(entry.get("remote_url")) or "",
                        "tracked_branch": str(repo["tracked_branch"]),
                        "target_commit_sha": change.target_commit_sha,
                    },
                )
                candidate_run_ids.append(run_id)
                run_ids_by_repo[repo_key] = run_id
                outcomes_by_repo[repo_key] = {}
                change_set_id = _record_change_set(
                    conn,
                    catalog_build_id=build_id,
                    run_id=run_id,
                    repo_id=repo_id,
                    change=change,
                )
                conn.execute(
                    "UPDATE repo_change_sets SET status='running',started_at=CURRENT_TIMESTAMP WHERE id=?",
                    (change_set_id,),
                )
                conn.commit()

                old_paths = sorted(
                    {
                        path.old_path
                        for path in change.changed_paths
                        if path.old_path is not None
                    }
                )
                prior_ids: tuple[int, ...] = ()
                prior_names: tuple[str, ...] = ()
                if old_paths:
                    placeholders = ",".join("?" for _ in old_paths)
                    prior = conn.execute(
                        f"""SELECT s.id,s.name FROM symbols s JOIN files f ON f.id=s.file_id
                            WHERE f.repo_id=? AND f.path IN ({placeholders})""",
                        (repo_id, *old_paths),
                    ).fetchall()
                    prior_ids = tuple(int(row[0]) for row in prior)
                    prior_names = tuple(str(row[1]) for row in prior)
                delta_context: dict[str, object] = {
                    "changed_paths": change.changed_paths,
                    "prior_symbol_ids": prior_ids,
                    "prior_symbol_names": prior_names,
                }
                root = snapshots.get(repo_key)
                source_root = (
                    root.snapshot_root if root is not None else git_roots[repo_key]
                )
                for builder in plans[repo_key]:
                    failed_step = builder
                    execution_mode, reason = stage_modes[repo_key][builder]
                    if execution_mode == "skipped":
                        _stage(
                            conn,
                            run_id,
                            builder,
                            "skipped",
                            execution_mode="skipped",
                            reason=reason,
                            affected_count=0,
                        )
                        continue
                    _stage(
                        conn,
                        run_id,
                        builder,
                        "running",
                        execution_mode=execution_mode,
                        reason=reason,
                    )
                    try:
                        outcome = _run_builder(
                            builder,
                            repo_key,
                            repo_id,
                            source_root,
                            str(candidate),
                            entry,
                            git_root=git_roots[repo_key],
                            output_root=output_root / repo_key,
                            execution_mode=execution_mode,
                            delta_context=delta_context,
                            coverage_build_context={
                                "build_token": build_token,
                                "target_sha": change_by_repo[repo_key].target_commit_sha,
                                "dependency_revisions": {
                                    key: change_by_repo[key].target_commit_sha
                                    for key in sorted(change_by_repo)
                                },
                            }
                            if builder == "gherkin_coverage"
                            else None,
                        )
                    except Exception as exc:
                        _stage(
                            conn,
                            run_id,
                            builder,
                            "failed",
                            str(exc),
                            execution_mode=execution_mode,
                            reason=reason,
                        )
                        conn.execute(
                            "UPDATE repo_change_sets SET status='failed',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                            (change_set_id,),
                        )
                        conn.commit()
                        raise
                    outcomes_by_repo[repo_key][builder] = outcome
                    _stage(
                        conn,
                        run_id,
                        builder,
                        "succeeded",
                        execution_mode=execution_mode,
                        reason=reason,
                        outcome=outcome,
                    )

                failed_step = "candidate_source_verification"
                if repo_key in snapshots:
                    _verify_candidate_sources(
                        conn, repo_id, snapshots[repo_key].snapshot_root
                    )
                failed_step = "repository_candidate_validation"
                _validate_candidate(conn, repo_id)
                conn.execute(
                    "UPDATE repo_change_sets SET status='succeeded',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (change_set_id,),
                )
                conn.execute(
                    "UPDATE repo_index_runs SET status='validated',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (run_id,),
                )
                conn.execute(
                    """UPDATE repos SET indexed_commit_sha=?,last_scanned_at=CURRENT_TIMESTAMP,
                           last_built_at=CURRENT_TIMESTAMP,index_status='active',diagnostic_error=NULL,
                           last_attempt_status='active',last_attempted_at=CURRENT_TIMESTAMP,
                           last_attempt_error=NULL WHERE id=?""",
                    (change.target_commit_sha, repo_id),
                )
                conn.commit()

            failed_step = "integrity_check"
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise RefreshError(f"candidate integrity_check failed: {integrity}")
            failed_step = "foreign_key_check"
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RefreshError(
                    f"candidate foreign key violations: {violations[:3]}"
                )
            failed_step = "migration_025_validation"
            if not conn.execute(
                "SELECT 1 FROM schema_migrations WHERE name='025_delta_refresh_hardening'"
            ).fetchone():
                raise RefreshError("migration 025 is absent from candidate")
            if conn.execute("SELECT COUNT(*) FROM integration_links").fetchone()[0]:
                raise RefreshError(
                    "unsupported integration-link rows remain in candidate"
                )
            failed_step = "migration_028_validation"
            if not conn.execute(
                "SELECT 1 FROM schema_migrations WHERE name='028_api_registry'"
            ).fetchone():
                raise RefreshError("migration 028 is absent from candidate")
            missing_registry_tables = [
                table
                for table in (
                    "api_registry_entries",
                    "api_registry_entry_links",
                    "api_registry_issues",
                    "ui_source_diagnostics",
                )
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
            ]
            if missing_registry_tables:
                raise RefreshError(
                    "migration 028 tables are absent from candidate: "
                    + ", ".join(missing_registry_tables)
                )

            # Counts and structured diagnostics are durable audit output, not
            # a promotion gate.  Source/provenance and SQLite integrity gates
            # above remain mandatory.
            candidate_repositories: list[dict[str, object]] = []
            for repo_key in refresh_order:
                repo = get_repository(conn, repo_key)
                counts = collect_repository_counts(conn, int(repo["id"]))
                diagnostics = _quality_diagnostics(outcomes_by_repo[repo_key])
                candidate_repositories.append(
                    {
                        "repo_key": repo_key,
                        "commit_sha": change_by_repo[repo_key].target_commit_sha,
                        "manifest_hash": _repository_manifest_hash(
                            _manifest_repository(manifest, repo_key)
                        ),
                        "builder_plan_hash": _repository_plan_hash(
                            plans[repo_key], runtime_hash
                        ),
                        "diagnostics": diagnostics,
                        "counts": counts,
                    }
                )

            for repository in candidate_repositories:
                repo_key = str(repository["repo_key"])
                conn.execute(
                    "UPDATE repo_index_runs SET validation_summary=? WHERE id=?",
                    (_stable_json({"kind": "audit", "counts": repository["counts"],
                                   "diagnostics": repository["diagnostics"]}),
                     run_ids_by_repo[repo_key]),
                )

            blockers_by_repo = {
                repo_key: _semantic_blockers(outcomes_by_repo[repo_key])
                for repo_key in refresh_order
            }
            blockers_by_repo = {
                repo_key: diagnostics
                for repo_key, diagnostics in blockers_by_repo.items() if diagnostics
            }
            if blockers_by_repo:
                error = RefreshError(
                    "semantic diagnostics rejected candidate: "
                    + "; ".join(
                        f"{repo_key}:{item['code']}"
                        for repo_key, diagnostics in sorted(blockers_by_repo.items())
                        for item in diagnostics
                    )
                )
                error.refresh_stage = "semantic_validation"
                error.error_bundle_by_repo = blockers_by_repo
                error.error_bundle_target_shas = {
                    repo_key: change_by_repo[repo_key].target_commit_sha
                    for repo_key in blockers_by_repo
                }
                error.error_output_root = output_root
                raise error

            failed_step = "restore_manifest_roots"
            register_manifest(conn, _closure_manifest(manifest, set(refresh_order)))
            failed_step = "logical_fingerprint"
            fingerprint = logical_content_fingerprint(conn)
            conn.execute(
                """UPDATE catalog_builds SET status='validated',content_fingerprint=?,
                   completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                (fingerprint, build_id),
            )
            failed_step = "source_revision_final"
            _recheck_source_revisions(manifest, start_revisions)
            failed_step = "parent_cas"
            _assert_parent_unchanged(active_db, parent)
            conn.execute(
                "UPDATE catalog_builds SET status='previous' WHERE status='active' AND id<>?",
                (build_id,),
            )
            conn.execute(
                "UPDATE catalog_builds SET status='active',refresh_readiness='ready' WHERE id=?", (build_id,)
            )
            run_placeholders = ",".join("?" for _ in candidate_run_ids)
            conn.execute(
                f"UPDATE repo_index_runs SET status='active' "
                f"WHERE status='validated' AND id IN ({run_placeholders})",
                candidate_run_ids,
            )
            conn.execute(
                "UPDATE graph_builds SET status='previous' WHERE status='active'"
            )
            for repo_key in refresh_order:
                repo = get_repository(conn, repo_key)
                repo_id = int(repo["id"])
                stale_records = _record_stale_parser_evidence(
                    conn, active_db, repo_id, change_by_repo[repo_key].target_commit_sha,
                    change_by_repo[repo_key], outcomes_by_repo[repo_key],
                )
                errors = [
                    diagnostic.to_dict()
                    for outcome in outcomes_by_repo[repo_key].values()
                    for diagnostic in outcome.diagnostics
                    if diagnostic.severity == "error"
                ]
                if errors:
                    _write_error_bundle(
                        conn, repo_id, change_by_repo[repo_key].target_commit_sha,
                        errors, output_root=output_root,
                    )
                else:
                    conn.execute("DELETE FROM repo_error_bundles WHERE repo_id=?", (repo_id,))
                for builder, (execution_mode, _reason) in stage_modes[repo_key].items():
                    outcome = outcomes_by_repo[repo_key].get(builder)
                    if outcome is None:
                        continue
                    coverage = "incomplete" if any(
                        d.severity == "error" for d in outcome.diagnostics
                    ) else "complete"
                    conn.execute(
                        """INSERT INTO repo_builder_hydrations(
                               repo_id,builder_name,catalog_build_id,repo_index_run_id,coverage_status,
                               last_full_commit_sha,last_attempt_commit_sha)
                           VALUES (?,?,?,?,?,?,?) ON CONFLICT(repo_id,builder_name) DO UPDATE SET
                               catalog_build_id=excluded.catalog_build_id,repo_index_run_id=excluded.repo_index_run_id,
                               coverage_status=excluded.coverage_status,
                               last_full_commit_sha=CASE WHEN excluded.last_full_commit_sha IS NULL
                                   THEN repo_builder_hydrations.last_full_commit_sha ELSE excluded.last_full_commit_sha END,
                               last_attempt_commit_sha=excluded.last_attempt_commit_sha,updated_at=CURRENT_TIMESTAMP""",
                        (repo_id, builder, build_id, run_ids_by_repo[repo_key], coverage,
                         change_by_repo[repo_key].target_commit_sha if execution_mode == "full" else None,
                         change_by_repo[repo_key].target_commit_sha),
                    )
                conn.execute(
                    """INSERT INTO repo_evidence_fingerprints(
                           repo_id,catalog_build_id,evidence_fingerprint,stale_state_json)
                       VALUES (?,?,?,?) ON CONFLICT(repo_id) DO UPDATE SET
                       catalog_build_id=excluded.catalog_build_id,evidence_fingerprint=excluded.evidence_fingerprint,
                       stale_state_json=excluded.stale_state_json,
                       updated_at=CURRENT_TIMESTAMP""",
                    (repo_id, build_id, repository_evidence_fingerprint(conn, repo_id),
                     _stable_json(stale_records)),
                )
            validation_summary = validate_catalog_connection(
                conn, expected_catalog_build_id=build_id,
            )
            validation_summary["source_verified"] = True
            validation_summary["parent_cas"] = "verified"
            validation_summary["diagnostics"] = candidate_repositories
            conn.execute(
                "UPDATE catalog_builds SET validation_summary=? WHERE id=?",
                (_stable_json(validation_summary), build_id),
            )
            conn.commit()
            conn.close()

            failed_step = "source_revision_promotion"
            _recheck_source_revisions(manifest, start_revisions)
            failed_step = "parent_cas_promotion"
            _assert_parent_unchanged(active_db, parent)
            failed_step = "promote_candidate"
            _promote_catalog_candidate(active_db, candidate, previous, build_token)
        except Exception as exc:
            candidate.unlink(missing_ok=True)
            exc.refresh_stage = failed_step
            raise
        finally:
            candidate.unlink(missing_ok=True)


def refresh_repository(
    db_path: str | Path,
    manifest_path: str | Path,
    repo_key: str,
    mode: str = "auto",
) -> None:
    active = Path(db_path).resolve()
    if not active.is_file():
        raise RefreshError(f"catalog database does not exist: {active}")
    manifest_file = Path(manifest_path).expanduser().resolve()
    with _refresh_lock(active):
        # The independent ledger is deliberately available before catalog
        # migrations or manifest parsing.  A bootstrap failure is non-durable;
        # after it commits every lifecycle boundary is auditable.
        bootstrap_refresh_ledger(active)
        attempt_token = create_refresh_attempt(active, repo_key=repo_key, requested_mode=mode)
        record_attempt_event(active, attempt_token, stage="migration_bootstrap", status="succeeded", detail="independent ledger ready")
        migration_conn = sqlite3.connect(active)
        try:
            apply_delta_refresh_migration(migration_conn)
            record_attempt_event(active, attempt_token, stage="migration_apply", status="succeeded", detail="catalog migrations applied")
        except Exception as exc:
            exc.refresh_stage = "migration_apply"
            raise
        finally:
            migration_conn.close()
        manifest: dict | None = None
        failed_step = "load_workspace_manifest"
        try:
            manifest = load_workspace_manifest(manifest_file)
            failed_step = "dependency_preflight"
            refresh_order = _resolve_refresh_order(manifest, repo_key)
            start_revisions = _validate_refresh_preconditions(manifest, refresh_order, active)
            record_attempt_event(
                active,
                attempt_token,
                stage="admission",
                status="succeeded",
                detail="manifest and source preflight accepted",
                fields={
                    "closure_json": _stable_json(refresh_order),
                    "target_revisions_json": (
                        _stable_json(start_revisions)
                        if isinstance(start_revisions, dict)
                        else None
                    ),
                },
            )
        except Exception as exc:
            if isinstance(exc, RepositoryArchivedError) and "GitHub reports" in str(exc):
                _persist_github_archive(
                    active, str(getattr(exc, "repository_key", repo_key)), str(exc)
                )
            record_attempt_failure(active, attempt_token, stage=failed_step, error=exc)
            _record_failed_refresh(
                active, manifest, repo_key, exc, failed_step, requested_mode=mode
            )
            raise
        try:
            _refresh_repository_closure(
                active,
                manifest,
                refresh_order,
                mode,
                start_revisions=start_revisions,
                manifest_path=manifest_file,
                attempt_token=attempt_token,
            )
            readiness_conn = sqlite3.connect(active)
            try:
                readiness_row = readiness_conn.execute(
                    "SELECT refresh_readiness FROM catalog_builds WHERE status='active' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                readiness_after = str(readiness_row[0]) if readiness_row else "full_recovery_required"
            finally:
                readiness_conn.close()
            record_attempt_event(
                active,
                attempt_token,
                stage="terminal",
                status="succeeded",
                detail="refresh succeeded",
                fields={"status": "succeeded", "readiness_after": readiness_after},
            )
        except Exception as exc:
            # The stage is retained by the coordinator for precise fault
            # diagnosis; terminal ledger writes are never best-effort.
            stage = str(getattr(exc, "refresh_stage", "refresh"))
            record_attempt_failure(
                active,
                attempt_token,
                stage=stage,
                error=exc,
            )
            _record_failed_refresh(
                active, manifest, repo_key, exc, stage, requested_mode=mode
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh a repository through a validated SQLite candidate"
    )
    parser.add_argument("--db", default="catalog/catalog.db")
    parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mode", choices=("auto", "full", "delta"), default="auto")
    args = parser.parse_args()
    refresh_repository(
        args.db,
        args.manifest,
        args.repo,
        mode=args.mode,
    )


if __name__ == "__main__":
    main()
