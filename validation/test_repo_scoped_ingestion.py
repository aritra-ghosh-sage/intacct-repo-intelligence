"""Focused regressions for generic multi-repository ingestion isolation."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from parser import extract_relationships, scan_repo
from parser.repo_context import resolve_repo


class _ConnectionWithoutClose:
    """Keep an in-memory test database inspectable after scanner cleanup."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


class RepoScopedIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo_one = self.root / "one"
        self.repo_two = self.root / "two"
        self.repo_one.mkdir()
        self.repo_two.mkdir()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE repos (
                id INTEGER PRIMARY KEY,
                repo_key TEXT NOT NULL UNIQUE,
                local_root TEXT NOT NULL,
                tracked_branch TEXT
            );
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                repo_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                language TEXT,
                size_bytes INTEGER,
                sha1 TEXT,
                last_modified TEXT,
                last_indexed TEXT,
                last_symbols_extracted TEXT,
                last_relationships_extracted TEXT,
                UNIQUE(repo_id, path)
            );
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                parent_symbol TEXT,
                file_path TEXT
            );
            CREATE TABLE relationships (
                id INTEGER PRIMARY KEY,
                repo_id INTEGER,
                file_id INTEGER,
                extractor TEXT
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO repos (id, repo_key, local_root, tracked_branch) VALUES (?, ?, ?, 'main')",
            [(1, "one", str(self.repo_one)), (2, "two", str(self.repo_two))],
        )

    def test_repo_key_is_required_when_multiple_repositories_exist(self):
        with self.assertRaisesRegex(RuntimeError, "--repo is required"):
            resolve_repo(self.conn)
        self.assertEqual(resolve_repo(self.conn, "two").id, 2)

    def test_scan_allows_colliding_paths_and_purges_only_target_repo(self):
        relative = Path("src") / "shared.py"
        for root, contents in ((self.repo_one, "one = 1\n"), (self.repo_two, "two = 2\n")):
            target = root / relative
            target.parent.mkdir(exist_ok=True)
            target.write_text(contents, encoding="utf-8")

        connection = _ConnectionWithoutClose(self.conn)
        with patch.object(scan_repo, "get_connection", return_value=connection):
            scan_repo.scan("one")
            scan_repo.scan("two")

        rows = self.conn.execute(
            "SELECT repo_id, path FROM files ORDER BY repo_id"
        ).fetchall()
        self.assertEqual([(row["repo_id"], row["path"]) for row in rows], [(1, "src/shared.py"), (2, "src/shared.py")])

        one_id = self.conn.execute("SELECT id FROM files WHERE repo_id = 1").fetchone()[0]
        two_id = self.conn.execute("SELECT id FROM files WHERE repo_id = 2").fetchone()[0]
        self.conn.executemany(
            "INSERT INTO relationships (repo_id, file_id, extractor) VALUES (?, ?, 'phase2_regex_mvp')",
            [(1, one_id), (2, two_id)],
        )
        self.conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (300, ?, 'Gone', 'class')",
            (one_id,),
        )
        os.unlink(self.repo_one / relative)
        with patch.object(scan_repo, "get_connection", return_value=connection):
            scan_repo.scan("one")

        self.assertIsNone(self.conn.execute("SELECT 1 FROM files WHERE id = ?", (one_id,)).fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM files WHERE id = ?", (two_id,)).fetchone())
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM relationships WHERE repo_id = 1").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM relationships WHERE repo_id = 2").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM symbols WHERE id = 300").fetchone()[0],
            0,
        )

    def test_symbol_resolution_map_excludes_other_repositories(self):
        self.conn.executemany(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, 'python')",
            [(10, 1, "same.py"), (20, 2, "same.py")],
        )
        self.conn.executemany(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
            [(100, 10, "SharedName", "class"), (200, 20, "SharedName", "class")],
        )
        by_name, by_file, _ = extract_relationships.load_symbols(self.conn, 1)
        self.assertEqual([symbol.id for symbol in by_name["SharedName"]], [100])
        self.assertEqual(list(by_file), [10])


if __name__ == "__main__":
    unittest.main()
