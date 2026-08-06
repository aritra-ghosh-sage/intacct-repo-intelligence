"""Foundation and immutable inventory builder for Repo Intelligence V1.

This module deliberately supports one repository (``ia-main``), one full
build mode, and one sequential inventory step.  It is not a compatibility
wrapper around the legacy refresh orchestration.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from catalog.refresh_transaction import (
    assert_parent_unchanged,
    parent_descriptor,
    promote_catalog_candidate,
    refresh_lock,
)
from catalog.repositories import RepositoryError, load_workspace_manifest
from catalog.source_snapshot import SourceSnapshotError, materialize_source_snapshot
from parser.scan_repo import detect_language

SCHEMA_PATH = Path(__file__).with_name("repo_v1_schema.sql")
REPO_KEY = "ia-main"


class RepoV1Error(RuntimeError):
    """A V1 foundation or inventory build cannot safely proceed."""


@dataclass(frozen=True)
class BuildResult:
    build_token: str
    target_commit_sha: str
    file_count: int
    active_db: Path
    promoted: bool


@dataclass(frozen=True)
class _RepositoryConfig:
    repo_key: str
    name: str | None
    kind: str | None
    language: str | None
    remote_url: str | None
    local_root: Path
    tracked_branch: str


def _repository_config(manifest_path: Path) -> _RepositoryConfig:
    try:
        manifest = load_workspace_manifest(manifest_path)
    except RepositoryError as exc:
        raise RepoV1Error(str(exc)) from exc
    matches = [
        entry
        for entry in manifest["repositories"]
        if entry.get("repo_key") == REPO_KEY
    ]
    if len(matches) != 1:
        raise RepoV1Error("workspace manifest must contain exactly one ia-main entry")
    entry = matches[0]
    if entry.get("storage", "central") != "central":
        raise RepoV1Error("ia-main must use central storage for the V1 foundation")
    root = Path(str(entry["local_root"])).expanduser().resolve()
    if not root.is_dir():
        raise RepoV1Error(f"ia-main checkout does not exist: {root}")
    return _RepositoryConfig(
        repo_key=REPO_KEY,
        name=entry.get("name"),
        kind=entry.get("kind"),
        language=entry.get("language"),
        remote_url=entry.get("remote_url"),
        local_root=root,
        tracked_branch=str(entry["tracked_branch"]),
    )


def _new_candidate(active_db: Path) -> Path:
    active_db.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{active_db.name}.candidate.", suffix=".db", dir=active_db.parent
    )
    os.close(descriptor)
    return Path(raw_path)


def _connect_candidate(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _insert_build(
    conn: sqlite3.Connection,
    *,
    candidate: Path,
    build_token: str,
    target_commit_sha: str,
) -> int:
    cursor = conn.execute(
        """INSERT INTO catalog_builds(
               build_token,catalog_path,status,source_revisions_json
           ) VALUES (?,?,?,?)""",
        (
            build_token,
            str(candidate),
            "building",
            json.dumps({REPO_KEY: target_commit_sha}, sort_keys=True, separators=(",", ":")),
        ),
    )
    return int(cursor.lastrowid)


def _build_inventory(
    *,
    candidate: Path,
    config: _RepositoryConfig,
    target_commit_sha: str,
    build_token: str,
) -> int:
    conn = _connect_candidate(candidate)
    try:
        build_id = _insert_build(
            conn,
            candidate=candidate,
            build_token=build_token,
            target_commit_sha=target_commit_sha,
        )
        repo_id = int(
            conn.execute(
                """INSERT INTO repos(
                       repo_key,name,kind,language,remote_url,local_root,
                       tracked_branch,target_commit_sha,build_id
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    config.repo_key,
                    config.name,
                    config.kind,
                    config.language,
                    config.remote_url,
                    str(config.local_root),
                    config.tracked_branch,
                    target_commit_sha,
                    build_id,
                ),
            ).lastrowid
        )
        with materialize_source_snapshot(
            config.repo_key, config.local_root, target_commit_sha, candidate.parent
        ) as snapshot:
            for entry in snapshot.entries:
                conn.execute(
                    """INSERT INTO files(
                           repo_id,path,blob_object_id,file_mode,size_bytes,
                           language,source_commit_sha
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        entry.path,
                        entry.object_id,
                        entry.mode,
                        entry.size,
                        detect_language(entry.path),
                        snapshot.target_sha,
                    ),
                )
        file_count = int(
            conn.execute("SELECT COUNT(*) FROM files WHERE repo_id=?", (repo_id,)).fetchone()[0]
        )
        conn.execute(
            "UPDATE catalog_builds SET status='validated',completed_at=?,validation_summary=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                json.dumps({"repo_key": REPO_KEY, "file_count": file_count}, sort_keys=True),
                build_id,
            ),
        )
        conn.commit()
        return file_count
    except (OSError, SourceSnapshotError, sqlite3.Error) as exc:
        conn.rollback()
        raise RepoV1Error(str(exc)) from exc
    finally:
        conn.close()


def _validate_candidate(candidate: Path, *, target_commit_sha: str, build_token: str) -> None:
    conn = sqlite3.connect(candidate)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RepoV1Error(f"candidate integrity check failed: {integrity}")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RepoV1Error(f"candidate foreign-key check failed: {violations[:3]}")
        build = conn.execute(
            "SELECT id,status,source_revisions_json FROM catalog_builds WHERE build_token=?",
            (build_token,),
        ).fetchone()
        if build is None or build["status"] != "validated":
            raise RepoV1Error("candidate build is not validated")
        if json.loads(str(build["source_revisions_json"])) != {REPO_KEY: target_commit_sha}:
            raise RepoV1Error("candidate source revision does not match target commit")
        repo = conn.execute(
            "SELECT id,repo_key,target_commit_sha FROM repos WHERE build_id=?", (build["id"],)
        ).fetchone()
        if repo is None or (repo["repo_key"], repo["target_commit_sha"]) != (REPO_KEY, target_commit_sha):
            raise RepoV1Error("candidate repository provenance is invalid")
        if conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] != 1:
            raise RepoV1Error("V1 candidate must contain exactly one repository")
        file_count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE repo_id=?", (repo["id"],)
        ).fetchone()[0]
        distinct_path_count = conn.execute(
            "SELECT COUNT(DISTINCT path) FROM files WHERE repo_id=?", (repo["id"],)
        ).fetchone()[0]
        if file_count != distinct_path_count:
            raise RepoV1Error("candidate inventory contains duplicate paths")
        invalid_provenance = conn.execute(
            """SELECT COUNT(*) FROM files
               WHERE (repo_id=? AND source_commit_sha<>?)
                  OR (repo_id=? AND (blob_object_id='' OR path=''))""",
            (repo["id"], target_commit_sha, repo["id"]),
        ).fetchone()[0]
        if invalid_provenance:
            raise RepoV1Error("candidate file provenance is invalid")
    finally:
        conn.close()


def _mark_candidate_active(candidate: Path, active_db: Path, build_token: str) -> None:
    conn = sqlite3.connect(candidate)
    try:
        conn.execute(
            "UPDATE catalog_builds SET status='active',catalog_path=? WHERE build_token=? AND status='validated'",
            (str(active_db), build_token),
        )
        if conn.total_changes != 1:
            raise RepoV1Error("candidate build could not enter active promotion state")
        conn.commit()
    finally:
        conn.close()


def build_ia_main(
    *,
    manifest_path: str | Path = "config/workspace_repos.yaml",
    active_db: str | Path = "catalog/repo-v1/catalog.db",
    target_sha: str | None = None,
    promote: bool = True,
) -> BuildResult:
    """Build and optionally promote a full immutable ``ia-main`` inventory."""

    manifest = _repository_config(Path(manifest_path))
    from catalog.source_snapshot import resolve_commit_sha

    try:
        resolved_sha = resolve_commit_sha(
            manifest.local_root, target_sha or manifest.tracked_branch
        )
    except SourceSnapshotError as exc:
        raise RepoV1Error(str(exc)) from exc

    active = Path(active_db).expanduser().resolve()
    token = uuid.uuid4().hex
    with refresh_lock(active):
        candidate = _new_candidate(active)
        expected_parent = parent_descriptor(active)
        try:
            file_count = _build_inventory(
                candidate=candidate,
                config=manifest,
                target_commit_sha=resolved_sha,
                build_token=token,
            )
            _validate_candidate(candidate, target_commit_sha=resolved_sha, build_token=token)
            if not promote:
                return BuildResult(token, resolved_sha, file_count, active, False)
            _mark_candidate_active(candidate, active, token)
            assert_parent_unchanged(active, expected_parent)
            previous = active.with_name(active.name + ".previous")
            promote_catalog_candidate(active, candidate, previous, token)
            return BuildResult(token, resolved_sha, file_count, active, True)
        finally:
            candidate.unlink(missing_ok=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("config/workspace_repos.yaml"))
    parser.add_argument("--active-db", type=Path, default=Path("catalog/repo-v1/catalog.db"))
    parser.add_argument("--target-sha", help="Git revision; defaults to ia-main tracked_branch")
    parser.add_argument("--no-promote", action="store_true")
    args = parser.parse_args()
    result = build_ia_main(
        manifest_path=args.manifest,
        active_db=args.active_db,
        target_sha=args.target_sha,
        promote=not args.no_promote,
    )
    print(json.dumps(result.__dict__, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
