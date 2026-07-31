#!/usr/bin/env python3
"""Safely refresh one registered repository through a SQLite candidate.

The active catalog is never modified until all selected builders succeed and
the checked-out source revision is still the one that was validated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.db import migrate_multi_repo
from catalog.delta import (
    DELTA_CONTRACT_VERSION,
    ChangeType,
    DeltaUnavailable,
    RepositoryChangeSet,
    collect_repository_change_set,
)
from catalog.migrations import LEGACY_REPO_KEY
from catalog.repositories import (
    get_repository,
    load_workspace_manifest,
    register_manifest,
    resolve_repository_root,
    rest_automation_paths,
)
from scripts.builder_registry import build_plan, stage_execution_modes
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


def source_revision(root: Path, tracked_branch: str) -> str:
    """Validate a clean checkout and return the configured branch revision."""
    if _git(root, "status", "--porcelain"):
        raise RefreshError(f"repository checkout is dirty: {root}")
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


def _builder_plan_hash(plans: dict[str, list[str]]) -> str:
    return hashlib.sha256(_stable_json(plans).encode()).hexdigest()


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
        "depends_on": entry.get("depends_on"),
        "rest_automation": entry.get("rest_automation"),
    }
    return hashlib.sha256(_stable_json(contract).encode()).hexdigest()


def _repository_plan_hash(plan: list[str]) -> str:
    return hashlib.sha256(_stable_json(plan).encode()).hexdigest()


def _fingerprint(path: Path) -> str:
    """Legacy physical fingerprint retained for the compatibility runner."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_database(source: Path, target: Path) -> None:
    source_conn = sqlite3.connect(source)
    source_conn.execute("PRAGMA foreign_keys = ON")
    try:
        target_conn = sqlite3.connect(target)
        target_conn.execute("PRAGMA foreign_keys = ON")
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


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
    commit: bool = True,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO repo_index_runs(repo_id, tracked_branch, commit_sha, builder_plan_hash, status)
        VALUES (?, ?, ?, ?, 'building')
        """,
        (
            repo_id,
            branch,
            sha,
            builder_plan_hash or hashlib.sha256(json.dumps(plan).encode()).hexdigest(),
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
                   affected_record_count=?,record_count=?
               WHERE run_id=? AND builder_name=?""",
            (
                status,
                timestamp,
                error,
                execution_mode,
                reason,
                affected_count,
                affected_count,
                run_id,
                builder,
            ),
        )
    if commit:
        conn.commit()


def _run_builder(
    builder: str,
    repo_key: str,
    repo_id: int,
    root: Path,
    candidate_db: str,
    manifest_entry: dict,
    *,
    execution_mode: str = "full",
    delta_context: dict[str, object] | None = None,
) -> object:
    delta_context = delta_context if delta_context is not None else {}
    if builder == "scan":
        if execution_mode == "delta":
            from parser.scan_repo import apply_changed_paths

            result = apply_changed_paths(
                delta_context.get("changed_paths", ()),
                repo_key=repo_key,
                db_path=candidate_db,
            )
            delta_context["scan_result"] = result
            return result.affected_count
        from parser.scan_repo import scan

        return scan(repo_key=repo_key, db_path=candidate_db)
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
            write_logs=False,
            file_ids=file_ids,
        )
        delta_context["symbol_summary"] = summary
        return summary.affected_count
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
        return extract_all(
            only_changed=False,
            repo_key=repo_key,
            db_path=candidate_db,
            reset=reset,
            file_ids=file_ids,
        )
    elif builder == "integration_links":
        # Resolver extractors are additive and intentionally absent until a
        # repository declares a concrete, deterministic contract extractor.
        return 0
    elif builder == "entities":
        from scripts import scan_ent_files
        from scripts.build_entities import build

        entities_path = Path(candidate_db).with_name(
            f"{Path(candidate_db).name}.{repo_key}.entities.jsonl"
        )
        try:
            scan_ent_files.scan(root, entities_path)
            return build(candidate_db, entities_path, reset=True, repo_key=repo_key)
        finally:
            entities_path.unlink(missing_ok=True)
    elif builder == "entity_roots":
        from catalog.db import get_connection
        from scripts.build_entity_roots import build_entity_roots

        conn = get_connection(candidate_db)
        try:
            return build_entity_roots(conn, reset=True, repo_id=repo_id)
        finally:
            conn.close()
    elif builder == "openapi_scan":
        from catalog.db import get_connection
        from scripts.scan_openapispec import scan_openapispec

        conn = get_connection(candidate_db)
        try:
            result = scan_openapispec(conn, root, repo_id)
            conn.commit()
            return result
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
            result = _link_openapispec(conn, root, repo_id, None)
            conn.commit()
            return result
        finally:
            conn.close()
    elif builder == "workflows":
        from scripts.build_workflows import build

        return build(candidate_db, root, repo_id, reset=True)
    elif builder == "security":
        from scripts.build_security_mappings import build

        return build(
            candidate_db,
            repo_key=repo_key,
            reset=True,
            max_parse_failures=-1,
            max_unresolved=-1,
        )
    elif builder == "rest_endpoints":
        from scripts.build_rest_endpoints import build

        return build(candidate_db, root, repo_id, reset=True)
    elif builder == "entity_semantics":
        from scripts.build_entity_semantics import build

        return build(candidate_db, root, repo_key, reset=True)
    elif builder == "entity_access_links":
        from scripts.build_entity_access_links import build

        return build(candidate_db, reset=True, repo_key=repo_key)
    elif builder == "gherkin_coverage":
        from catalog.db import get_connection
        from scripts.build_gherkin_coverage import build

        features_root, object_mapping = rest_automation_paths(manifest_entry, root)
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
            return build(
                conn,
                repo_key=repo_key,
                suite_root=root,
                object_mapping_path=object_mapping,
                features_root=features_root,
            )
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
    """Best-effort diagnostic history without replacing the active catalog."""
    try:
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
                revisions = {
                    str(row[0]): row[1]
                    for row in conn.execute(
                        "SELECT repo_key,indexed_commit_sha FROM repos ORDER BY repo_key"
                    )
                }
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
    except Exception:
        # The candidate failure remains the primary error.  In particular, do
        # not mask it if the active catalog has not yet been migrated.
        return


def _refresh_repository_once(
    active: Path,
    manifest: dict,
    repo_key: str,
) -> None:
    manifest_entry: dict | None = None
    legacy_entry: dict | None = None
    candidate = active.with_name(f"{active.name}.candidate.{uuid.uuid4().hex}")
    previous = active.with_name(f"{active.name}.previous")
    failed_step: str | None = "manifest_repository"

    try:
        manifest_entry = _manifest_repository(manifest, repo_key)
        failed_step = "backup_database"
        legacy_entry = next(
            (
                entry
                for entry in manifest["repositories"]
                if entry["repo_key"] == LEGACY_REPO_KEY
            ),
            None,
        )
        _backup_database(active, candidate)
        failed_step = "open_candidate"
        conn = sqlite3.connect(candidate)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        try:
            if legacy_entry is not None:
                failed_step = "migrate_multi_repo"
                migrate_multi_repo(
                    db_path=str(candidate),
                    local_root=str(legacy_entry["local_root"]),
                    tracked_branch=str(legacy_entry["tracked_branch"]),
                )
            failed_step = "register_manifest"
            register_manifest(conn, manifest)
            conn.commit()
            repo = get_repository(conn, repo_key)
            if not repo["enabled"]:
                raise RefreshError(f"repository is disabled: {repo_key}")
            failed_step = "resolve_repository_root"
            root = resolve_repository_root(conn, repo_key)
            branch = str(repo["tracked_branch"])
            failed_step = "source_revision"
            start_sha = source_revision(root, branch)
            failed_step = "build_plan"
            plan = build_plan(
                str(repo["profile"] or "generic"),
                json.loads(repo["effective_builders_json"] or "[]"),
            )
            conn.execute(
                "UPDATE repos SET effective_builders_json=? WHERE id=?",
                (json.dumps(plan, separators=(",", ":")), int(repo["id"])),
            )
            conn.commit()
            run_id = _record_run(conn, int(repo["id"]), branch, start_sha, plan)

            for builder in plan:
                failed_step = builder
                _stage(conn, run_id, builder, "running")
                try:
                    _run_builder(
                        builder,
                        repo_key,
                        int(repo["id"]),
                        root,
                        str(candidate),
                        manifest_entry,
                    )
                except Exception as exc:
                    _stage(conn, run_id, builder, "failed", str(exc))
                    raise
                _stage(conn, run_id, builder, "succeeded")

            # Re-open because individual builders own and close their own connections.
            failed_step = "reopen_candidate"
            conn.close()
            conn = sqlite3.connect(candidate)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            failed_step = "validate_candidate"
            _validate_candidate(conn, int(repo["id"]))
            failed_step = "source_revision_postbuild"
            if source_revision(root, branch) != start_sha:
                raise RefreshError(
                    "repository revision changed while candidate was built"
                )
            fingerprint = _fingerprint(candidate)
            conn.execute(
                "UPDATE repo_index_runs SET status='validated', catalog_fingerprint=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (fingerprint, run_id),
            )
            conn.execute(
                """UPDATE repos SET
                       indexed_commit_sha=?, last_scanned_at=CURRENT_TIMESTAMP,
                       last_built_at=CURRENT_TIMESTAMP, index_status='active',
                       diagnostic_error=NULL, last_attempt_status='active',
                       last_attempted_at=CURRENT_TIMESTAMP, last_attempt_error=NULL
                   WHERE id=?""",
                (start_sha, int(repo["id"])),
            )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_builds'"
            ).fetchone():
                conn.execute(
                    "UPDATE graph_builds SET status='previous' WHERE status='active'"
                )
            conn.execute(
                "UPDATE repo_index_runs SET status='active' WHERE id=?", (run_id,)
            )
            conn.commit()
        finally:
            conn.close()

        shutil.copy2(active, previous)
        os.replace(candidate, active)
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        _record_failed_refresh(active, manifest, repo_key, exc, failed_step)
        raise


def _resolve_refresh_order(manifest: dict, repo_key: str) -> list[str]:
    """Return a validated, dependency-first refresh order."""
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
    manifest: dict, refresh_order: list[str]
) -> dict[str, str]:
    """Validate every checkout before any repository candidate is built."""
    revisions: dict[str, str] = {}
    for repo_key in refresh_order:
        entry = _manifest_repository(manifest, repo_key)
        root = Path(entry["local_root"]).expanduser()
        if not root.is_dir():
            raise RefreshError(
                f"repository {repo_key} checkout root does not exist: {root}"
            )
        resolved_root = root.resolve()
        revisions[repo_key] = source_revision(
            resolved_root, str(entry["tracked_branch"])
        )
        if entry.get("profile") == "rest_automation":
            rest_automation_paths(entry, resolved_root)
    return revisions


def _result_count(result: object) -> int:
    if result is None:
        return 0
    if isinstance(result, bool):
        return int(result)
    if isinstance(result, int):
        return max(0, result)
    if isinstance(result, dict):
        return sum(
            int(value)
            for value in result.values()
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        )
    affected = getattr(result, "affected_count", None)
    return int(affected) if isinstance(affected, int) else 0


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


def _indexed_revisions(active: Path) -> dict[str, str | None]:
    conn = sqlite3.connect(active)
    conn.row_factory = sqlite3.Row
    try:
        return {
            str(row[0]): row[1]
            for row in conn.execute(
                "SELECT repo_key,indexed_commit_sha FROM repos ORDER BY repo_key"
            )
        }
    finally:
        conn.close()


def _active_catalog_contract(
    active: Path,
    repo_keys: list[str],
) -> tuple[sqlite3.Row | None, dict[str, str | None], dict[str, sqlite3.Row]]:
    conn = sqlite3.connect(active)
    conn.row_factory = sqlite3.Row
    try:
        indexed_revisions = {
            str(row[0]): row[1]
            for row in conn.execute(
                "SELECT repo_key,indexed_commit_sha FROM repos ORDER BY repo_key"
            )
        }
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
) -> list[RepositoryChangeSet]:
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
                try:
                    contract_version = int(active_build["delta_contract_version"])
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    global_reason = f"compatibility metadata unavailable: {exc}"
                else:
                    if contract_version != DELTA_CONTRACT_VERSION:
                        global_reason = "delta-contract version mismatch"
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
                if (
                    contract is None
                    or not contract["manifest_hash"]
                    or not contract["builder_plan_hash"]
                ):
                    compatibility_reason = "compatibility metadata unavailable"
                elif contract["manifest_hash"] != _repository_manifest_hash(entry):
                    compatibility_reason = "repository manifest incompatibility"
                elif contract["builder_plan_hash"] != _repository_plan_hash(
                    plans[repo_key]
                ):
                    compatibility_reason = "repository builder-plan incompatibility"
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
               change_set_id,change_type,old_path,new_path,old_blob_sha,new_blob_sha
           ) VALUES (?,?,?,?,?,?)""",
        [
            (
                change_set_id,
                path.change_type.value,
                path.old_path,
                path.new_path,
                path.old_blob_sha,
                path.new_blob_sha,
            )
            for path in change.changed_paths
        ],
    )
    return change_set_id


def _preserve_previous_database(active: Path, previous: Path, token: str) -> None:
    temporary = previous.with_name(f"{previous.name}.tmp.{token}")
    temporary.unlink(missing_ok=True)
    try:
        _backup_database(active, temporary)
        os.replace(temporary, previous)
    finally:
        temporary.unlink(missing_ok=True)


def _promote_catalog_candidate(
    active: Path, candidate: Path, previous: Path, token: str
) -> None:
    """Promote both retained generations, rolling back either-path failures."""

    previous_stage = previous.with_name(f"{previous.name}.stage.{token}")
    previous_backup = previous.with_name(f"{previous.name}.backup.{token}")
    previous_stage.unlink(missing_ok=True)
    previous_backup.unlink(missing_ok=True)
    promoted = False
    try:
        _backup_database(active, previous_stage)
        if previous.exists():
            _backup_database(previous, previous_backup)
        os.replace(candidate, active)
        promoted = True
        os.replace(previous_stage, previous)
    except Exception:
        if promoted and previous_stage.exists():
            os.replace(previous_stage, active)
        if previous_backup.exists():
            os.replace(previous_backup, previous)
        raise
    finally:
        previous_stage.unlink(missing_ok=True)
        previous_backup.unlink(missing_ok=True)


def _record_noop_attempts(
    active: Path,
    manifest: dict,
    changes: list[RepositoryChangeSet],
    plans: dict[str, list[str]],
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
                    affected_count=0,
                    commit=False,
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
) -> None:
    """Refresh one dependency closure through one candidate and one promotion."""

    if requested_mode not in {"auto", "full", "delta"}:
        raise RefreshError(f"unsupported refresh mode: {requested_mode}")
    start_revisions = start_revisions or _validate_refresh_preconditions(
        manifest, refresh_order
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
        )
    except Exception as exc:
        _record_failed_refresh(
            active_db,
            manifest,
            refresh_order[-1] if refresh_order else "unknown",
            exc,
            "delta_preflight",
            requested_mode=requested_mode,
        )
        raise
    if changes and all(change.is_noop for change in changes):
        _record_noop_attempts(active_db, manifest, changes, plans)
        return

    change_by_repo = {change.repo_key: change for change in changes}
    stage_modes: dict[str, dict[str, tuple[str, str]]] = {}
    endpoint_invalidated = False
    for repo_key in refresh_order:
        change = change_by_repo[repo_key]
        paths = _changed_input_paths(change)
        modes = stage_execution_modes(
            plans[repo_key], repository_mode=change.effective_mode, changed_paths=paths
        )
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
                )
                if change.effective_mode == "noop":
                    changed = replace(change, effective_mode="delta")
                    change_by_repo[repo_key] = changed

    effective_modes = {change_by_repo[key].effective_mode for key in refresh_order}
    non_noop_modes = effective_modes - {"noop"}
    effective_catalog_mode = (
        "hybrid" if len(non_noop_modes) > 1 else next(iter(non_noop_modes), "delta")
    )
    build_token = str(uuid.uuid4())
    candidate = active_db.with_name(f"{active_db.name}.candidate.{build_token}")
    previous = active_db.with_name(active_db.name + ".previous")
    candidate_run_ids: list[int] = []
    manifest_digest = _manifest_hash(manifest)
    plan_digest = _builder_plan_hash(plans)
    failed_repo = refresh_order[0] if refresh_order else "unknown"
    failed_step = "backup_database"
    try:
        _backup_database(active_db, candidate)
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
            from catalog.migrations import apply_delta_refresh_migration

            migration_conn = sqlite3.connect(candidate)
            try:
                apply_delta_refresh_migration(migration_conn)
            finally:
                migration_conn.close()

        conn = sqlite3.connect(candidate)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            failed_step = "register_manifest"
            register_manifest(conn, manifest)
            parent = conn.execute(
                "SELECT id FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            source_revisions = {
                str(row[0]): row[1]
                for row in conn.execute(
                    "SELECT repo_key,indexed_commit_sha FROM repos ORDER BY repo_key"
                )
            }
            source_revisions.update(start_revisions)
            build_id = int(
                conn.execute(
                    """INSERT INTO catalog_builds(
                           build_token,parent_catalog_build_id,catalog_path,
                           requested_mode,effective_mode,status,source_revisions_json,
                           manifest_hash,builder_plan_hash,delta_contract_version
                       ) VALUES (?,?,?,?,?,'building',?,?,?,?)""",
                    (
                        build_token,
                        int(parent["id"]) if parent else None,
                        str(active_db),
                        requested_mode,
                        effective_catalog_mode,
                        _stable_json(source_revisions),
                        manifest_digest,
                        plan_digest,
                        DELTA_CONTRACT_VERSION,
                    ),
                ).lastrowid
            )
            conn.commit()

            for repo_key in refresh_order:
                failed_repo = repo_key
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
                    builder_plan_hash=_repository_plan_hash(plans[repo_key]),
                    stage_modes=stage_modes[repo_key],
                )
                candidate_run_ids.append(run_id)
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
                root = Path(entry["local_root"]).expanduser().resolve()
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
                        result = _run_builder(
                            builder,
                            repo_key,
                            repo_id,
                            root,
                            str(candidate),
                            entry,
                            execution_mode=execution_mode,
                            delta_context=delta_context,
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
                    _stage(
                        conn,
                        run_id,
                        builder,
                        "succeeded",
                        execution_mode=execution_mode,
                        reason=reason,
                        affected_count=_result_count(result),
                    )

                failed_step = "validate_candidate"
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

            failed_step = "source_revision_postbuild"
            for repo_key in refresh_order:
                entry = _manifest_repository(manifest, repo_key)
                root = Path(entry["local_root"]).expanduser().resolve()
                if (
                    source_revision(root, str(entry["tracked_branch"]))
                    != start_revisions[repo_key]
                ):
                    failed_repo = repo_key
                    raise RefreshError(
                        f"repository revision changed while closure candidate was built: {repo_key}"
                    )
            failed_step = "global_validation"
            fingerprint = logical_content_fingerprint(conn)
            conn.execute(
                """UPDATE catalog_builds SET status='validated',content_fingerprint=?,
                       completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                (fingerprint, build_id),
            )
            conn.execute(
                "UPDATE catalog_builds SET status='previous' WHERE status='active' AND id<>?",
                (build_id,),
            )
            conn.execute(
                "UPDATE catalog_builds SET status='active' WHERE id=?", (build_id,)
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
            validation_summary = validate_catalog_connection(
                conn, expected_catalog_build_id=build_id
            )
            conn.execute(
                "UPDATE catalog_builds SET validation_summary=? WHERE id=?",
                (_stable_json(validation_summary), build_id),
            )
            conn.commit()
        finally:
            conn.close()

        failed_step = "promote_candidate"
        _promote_catalog_candidate(active_db, candidate, previous, build_token)
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        _record_failed_refresh(
            active_db,
            manifest,
            failed_repo,
            exc,
            failed_step,
            requested_mode=requested_mode,
            effective_mode=effective_catalog_mode,
        )
        raise


def refresh_repository(
    db_path: str | Path,
    manifest_path: str | Path,
    repo_key: str,
    mode: str = "auto",
) -> None:
    active = Path(db_path).resolve()
    if not active.is_file():
        raise RefreshError(f"catalog database does not exist: {active}")
    manifest: dict | None = None
    failed_step = "load_workspace_manifest"
    try:
        manifest = load_workspace_manifest(manifest_path)
        failed_step = "dependency_preflight"
        refresh_order = _resolve_refresh_order(manifest, repo_key)
        start_revisions = _validate_refresh_preconditions(manifest, refresh_order)
    except Exception as exc:
        _record_failed_refresh(
            active,
            manifest,
            repo_key,
            exc,
            failed_step,
            requested_mode=mode,
        )
        raise

    # Compatibility for older tests/extensions that replaced the preflight and
    # per-repository hook.  Real preflight always returns the revision mapping.
    if not isinstance(start_revisions, dict):
        for refresh_repo_key in refresh_order:
            _refresh_repository_once(active, manifest, refresh_repo_key)
        return

    _refresh_repository_closure(
        active,
        manifest,
        refresh_order,
        mode,
        start_revisions=start_revisions,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh a repository through a validated SQLite candidate"
    )
    parser.add_argument("--db", default="catalog/catalog.db")
    parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mode", choices=("auto", "full", "delta"), default="auto")
    args = parser.parse_args()
    refresh_repository(args.db, args.manifest, args.repo, mode=args.mode)


if __name__ == "__main__":
    main()
