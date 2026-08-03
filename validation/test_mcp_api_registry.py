"""Focused MCP adapter tests for exact API Registry evidence."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from intacct_mcp.server import CatalogState, api_registry_impl
from scripts.query_api_registry import (
    query_api_registry_file,
    query_api_registry_issues,
    query_api_registry_releases,
    query_api_registry_resource,
)

ROOT = Path(__file__).resolve().parents[1]


class McpApiRegistryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "catalog.db"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript((ROOT / "catalog" / "schema.sql").read_text())
            conn.executescript(
                """
                INSERT INTO repos(id, repo_key, local_root, tracked_branch)
                    VALUES (1, 'ia-main', '/repo/main', 'main');
                INSERT INTO files(id, repo_id, path, language) VALUES
                    (1, 1, 'app/source/api/registries/RegistryV1.json', 'json'),
                    (2, 1, 'app/source/openapispec/ap/bill.s1.yaml', 'yaml');
                INSERT INTO api_registry_entries(
                    id, repo_id, registry_release, registry_file_id, json_pointer,
                    module, resource_kind, resource_path, revision, declared_hash,
                    api_type, runtime_owner, ui_metadata_hash, source_optional, payload_json
                ) VALUES (
                    10, 1, 'V1', 1, '/accounts-payable/objects/bill',
                    'accounts-payable', 'objects', 'bill', 's1', 'abc',
                    'rootObject', 'php', 'ui1', 0, '{"type":"rootObject"}'
                );
                INSERT INTO api_registry_entry_links(
                    repo_id, entry_id, source_file_id, source_pointer, link_kind,
                    component_hash, evidence_json
                ) VALUES (
                    1, 10, 2, '/components/schemas/Bill', 'openapi_component',
                    'abc', '{"matched":true}'
                );
                INSERT INTO api_registry_issues(
                    repo_id, entry_id, source_file_id, source_pointer, issue_key,
                    severity, issue_code, message, details_json
                ) VALUES (
                    1, 10, 1, '/accounts-payable/objects/bill', 'v1-bill-warning',
                    'warning', 'hash_mismatch', 'Hash differs', '{"expected":"abc"}'
                );
                """
            )
            conn.commit()
        finally:
            conn.close()
        self.state = CatalogState(self.db_path, Path(self.tempdir.name) / "graph.lbug")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _cli_data(self, query, **kwargs: object) -> dict[str, object]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return query(conn, **kwargs)
        finally:
            conn.close()

    def test_each_operation_preserves_shared_cli_evidence_content(self) -> None:
        cases = (
            (
                "releases",
                query_api_registry_releases,
                {"repo_key": "ia-main", "release": "V1", "limit": 1, "cursor": None},
            ),
            (
                "resource",
                query_api_registry_resource,
                {
                    "repo_key": "ia-main",
                    "release": "V1",
                    "module": "accounts-payable",
                    "resource_kind": "objects",
                    "resource_path": "bill",
                    "limit": 1,
                    "cursor": None,
                },
            ),
            (
                "file",
                query_api_registry_file,
                {
                    "repo_key": "ia-main",
                    "file_path": "app/source/api/registries/RegistryV1.json",
                    "release": "V1",
                    "limit": 1,
                    "cursor": None,
                },
            ),
            (
                "issues",
                query_api_registry_issues,
                {"repo_key": "ia-main", "release": "V1", "limit": 1, "cursor": None},
            ),
        )
        for operation, cli_query, kwargs in cases:
            with self.subTest(operation=operation):
                expected = self._cli_data(cli_query, **kwargs)
                response = api_registry_impl(self.state, operation, **kwargs)
                self.assertEqual("ok", response["status"])
                self.assertEqual("api_registry", response["operation"])
                self.assertEqual(expected, response["data"])
                self.assertEqual(expected["page"], response["page"])

    def test_repository_and_registry_table_errors_stay_native_to_mcp(self) -> None:
        missing_repo = api_registry_impl(self.state, "issues", repo_key="missing")
        self.assertEqual("error", missing_repo["status"])
        self.assertEqual("repository_not_found", missing_repo["error"]["code"])
        self.assertEqual({"repo_key": "missing"}, missing_repo["error"]["details"])

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DROP TABLE api_registry_issues")
            conn.commit()
        finally:
            conn.close()
        unavailable = api_registry_impl(self.state, "releases", repo_key="ia-main")
        self.assertEqual("error", unavailable["status"])
        self.assertEqual("api_registry_unavailable", unavailable["error"]["code"])
        self.assertIn("api_registry_issues", unavailable["error"]["details"]["missing_tables"])

    def test_operation_specific_required_inputs_fail_before_querying(self) -> None:
        resource = api_registry_impl(
            self.state, "resource", repo_key="ia-main", release="V1"
        )
        self.assertEqual("missing_required_parameters", resource["error"]["code"])
        self.assertEqual(
            ["module", "resource_kind", "resource_path"],
            resource["error"]["details"]["missing_parameters"],
        )

        file_response = api_registry_impl(self.state, "file", repo_key="ia-main")
        self.assertEqual("missing_required_parameters", file_response["error"]["code"])
        self.assertEqual(["file_path"], file_response["error"]["details"]["missing_parameters"])


if __name__ == "__main__":
    unittest.main()
