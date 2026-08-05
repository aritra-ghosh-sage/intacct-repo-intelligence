"""Regression coverage for repository-scoped normalized evidence hashes."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from catalog.repository_evidence import repository_evidence_fingerprint


class RepositoryEvidenceFingerprintTests(unittest.TestCase):
    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript((Path(__file__).parents[1] / "catalog/schema.sql").read_text())
        return conn

    def test_equivalent_rows_with_different_sqlite_ids_hash_identically(self) -> None:
        left, right = self._connection(), self._connection()
        try:
            for conn, repo_id, file_id, symbol_id in (
                (left, 1, 10, 20),
                (right, 8, 80, 200),
            ):
                conn.execute(
                    "INSERT INTO repos(id,repo_key,remote_url,local_root,tracked_branch) VALUES (?,?,?,?,?)",
                    (repo_id, "service", "https://github.com/acme/service.git", "/different/local/root", "main"),
                )
                conn.execute(
                    "INSERT INTO files(id,repo_id,path,language,sha1) VALUES (?,?,?,?,?)",
                    (file_id, repo_id, "src/service.php", "php", "a" * 40),
                )
                conn.execute(
                    "INSERT INTO symbols(id,file_id,name,kind,stable_key) VALUES (?,?,?,?,?)",
                    (symbol_id, file_id, "Service", "class", "class:Service"),
                )
                conn.commit()
            self.assertEqual(
                repository_evidence_fingerprint(left, 1),
                repository_evidence_fingerprint(right, 8),
            )
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
