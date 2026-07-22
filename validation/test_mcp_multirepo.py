from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from intacct_mcp.server import Catalog


ROOT = Path(__file__).resolve().parents[1]


class McpMultiRepoTests(unittest.TestCase):
    def test_entity_context_and_repository_status_are_repo_qualified(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "catalog.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript((ROOT / "catalog/schema.sql").read_text())
                conn.executescript(
                    """
                    INSERT INTO repos(
                        id, repo_key, local_root, tracked_branch, indexed_commit_sha,
                        index_status, last_attempt_status, last_attempted_at, last_attempt_error
                    ) VALUES
                        (1, 'one', '/repo/one', 'main', 'one-sha', 'active', 'failed', '2026-07-21T00:00:00Z', 'dirty checkout'),
                        (2, 'two', '/repo/two', 'main', 'two-sha', 'active', 'active', '2026-07-21T00:01:00Z', NULL);
                    INSERT INTO entity_nodes(id, name, entity_type) VALUES (1, 'Customer', 'entity');
                    INSERT INTO entity_occurrences(
                        repo_id, entity_id, ent_file, module, table_name, view_name, dummy, extractor, confidence
                    ) VALUES
                        (1, 1, 'one/Customer.ent', 'one-module', 'one_customer', NULL, 0, 'fixture', 1.0),
                        (2, 1, 'two/Customer.ent', 'two-module', 'two_customer', NULL, 0, 'fixture', 1.0);
                    """
                )
                conn.commit()
            finally:
                conn.close()

            catalog = Catalog(str(db_path), str(Path(directory) / "missing.lbug"))
            one = catalog.entity("Customer", "one")
            self.assertEqual(one["data"]["occurrences"][0]["repo_key"], "one")
            self.assertEqual(one["data"]["occurrences"][0]["module"], "one-module")
            self.assertEqual(len(one["data"]["occurrences"]), 1)

            status = catalog.repositories()
            repo_one = status["data"]["repositories"][0]
            self.assertEqual(repo_one["indexed_commit_sha"], "one-sha")
            self.assertEqual(repo_one["last_attempt_status"], "failed")
            self.assertEqual(repo_one["last_attempt_error"], "dirty checkout")
