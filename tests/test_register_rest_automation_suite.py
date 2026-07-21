from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.register_rest_automation_suite import register_suite


class RegisterRestAutomationSuiteTests(unittest.TestCase):
    def test_upserts_only_an_explicit_mapping_inside_the_suite(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE source_repositories(
                id INTEGER PRIMARY KEY,
                suite_id TEXT UNIQUE NOT NULL,
                repo_root TEXT NOT NULL,
                kind TEXT NOT NULL,
                revision TEXT,
                enabled INTEGER NOT NULL,
                object_mapping_path TEXT,
                updated_at TEXT
            )
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping = root / "object-mapping.json"
            mapping.write_text("{}", encoding="utf-8")
            register_suite(conn, "suite-a", root, mapping, "abc123", True)
            register_suite(conn, "suite-a", root, mapping, "def456", False)
        row = conn.execute(
            "SELECT suite_id, kind, revision, enabled, object_mapping_path FROM source_repositories"
        ).fetchone()
        self.assertEqual(row, ("suite-a", "test_suite", "def456", 0, "object-mapping.json"))


if __name__ == "__main__":
    unittest.main()
