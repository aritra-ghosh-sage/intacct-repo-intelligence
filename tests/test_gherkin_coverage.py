from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_gherkin_coverage import (
    _endpoint_matches,
    build,
    canonicalize_path,
    load_object_mapping,
    parse_feature,
    read_properties_metadata,
)
from scripts.query_rest import _coverage_rows


FEATURE = """@version:v1-beta2
@IA-100
Feature: Accounts

  @ci_only
  Scenario Outline: Read then update <testCaseID>
    When "GET" to "account" for version "v0" with key "<key>" and file "ignored.json"
    Then I verify that status code "200" in Response
    When "PATCH" to "account" with key "<key>" and file "ignored.json"
    Then response code is "204"

    Examples:
      | testCaseID | key |
      | IA-101     | 12  |
      | IA-102     | 13  |
"""


class GherkinCoverageTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, str]]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        feature = root / "account.feature"
        feature.write_text(FEATURE)
        feature.with_suffix(".properties").write_text(
            "version=v1-beta2\npassword=must-not-be-read\ntestObject=account\n"
        )
        mapping_path = root / "object-mapping.json"
        mapping_path.write_text(
            json.dumps({"one": {"account": "accounts-payable/account"}})
        )
        mapping, diagnostics = load_object_mapping(mapping_path)
        self.assertFalse(diagnostics)
        return tmp, feature, mapping

    def test_outline_expands_requests_and_keeps_sequential_version_state(self) -> None:
        tmp, feature, mapping = self._fixture()
        self.addCleanup(tmp.cleanup)
        cases = parse_feature(feature, mapping)
        self.assertEqual([case.case_name for case in cases], ["IA-101", "IA-102"])
        first = cases[0]
        self.assertEqual(first.eligibility, "ci_only")
        self.assertEqual(first.jira_refs, ("IA-100",))
        self.assertEqual(
            [
                (
                    request.method,
                    request.version,
                    request.normalized_path,
                    request.expected_status,
                )
                for request in first.requests
            ],
            [
                ("GET", "v0", "/objects/accounts-payable/account/{key}", 200),
                ("PATCH", "v0", "/objects/accounts-payable/account/{key}", 204),
            ],
        )

    def test_conflicting_feature_and_properties_versions_are_diagnostic(self) -> None:
        tmp, feature, mapping = self._fixture()
        self.addCleanup(tmp.cleanup)
        feature.with_suffix(".properties").write_text("version=v3\n")
        case = parse_feature(feature, mapping)[0]
        self.assertEqual(case.versions, ())
        self.assertIn(
            "version_conflict", {diagnostic.kind for diagnostic in case.diagnostics}
        )

    def test_allowlist_does_not_return_sensitive_properties(self) -> None:
        tmp, feature, _ = self._fixture()
        self.addCleanup(tmp.cleanup)
        metadata, diagnostics = read_properties_metadata(
            feature.with_suffix(".properties")
        )
        self.assertEqual(metadata, {"version": "v1-beta2", "testObject": "account"})
        self.assertFalse(diagnostics)

    def test_duplicate_alias_is_not_resolvable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mapping_path = Path(directory) / "mapping.json"
            mapping_path.write_text(
                json.dumps({"a": {"thing": "a/thing"}, "b": {"thing": "b/thing"}})
            )
            mapping, diagnostics = load_object_mapping(mapping_path)
            self.assertNotIn("thing", mapping)
            self.assertEqual(diagnostics[0].kind, "duplicate_object_alias")

    def test_wrapped_parent_alias_resolves_to_a_child_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = root / "child.feature"
            feature.write_text(
                """Feature: Child
  Scenario: Read child
    When "GET" to child "child-object" with key "child-key" for parent "{{dummy-parent}}" with key "parent-key" and file ""
""",
                encoding="utf-8",
            )
            cases = parse_feature(
                feature,
                {
                    "dummy-parent": "accounts-payable/parent",
                    "child-object": "accounts-payable/child",
                },
            )
        request = cases[0].requests[0]
        self.assertEqual("child", request.operation_kind)
        self.assertEqual(
            "/objects/accounts-payable/parent/{key}/accounts-payable/child",
            request.normalized_path,
        )
        self.assertNotIn(
            "unresolved_parent",
            {diagnostic.kind for diagnostic in cases[0].diagnostics},
        )

    def test_endpoint_matching_requires_version_or_active_compatibility(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE rest_endpoints(id INTEGER, repo_id INTEGER, method TEXT, path TEXT, source_version TEXT, entity_id INTEGER);
            CREATE TABLE api_version_compatibility(id INTEGER, test_version TEXT, endpoint_version TEXT, status TEXT);
            INSERT INTO rest_endpoints VALUES(1, 1, 'GET', '/services/s1/objects/accounts-payable/account/{key}', 's1', 9);
            INSERT INTO api_version_compatibility VALUES(10, 'v0', 's1', 'active');
        """)
        self.assertEqual(
            canonicalize_path("/services/s1/objects/accounts-payable/account/{key}"),
            "/objects/accounts-payable/account/{key}",
        )
        compatible = _endpoint_matches(
            conn,
            1,
            "GET",
            "/objects/accounts-payable/account/{key}",
            ("v0",),
        )
        self.assertEqual(
            [(row[0]["id"], row[1], row[2]) for row in compatible],
            [(1, 10, "compatible")],
        )
        self.assertEqual(
            _endpoint_matches(
                conn, 1, "GET", "/objects/accounts-payable/account/{key}", ("v2",)
            ),
            [],
        )

    def _database(self, root: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema = (Path(__file__).parents[1] / "catalog" / "schema.sql").read_text()
        conn.executescript(schema)
        production_repo_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('ia-main', ?, 'main')",
            (str(root / "main"),),
        ).lastrowid
        conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('suite-a', ?, 'main')",
            (str(root),),
        )
        endpoint_file_id = conn.execute(
            "INSERT INTO files(repo_id, path, language) VALUES (?, 'openapi/account.yaml', 'yaml')",
            (production_repo_id,),
        ).lastrowid
        entity_id = conn.execute(
            "INSERT INTO entity_nodes(name) VALUES ('Account')"
        ).lastrowid
        conn.execute(
            """
            INSERT INTO rest_endpoints(repo_id, method, path, source_version, entity_id, file_id)
            VALUES (?, 'POST', '/objects/accounts-payable/account', 'v1', ?, ?)
            """,
            (production_repo_id, entity_id, endpoint_file_id),
        )
        conn.commit()
        return conn

    def _mapping(self, root: Path) -> Path:
        mapping = root / "object-mapping.json"
        mapping.write_text(
            json.dumps({"accounts": {"account": "accounts-payable/account"}}),
            encoding="utf-8",
        )
        return mapping

    def test_known_issue_links_but_is_not_active_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = root / "account.feature"
            feature.write_text(
                """@version:v1
Feature: Account
  @knownIssue
  Scenario: Create account
    When "POST" to "account" with key "" and file ""
""",
                encoding="utf-8",
            )
            conn = self._database(root)
            self.addCleanup(conn.close)
            build(conn, "suite-a", root, self._mapping(root), root)
            entity_id = conn.execute(
                "SELECT id FROM entity_nodes WHERE name = 'Account'"
            ).fetchone()[0]
            endpoints, _diagnostics = _coverage_rows(conn, entity_id, "v1", 10)
        self.assertEqual(
            1, conn.execute("SELECT COUNT(*) FROM test_endpoint_links").fetchone()[0]
        )
        self.assertEqual("known_issue_only", endpoints[0]["coverage"])
        self.assertEqual(0, endpoints[0]["active_case_count"])

    def test_version_conflict_blocks_explicit_override_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = root / "account.feature"
            feature.write_text(
                """@version:v1
Feature: Account
  Scenario: Create account
    When "POST" to "account" for version "v1" with key "" and file ""
""",
                encoding="utf-8",
            )
            feature.with_suffix(".properties").write_text(
                "version=v2\n", encoding="utf-8"
            )
            conn = self._database(root)
            self.addCleanup(conn.close)
            build(conn, "suite-a", root, self._mapping(root), root)
        self.assertEqual(
            1, conn.execute("SELECT COUNT(*) FROM test_requests").fetchone()[0]
        )
        self.assertEqual(
            3,
            conn.execute("SELECT COUNT(*) FROM test_case_versions").fetchone()[0],
        )
        self.assertEqual(
            {"feature_tag", "properties", "request_override"},
            {
                row[0]
                for row in conn.execute(
                    "SELECT source_kind FROM test_case_versions ORDER BY source_kind"
                ).fetchall()
            },
        )
        self.assertEqual(
            1,
            conn.execute(
                "SELECT COUNT(*) FROM test_case_versions WHERE source_kind = 'request_override'"
            ).fetchone()[0],
        )
        self.assertEqual(
            0, conn.execute("SELECT COUNT(*) FROM test_endpoint_links").fetchone()[0]
        )
        self.assertEqual(
            0, conn.execute("SELECT COUNT(*) FROM test_entity_links").fetchone()[0]
        )
        self.assertEqual(
            1,
            conn.execute(
                "SELECT COUNT(*) FROM test_diagnostics WHERE kind = 'version_conflict'"
            ).fetchone()[0],
        )

    def test_orphan_properties_is_diagnostic_without_case_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "account.feature").write_text(
                """@version:v1
Feature: Account
  Scenario: Create account
    When "POST" to "account" with key "" and file ""
""",
                encoding="utf-8",
            )
            (root / "orphan.properties").write_text(
                "password=must-not-be-retained\n",
                encoding="utf-8",
            )
            conn = self._database(root)
            self.addCleanup(conn.close)
            original_read_bytes = Path.read_bytes

            def _guard(self: Path) -> bytes:
                if self.name == "orphan.properties":
                    raise AssertionError(
                        "orphan.properties should not be read for contents"
                    )
                return original_read_bytes(self)

            with patch("pathlib.Path.read_bytes", autospec=True, side_effect=_guard):
                build(conn, "suite-a", root, self._mapping(root), root)
                build(conn, "suite-a", root, self._mapping(root), root)
        orphan = conn.execute(
            "SELECT test_case_id, message FROM test_diagnostics WHERE kind = 'orphan_properties'"
        ).fetchone()
        self.assertEqual(
            (None, "No same-stem feature file for: orphan.properties"), tuple(orphan)
        )
        self.assertEqual(
            1, conn.execute("SELECT COUNT(*) FROM test_cases").fetchone()[0]
        )
        self.assertIsNone(
            conn.execute(
                "SELECT sha1 FROM files WHERE path = 'orphan.properties'"
            ).fetchone()[0]
        )
        self.assertEqual(
            0,
            conn.execute(
                """
                SELECT COUNT(*)
                FROM test_case_versions tcv
                JOIN files f ON f.id = tcv.source_file_id
                WHERE f.path = 'orphan.properties'
                """
            ).fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
