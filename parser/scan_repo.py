# parser/scan_repo.py

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tqdm import tqdm

from catalog.db import get_connection
from catalog.delta import ChangedPath, ChangeType, path_is_in_scan_scope
from config import EXCLUDE_DIRS
from parser.repo_context import RepoContext, require_repo_scoped_files, resolve_repo


@dataclass(frozen=True)
class DeletedFile:
    file_id: int
    path: str
    sha1: str | None


@dataclass(frozen=True)
class ScanDeltaResult:
    affected_file_ids: tuple[int, ...]
    deleted_files: tuple[DeletedFile, ...]

    @property
    def affected_count(self) -> int:
        return len(self.affected_file_ids) + len(self.deleted_files)


@dataclass(frozen=True)
class FullScanResult:
    scanned: int
    added: int
    updated: int
    unchanged: int
    removed: int

    @property
    def affected_count(self) -> int:
        return self.added + self.updated + self.removed


def detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    mapping = {
        ".java": "java",
        ".php": "php",
        ".inc": "php",
        ".menu": "php",
        ".pol": "php",
        ".ent": "php",
        ".cls": "php",
        ".phtml": "php",
        ".cqry": "php",
        ".qry": "php",
        ".js": "javascript",
        ".ts": "typescript",
        ".sql": "sql",
        ".xml": "xml",
        ".json": "json",
        ".py": "python",
        ".yaml": "yaml",
        ".html": "html",
        ".xsl": "xslt",
        ".xslt": "xslt",
        ".rpt": "php",
    }
    return mapping.get(ext, "unknown")


def compute_sha1(filepath: str, chunk_size: int = 65536) -> str:
    h = hashlib.sha1()
    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def walk_repo(root: str, errors: list[tuple[str, str]] | None = None):
    root = os.path.abspath(root)
    collected_errors = errors if errors is not None else []

    def onerror(error: OSError) -> None:
        collected_errors.append((str(error.filename or root), str(error)))

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        # prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for name in filenames:
            absolute = os.path.join(dirpath, name)
            relative = Path(absolute).relative_to(root).as_posix()
            if not path_is_in_scan_scope(relative):
                continue
            yield absolute


def _purge_deleted_files(conn, repo: RepoContext, seen_paths: set[str]) -> int:
    """Remove generic facts for files no longer present in this repository."""
    rows = conn.execute(
        "SELECT id, path FROM files WHERE repo_id = ?", (repo.id,)
    ).fetchall()
    missing = [row for row in rows if row["path"] not in seen_paths]
    if not missing:
        return 0

    file_ids = [row["id"] for row in missing]
    placeholders = ",".join("?" for _ in file_ids)
    # Do this explicitly: existing catalog connections do not universally enable
    # SQLite foreign keys, and relationships have no file FK in the legacy
    # schema.
    conn.execute(
        f"DELETE FROM relationships WHERE file_id IN ({placeholders})", file_ids
    )
    conn.execute(f"DELETE FROM symbols WHERE file_id IN ({placeholders})", file_ids)
    conn.execute(f"DELETE FROM files WHERE id IN ({placeholders})", file_ids)
    return len(missing)


def _raw_bytes_for_git_blob(root: Path, blob_sha: str | None) -> bytes | None:
    if not blob_sha:
        return None
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", blob_sha],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"expected Git blob is unavailable: {blob_sha}")
    return result.stdout


def _raw_sha1_for_git_blob(root: Path, blob_sha: str | None) -> str | None:
    value = _raw_bytes_for_git_blob(root, blob_sha)
    return hashlib.sha1(value).hexdigest() if value is not None else None


def apply_changed_paths(
    changed_paths: list[ChangedPath] | tuple[ChangedPath, ...],
    *,
    repo_key: str,
    db_path: str,
    source_root: Path,
    git_root: Path,
) -> ScanDeltaResult:
    """Apply an explicit committed path delta without walking the repository."""

    conn = get_connection(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    require_repo_scoped_files(conn)
    repo = resolve_repo(conn, repo_key)
    started = datetime.now(UTC).isoformat()
    affected_ids: list[int] = []
    deleted: list[DeletedFile] = []

    def delete_path(path: str, expected_blob_sha: str | None) -> None:
        row = conn.execute(
            "SELECT id,path,sha1 FROM files WHERE repo_id=? AND path=?",
            (repo.id, path),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"delta delete has no owned catalog file: {path}")
        expected_raw = _raw_sha1_for_git_blob(git_root, expected_blob_sha)
        if expected_raw is not None and row["sha1"] != expected_raw:
            raise RuntimeError(
                f"delta delete hash mismatch for {path}: catalog={row['sha1']} expected={expected_raw}"
            )
        file_id = int(row["id"])
        deleted.append(DeletedFile(file_id, str(row["path"]), row["sha1"]))
        # Ownership is the source file.  Explicit cleanup remains necessary for
        # catalogs opened by older callers without FK enforcement.
        conn.execute("DELETE FROM relationships WHERE file_id=?", (file_id,))
        conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))

    def upsert_path(
        path: str,
        expected_old_blob_sha: str | None,
        expected_new_blob_sha: str | None,
    ) -> None:
        if not path_is_in_scan_scope(path):
            raise RuntimeError(f"delta add/update is outside scan scope: {path}")
        absolute = source_root / path
        if not absolute.is_file():
            raise RuntimeError(f"delta source path does not exist: {absolute}")
        row = conn.execute(
            "SELECT id,sha1 FROM files WHERE repo_id=? AND path=?", (repo.id, path)
        ).fetchone()
        expected_raw = _raw_sha1_for_git_blob(git_root, expected_old_blob_sha)
        if row is not None and expected_raw is not None and row["sha1"] != expected_raw:
            raise RuntimeError(
                f"delta update hash mismatch for {path}: catalog={row['sha1']} expected={expected_raw}"
            )
        stat = absolute.stat()
        source_bytes = absolute.read_bytes()
        target_bytes = _raw_bytes_for_git_blob(git_root, expected_new_blob_sha)
        if target_bytes is None:
            raise RuntimeError(f"delta target blob is missing for {path}")
        sha1 = hashlib.sha1(source_bytes).hexdigest()
        target_sha1 = hashlib.sha1(target_bytes).hexdigest()
        if sha1 != target_sha1:
            raise RuntimeError(
                f"delta target hash mismatch for {path}: snapshot={sha1} expected={target_sha1}"
            )
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        if row is None:
            cursor = conn.execute(
                """INSERT INTO files(
                       repo_id,path,language,size_bytes,sha1,last_modified,last_indexed
                   ) VALUES (?,?,?,?,?,?,?)""",
                (
                    repo.id,
                    path,
                    detect_language(path),
                    stat.st_size,
                    sha1,
                    modified,
                    started,
                ),
            )
            affected_ids.append(int(cursor.lastrowid))
        else:
            conn.execute(
                """UPDATE files SET language=?,size_bytes=?,sha1=?,last_modified=?,
                       last_indexed=? WHERE id=?""",
                (
                    detect_language(path),
                    stat.st_size,
                    sha1,
                    modified,
                    started,
                    int(row["id"]),
                ),
            )
            affected_ids.append(int(row["id"]))

    try:
        for change in changed_paths:
            old_in_scope = bool(
                change.old_path is not None and path_is_in_scan_scope(change.old_path)
            )
            new_in_scope = bool(
                change.new_path is not None and path_is_in_scan_scope(change.new_path)
            )
            if change.change_type == ChangeType.RENAMED:
                if old_in_scope:
                    delete_path(str(change.old_path), change.old_blob_sha)
                if new_in_scope:
                    upsert_path(
                        str(change.new_path),
                        None,
                        change.new_blob_sha,
                    )
            elif change.change_type == ChangeType.DELETED:
                if old_in_scope:
                    delete_path(str(change.old_path), change.old_blob_sha)
            elif change.change_type == ChangeType.ADDED:
                if new_in_scope:
                    upsert_path(str(change.new_path), None, change.new_blob_sha)
            elif change.change_type == ChangeType.MODIFIED:
                if old_in_scope and not new_in_scope:
                    delete_path(str(change.old_path), change.old_blob_sha)
                elif new_in_scope:
                    upsert_path(
                        str(change.new_path),
                        change.old_blob_sha if old_in_scope else None,
                        change.new_blob_sha,
                    )
            else:
                raise RuntimeError(f"unsupported change type: {change.change_type}")
        conn.commit()
        return ScanDeltaResult(tuple(sorted(set(affected_ids))), tuple(deleted))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def scan(repo_key: str | None = None, db_path: str | None = None) -> FullScanResult:
    conn = get_connection(db_path)
    cur = conn.cursor()
    require_repo_scoped_files(conn)
    repo = resolve_repo(conn, repo_key)

    started = datetime.now(UTC).isoformat()
    files_scanned = 0
    files_added = 0
    files_updated = 0
    files_unchanged = 0

    print(f"📂 Scanning [{repo.repo_key}]: {repo.local_root}")

    walk_errors: list[tuple[str, str]] = []
    all_files = list(walk_repo(str(repo.local_root), walk_errors))
    print(f"🔎 Found {len(all_files)} candidate files")
    seen_paths: set[str] = set()

    failures: list[tuple[str, str]] = list(walk_errors)
    for filepath in tqdm(all_files, desc="Indexing"):
        try:
            rel_path = Path(filepath).relative_to(repo.local_root).as_posix()
            if not path_is_in_scan_scope(rel_path):
                continue
            seen_paths.add(rel_path)
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(
                os.path.getmtime(filepath), tz=UTC
            ).isoformat()

            # Fetch existing row
            row = cur.execute(
                "SELECT sha1, last_modified FROM files WHERE repo_id = ? AND path = ?",
                (repo.id, rel_path),
            ).fetchone()

            sha1 = compute_sha1(filepath)

            if row is None:
                cur.execute(
                    """
                    INSERT INTO files
                    (repo_id, path, language, size_bytes, sha1, last_modified, last_indexed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        repo.id,
                        rel_path,
                        detect_language(rel_path),
                        size,
                        sha1,
                        mtime,
                        started,
                    ),
                )
                files_added += 1
            elif row["sha1"] != sha1:
                cur.execute(
                    """
                    UPDATE files
                    SET language = ?,
                        size_bytes = ?,
                        sha1 = ?,
                        last_modified = ?,
                        last_indexed = ?
                    WHERE repo_id = ? AND path = ?
                """,
                    (
                        detect_language(rel_path),
                        size,
                        sha1,
                        mtime,
                        started,
                        repo.id,
                        rel_path,
                    ),
                )
                files_updated += 1
            else:
                files_unchanged += 1

            files_scanned += 1

        except Exception as e:  # noqa: BLE001 - aggregate every per-file failure
            failures.append((str(filepath), str(e)))

    if failures:
        conn.rollback()
        conn.close()
        details = "; ".join(f"{path}: {message}" for path, message in sorted(failures))
        raise RuntimeError(f"full scan input failures: {details}")

    files_removed = _purge_deleted_files(conn, repo, seen_paths)
    conn.commit()
    conn.close()

    print("\n📊 Summary")
    print(f"   Scanned:  {files_scanned}")
    print(f"   Added:    {files_added}")
    print(f"   Updated:  {files_updated}")
    print(f"   Unchanged:{files_unchanged:>6}")
    print(f"   Removed:  {files_removed}")
    return FullScanResult(
        scanned=files_scanned,
        added=files_added,
        updated=files_updated,
        unchanged=files_unchanged,
        removed=files_removed,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="Registered repo_key to scan")
    parser.add_argument("--db", help="Catalog database path")
    args = parser.parse_args()
    scan(repo_key=args.repo, db_path=args.db)
