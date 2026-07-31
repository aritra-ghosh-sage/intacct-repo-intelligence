"""Focused regressions for repo-qualified OpenAPI, REST, and workflow builders."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import (
    build_rest_endpoints,
    build_workflows,
    link_openapispec,
    scan_openapispec,
)


class RepoScopedOpenApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.one = self.root / "one"
        self.two = self.root / "two"
        for root in (self.one, self.two):
            path = root / "app/source/openapispec/ap/paths"
            path.mkdir(parents=True)
            (path / "bill.s1.api.yaml").write_text(
                "paths:\n  /bills:\n    get: {}\n", encoding="utf-8"
            )
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = (Path(__file__).parents[1] / "catalog/schema.sql").read_text()
        self.conn.executescript(schema)
        self.conn.executemany(
            "INSERT INTO repos(id, repo_key, local_root, tracked_branch) VALUES (?, ?, ?, 'main')",
            [(1, "one", str(self.one)), (2, "two", str(self.two))],
        )
        self.conn.executemany(
            "INSERT INTO files(id, repo_id, path, language) VALUES (?, ?, ?, 'yaml')",
            [
                (11, 1, "app/source/openapispec/ap/paths/bill.s1.api.yaml"),
                (21, 2, "app/source/openapispec/ap/paths/bill.s1.api.yaml"),
            ],
        )
        self.conn.execute(
            """INSERT INTO openapispec_index(repo_id, file_id, file_path, state)
               VALUES (2, 21, 'app/source/openapispec/ap/paths/bill.s1.api.yaml', 'active')"""
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_scan_rebuilds_only_selected_repository_index(self) -> None:
        scan_openapispec.scan_openapispec(self.conn, self.one, repo_id=1)
        rows = self.conn.execute(
            "SELECT repo_id, file_id FROM openapispec_index ORDER BY repo_id"
        ).fetchall()
        self.assertEqual(
            [(row["repo_id"], row["file_id"]) for row in rows], [(1, 11), (2, 21)]
        )

    def test_workflow_path_resolution_and_mapping_are_repo_scoped(self) -> None:
        self.assertEqual(
            build_workflows._resolve_file_id(
                self.conn, 1, "app/source/openapispec/ap/paths/bill.s1.api.yaml"
            )[0],
            11,
        )
        self.assertEqual(
            build_workflows._resolve_file_id(
                self.conn, 2, "app/source/openapispec/ap/paths/bill.s1.api.yaml"
            )[0],
            21,
        )
        self.assertTrue(
            link_openapispec._insert_mapping(
                self.conn, 1, 1, 11, "openapispec_paths", "same"
            )
        )
        self.assertTrue(
            link_openapispec._insert_mapping(
                self.conn, 2, 1, 21, "openapispec_paths", "same"
            )
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM entity_mappings").fetchone()[0], 2
        )

    def test_rest_endpoint_insert_does_not_cross_repository_boundaries(self) -> None:
        endpoint = [("GET", "/bills", 11, None, None)]
        self.assertEqual(
            build_rest_endpoints._insert_endpoints(self.conn, 1, endpoint), 1
        )
        self.assertEqual(
            build_rest_endpoints._insert_endpoints(self.conn, 1, endpoint), 0
        )
        self.assertEqual(
            build_rest_endpoints._insert_endpoints(self.conn, 2, endpoint), 1
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM rest_endpoints").fetchone()[0], 2
        )


if __name__ == "__main__":
    unittest.main()
