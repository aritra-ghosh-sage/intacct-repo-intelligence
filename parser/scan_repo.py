# parser/scan_repo.py

import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from tqdm import tqdm

from config import (
    REPO_PATH,
    INCLUDE_EXTENSIONS,
    EXCLUDE_DIRS
)
from catalog.db import get_connection


def detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    mapping = {
        ".java": "java",
        ".php": "php",
        ".inc": "php",
        ".ent": "php",
        ".cls": "php",
        ".phtml": "php",
        ".cqry": "php",
        ".js": "javascript",
        ".ts": "typescript",
        ".sql": "sql",
        ".xml": "xml",
        ".json": "json",
        ".py": "python",
        ".yaml": "yaml",
        ".html": "html",
        ".xsl": "xslt",
        ".xslt": "xslt"
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
                #print(f'excluded file: ${ext}')
                continue
            yield os.path.join(dirpath, name)


def scan():
    conn = get_connection()
    cur = conn.cursor()

    started = datetime.now(timezone.utc).isoformat()
    files_scanned = 0
    files_added = 0
    files_updated = 0
    files_skipped = 0

    print(f"📂 Scanning: {REPO_PATH}")

    all_files = list(walk_repo(REPO_PATH))
    print(f"🔎 Found {len(all_files)} candidate files")

    for filepath in tqdm(all_files, desc="Indexing"):
        try:
            rel_path = os.path.relpath(filepath, REPO_PATH)
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(
                os.path.getmtime(filepath),
                tz=timezone.utc
            ).isoformat()

            # Fetch existing row
            row = cur.execute(
                "SELECT sha1, last_modified FROM files WHERE path = ?",
                (rel_path,)
            ).fetchone()

            # Fast skip: same mtime + size heuristic
            if row and row["last_modified"] == mtime:
                files_skipped += 1
                files_scanned += 1
                continue

            sha1 = compute_sha1(filepath)

            if row is None:
                cur.execute("""
                    INSERT INTO files
                    (path, language, size_bytes, sha1, last_modified, last_indexed)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    rel_path,
                    detect_language(rel_path),
                    size,
                    sha1,
                    mtime,
                    started
                ))
                files_added += 1
            elif row["sha1"] != sha1:
                cur.execute("""
                    UPDATE files
                    SET language = ?,
                        size_bytes = ?,
                        sha1 = ?,
                        last_modified = ?,
                        last_indexed = ?
                    WHERE path = ?
                """, (
                    detect_language(rel_path),
                    size,
                    sha1,
                    mtime,
                    started,
                    rel_path
                ))
                files_updated += 1
            else:
                files_skipped += 1

            files_scanned += 1

            # Commit periodically for safety
            if files_scanned % 1000 == 0:
                conn.commit()

        except Exception as e:
            print(f"⚠️  Error on {filepath}: {e}")

    completed = datetime.now(timezone.utc).isoformat()

    cur.execute("""
        INSERT INTO index_runs
        (started_at, completed_at, files_scanned,
         files_added, files_updated, files_skipped, git_commit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        started, completed,
        files_scanned, files_added,
        files_updated, files_skipped,
        None
    ))

    conn.commit()
    conn.close()

    print("\n📊 Summary")
    print(f"   Scanned:  {files_scanned}")
    print(f"   Added:    {files_added}")
    print(f"   Updated:  {files_updated}")
    print(f"   Skipped:  {files_skipped}")


if __name__ == "__main__":
    scan()
