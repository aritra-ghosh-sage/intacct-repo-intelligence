from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.rest_coverage import coverage_summary
from intacct_mcp.server import Catalog
from scripts.query_rest import _coverage_rows

ROOT = Path(__file__).resolve().parents[1]


class McpRestCoverageTests(unittest.TestCase):
    def _catalog(
        self,
    ) -> tuple[tempfile.TemporaryDirectory[str], Catalog, sqlite3.Connection]:
        directory = tempfile.TemporaryDirectory()
        db_path = Path(directory.name) / "catalog.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        conn.executescript(
            """
            INSERT INTO repos(id, repo_key, local_root, tracked_branch)
                VALUES (1, 'ia-main', '/repo/main', 'main'), (2, 'suite', '/repo/suite', 'main');
            INSERT INTO entity_nodes(id, name, entity_type) VALUES (9, 'Customer', 'entity');
            INSERT INTO files(id, repo_id, path, language)
                VALUES (1, 1, 'openapi/customer.yaml', 'yaml'),
                       (2, 2, 'features/customer.feature', 'gherkin');
            INSERT INTO rest_endpoints(id, repo_id, method, path, source_version, entity_id, file_id)
                VALUES (1, 1, 'GET', '/objects/customer', 's1', 9, 1),
                       (2, 1, 'POST', '/objects/customer', 's1', 9, 1);
            INSERT INTO test_cases(id, repo_id, file_id, feature_name, scenario_name, case_name,
                                   feature_line, scenario_line, eligibility)
                VALUES (1, 2, 2, 'Customer', 'Read', 'Read customer', 1, 3, 'active');
            INSERT INTO test_requests(id, test_case_id, ordinal, step_line, method, normalized_path,
                                      request_version, operation_kind)
                VALUES (1, 1, 1, 4, 'GET', '/objects/customer', 's1', 'collection');
            INSERT INTO test_endpoint_links(test_request_id, rest_endpoint_id, resolution_kind)
                VALUES (1, 1, 'exact_version');
            INSERT INTO test_entity_links(test_request_id, entity_id, rest_endpoint_id)
                VALUES (1, 9, 1);
            """
        )
        conn.commit()
        return (
            directory,
            Catalog(str(db_path), str(Path(directory.name) / "missing.lbug")),
            conn,
        )

    def test_mcp_coverage_matches_shared_cli_query(self):
        directory, catalog, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)

        expected, diagnostics = _coverage_rows(conn, 9, "s1", 25)
        actual = catalog.coverage("customer", "s1", 25)

        self.assertEqual("ok", actual["status"])
        self.assertEqual(expected, actual["data"]["endpoint_coverage"])
        self.assertEqual(diagnostics, actual["data"]["diagnostics"])
        self.assertEqual(
            coverage_summary(expected, diagnostics), actual["data"]["summary"]
        )
        self.assertEqual("active", actual["data"]["endpoint_coverage"][0]["coverage"])
        self.assertEqual(
            "uncovered", actual["data"]["endpoint_coverage"][1]["coverage"]
        )

    def test_mcp_coverage_reports_missing_entity(self):
        directory, catalog, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)

        result = catalog.coverage("DoesNotExist")

        self.assertEqual("not_found", result["status"])
        self.assertEqual("entity_not_found", result["error"]["code"])

    def test_mcp_coverage_reports_missing_coverage_tables(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db_path = Path(directory.name) / "catalog.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()

        result = Catalog(
            str(db_path), str(Path(directory.name) / "missing.lbug")
        ).coverage("Customer")

        self.assertEqual("error", result["status"])
        self.assertEqual("coverage_tables_missing", result["error"]["code"])
        self.assertIn("test_cases", result["error"]["details"]["missing_tables"])


if __name__ == "__main__":
    unittest.main()
