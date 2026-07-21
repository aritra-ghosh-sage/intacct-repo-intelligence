# parser/scan_repo.py

import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from tqdm import tqdm

from config import INCLUDE_EXTENSIONS, EXCLUDE_DIRS
from catalog.db import get_connection
from parser.repo_context import RepoContext, require_repo_scoped_files, resolve_repo


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


def walk_repo(root: str):
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        # prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext not in INCLUDE_EXTENSIONS:
                # print(f'excluded file: ${ext}')
                continue
            yield os.path.join(dirpath, name)


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


def scan(repo_key: str | None = None, db_path: str | None = None):
    conn = get_connection(db_path)
    cur = conn.cursor()
    require_repo_scoped_files(conn)
    repo = resolve_repo(conn, repo_key)

    started = datetime.now(timezone.utc).isoformat()
    files_scanned = 0
    files_added = 0
    files_updated = 0
    files_skipped = 0

    print(f"📂 Scanning [{repo.repo_key}]: {repo.local_root}")

    all_files = list(walk_repo(str(repo.local_root)))
    print(f"🔎 Found {len(all_files)} candidate files")
    seen_paths: set[str] = set()

    for filepath in tqdm(all_files, desc="Indexing"):
        try:
            rel_path = os.path.relpath(filepath, repo.local_root)
            seen_paths.add(rel_path)
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(
                os.path.getmtime(filepath), tz=timezone.utc
            ).isoformat()

            # Fetch existing row
            row = cur.execute(
                "SELECT sha1, last_modified FROM files WHERE repo_id = ? AND path = ?",
                (repo.id, rel_path),
            ).fetchone()

            # Fast skip: same mtime + size heuristic
            if row and row["last_modified"] == mtime:
                files_skipped += 1
                files_scanned += 1
                continue

            sha1 = compute_sha1(filepath)

            if row is None:
                cur.execute(
                    """
                    INSERT INTO files
                    (repo_id, path, language, size_bytes, sha1, last_modified, last_indexed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (repo.id, rel_path, detect_language(rel_path), size, sha1, mtime, started),
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
                    (detect_language(rel_path), size, sha1, mtime, started, repo.id, rel_path),
                )
                files_updated += 1
            else:
                files_skipped += 1

            files_scanned += 1

            # Commit periodically for safety
            if files_scanned % 1000 == 0:
                conn.commit()

        except Exception as e:
            print(f"⚠️  Error on {filepath}: {e}")

    files_removed = _purge_deleted_files(conn, repo, seen_paths)
    conn.commit()
    conn.close()

    print("\n📊 Summary")
    print(f"   Scanned:  {files_scanned}")
    print(f"   Added:    {files_added}")
    print(f"   Updated:  {files_updated}")
    print(f"   Skipped:  {files_skipped}")
    print(f"   Removed:  {files_removed}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="Registered repo_key to scan")
    parser.add_argument("--db", help="Catalog database path")
    args = parser.parse_args()
    scan(repo_key=args.repo, db_path=args.db)
