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
from datetime import datetime, timezone
from pathlib import Path

from catalog.repositories import (
    get_repository,
    load_workspace_manifest,
    register_manifest,
    rest_automation_paths,
    resolve_repository_root,
)
from catalog.db import migrate_multi_repo
from scripts.builder_registry import build_plan


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
    branch_sha = _git(root, "rev-parse", "--verify", tracked_branch)
    if head != branch_sha:
        raise RefreshError(
            f"HEAD {head} does not match configured branch {tracked_branch} ({branch_sha})"
        )
    return head


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_database(source: Path, target: Path) -> None:
    source_conn = sqlite3.connect(source)
    try:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def _manifest_repository(manifest: dict, repo_key: str) -> dict:
    entry = next((item for item in manifest["repositories"] if item["repo_key"] == repo_key), None)
    if entry is None:
        raise RefreshError(f"repository not found in manifest: {repo_key}")
    return entry


def _record_run(
    conn: sqlite3.Connection, repo_id: int, branch: str, sha: str, plan: list[str]
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO repo_index_runs(repo_id, tracked_branch, commit_sha, builder_plan_hash, status)
        VALUES (?, ?, ?, ?, 'building')
        """,
        (repo_id, branch, sha, hashlib.sha256(json.dumps(plan).encode()).hexdigest()),
    )
    run_id = int(cursor.lastrowid)
    conn.executemany(
        "INSERT INTO repo_index_stages(run_id, builder_name, status) VALUES (?, ?, 'pending')",
        [(run_id, builder) for builder in plan],
    )
    conn.commit()
    return run_id


def _stage(conn: sqlite3.Connection, run_id: int, builder: str, status: str, error: str | None = None) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    if status == "running":
        conn.execute(
            "UPDATE repo_index_stages SET status=?, started_at=? WHERE run_id=? AND builder_name=?",
            (status, timestamp, run_id, builder),
        )
    else:
        conn.execute(
            "UPDATE repo_index_stages SET status=?, completed_at=?, diagnostic_error=? WHERE run_id=? AND builder_name=?",
            (status, timestamp, error, run_id, builder),
        )
    conn.commit()


def _run_builder(
    builder: str,
    repo_key: str,
    repo_id: int,
    root: Path,
    candidate_db: str,
    manifest_entry: dict,
) -> None:
    if builder == "scan":
        from parser.scan_repo import scan

        scan(repo_key=repo_key, db_path=candidate_db)
    elif builder == "symbols":
        from parser.extract_symbols import extract_all

        extract_all(
            only_changed=False,
            repo_key=repo_key,
            db_path=candidate_db,
            write_logs=False,
        )
    elif builder == "relationships":
        from parser.extract_relationships import extract_all

        extract_all(
            only_changed=False, repo_key=repo_key, db_path=candidate_db, reset=True
        )
    elif builder == "integration_links":
        # Resolver extractors are additive and intentionally absent until a
        # repository declares a concrete, deterministic contract extractor.
        return
    elif builder == "entities":
        from scripts import scan_ent_files
        from scripts.build_entities import build

        entities_path = Path(candidate_db).with_name(
            f"{Path(candidate_db).name}.{repo_key}.entities.jsonl"
        )
        try:
            scan_ent_files.scan(root, entities_path)
            build(candidate_db, entities_path, reset=True, repo_key=repo_key)
        finally:
            entities_path.unlink(missing_ok=True)
    elif builder == "entity_roots":
        from catalog.db import get_connection
        from scripts.build_entity_roots import build_entity_roots

        conn = get_connection(candidate_db)
        try:
            build_entity_roots(conn, reset=True, repo_id=repo_id)
        finally:
            conn.close()
    elif builder == "openapi_scan":
        from catalog.db import get_connection
        from scripts.scan_openapispec import scan_openapispec

        conn = get_connection(candidate_db)
        try:
            scan_openapispec(conn, root, repo_id)
            conn.commit()
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
            _link_openapispec(conn, root, repo_id, None)
            conn.commit()
        finally:
            conn.close()
    elif builder == "workflows":
        from scripts.build_workflows import build

        build(candidate_db, root, repo_id, reset=True)
    elif builder == "security":
        from scripts.build_security_mappings import build

        build(
            candidate_db,
            repo_key=repo_key,
            reset=True,
            max_parse_failures=-1,
            max_unresolved=-1,
        )
    elif builder == "rest_endpoints":
        from scripts.build_rest_endpoints import build

        build(candidate_db, root, repo_id, reset=True)
    elif builder == "entity_access_links":
        from scripts.build_entity_access_links import build

        build(candidate_db, reset=True, repo_key=repo_key)
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
                WHERE r.repo_key = 'ia-main' AND re.source_version IS NOT NULL
                """
            ).fetchone()[0]
            if not production_endpoints:
                raise RefreshError(
                    "ia-main REST endpoints are absent; refresh ia-main before REST automation coverage"
                )
            build(
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
        raise RefreshError(f"candidate has duplicate repository paths: {duplicate_paths[:3]}")


def _record_failed_refresh(
    active: Path, manifest: dict, repo_key: str, error: Exception
) -> None:
    """Best-effort diagnostic history without replacing the active catalog."""
    try:
        conn = sqlite3.connect(active)
        conn.row_factory = sqlite3.Row
        try:
            register_manifest(conn, manifest)
            repo = get_repository(conn, repo_key)
            conn.execute(
                """INSERT INTO repo_index_runs(
                       repo_id, tracked_branch, status, diagnostic_error, completed_at
                   ) VALUES (?, ?, 'failed', ?, CURRENT_TIMESTAMP)""",
                (int(repo["id"]), str(repo["tracked_branch"]), str(error)),
            )
            conn.execute(
                """UPDATE repos SET
                       last_attempt_status='failed', last_attempted_at=CURRENT_TIMESTAMP,
                       last_attempt_error=?
                   WHERE id=?""",
                (str(error), int(repo["id"])),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # The candidate failure remains the primary error.  In particular, do
        # not mask it if the active catalog has not yet been migrated.
        return


def refresh_repository(db_path: str | Path, manifest_path: str | Path, repo_key: str) -> None:
    active = Path(db_path).resolve()
    if not active.is_file():
        raise RefreshError(f"catalog database does not exist: {active}")
    manifest = load_workspace_manifest(manifest_path)
    manifest_entry = _manifest_repository(manifest, repo_key)
    legacy_entry = next(
        (entry for entry in manifest["repositories"] if entry["repo_key"] == "ia-main"),
        None,
    )
    candidate = active.with_name(f"{active.name}.candidate.{uuid.uuid4().hex}")
    previous = active.with_name(f"{active.name}.previous")

    _backup_database(active, candidate)
    try:
        conn = sqlite3.connect(candidate)
        conn.row_factory = sqlite3.Row
        try:
            if legacy_entry is not None:
                migrate_multi_repo(
                    db_path=str(candidate),
                    local_root=str(legacy_entry["local_root"]),
                    tracked_branch=str(legacy_entry["tracked_branch"]),
                )
            register_manifest(conn, manifest)
            conn.commit()
            repo = get_repository(conn, repo_key)
            if not repo["enabled"]:
                raise RefreshError(f"repository is disabled: {repo_key}")
            root = resolve_repository_root(conn, repo_key)
            branch = str(repo["tracked_branch"])
            start_sha = source_revision(root, branch)
            plan = build_plan(str(repo["profile"] or "generic"), json.loads(repo["effective_builders_json"] or "[]"))
            conn.execute(
                "UPDATE repos SET effective_builders_json=? WHERE id=?",
                (json.dumps(plan, separators=(",", ":")), int(repo["id"])),
            )
            conn.commit()
            run_id = _record_run(conn, int(repo["id"]), branch, start_sha, plan)

            for builder in plan:
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
            conn.close()
            conn = sqlite3.connect(candidate)
            conn.row_factory = sqlite3.Row
            _validate_candidate(conn, int(repo["id"]))
            if source_revision(root, branch) != start_sha:
                raise RefreshError("repository revision changed while candidate was built")
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
                conn.execute("UPDATE graph_builds SET status='previous' WHERE status='active'")
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
        _record_failed_refresh(active, manifest, repo_key, exc)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh a repository through a validated SQLite candidate")
    parser.add_argument("--db", default="catalog/catalog.db")
    parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    refresh_repository(args.db, args.manifest, args.repo)


if __name__ == "__main__":
    main()
