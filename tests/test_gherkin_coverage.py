from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_gherkin_coverage import (
    _endpoint_matches,
    canonicalize_path,
    load_object_mapping,
    parse_feature,
    read_properties_metadata,
)


FEATURE = '''@version:v1-beta2
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
'''


class GherkinCoverageTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, str]]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        feature = root / "account.feature"
        feature.write_text(FEATURE)
        feature.with_suffix(".properties").write_text("version=v1-beta2\npassword=must-not-be-read\ntestObject=account\n")
        mapping_path = root / "object-mapping.json"
        mapping_path.write_text(json.dumps({"one": {"account": "accounts-payable/account"}}))
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
        self.assertEqual([(request.method, request.version, request.normalized_path, request.expected_status) for request in first.requests], [
            ("GET", "v0", "/objects/accounts-payable/account/{key}", 200),
            ("PATCH", "v0", "/objects/accounts-payable/account/{key}", 204),
        ])

    def test_conflicting_feature_and_properties_versions_are_diagnostic(self) -> None:
        tmp, feature, mapping = self._fixture()
        self.addCleanup(tmp.cleanup)
        feature.with_suffix(".properties").write_text("version=v3\n")
        case = parse_feature(feature, mapping)[0]
        self.assertEqual(case.versions, ())
        self.assertIn("version_conflict", {diagnostic.kind for diagnostic in case.diagnostics})

    def test_allowlist_does_not_return_sensitive_properties(self) -> None:
        tmp, feature, _ = self._fixture()
        self.addCleanup(tmp.cleanup)
        metadata, diagnostics = read_properties_metadata(feature.with_suffix(".properties"))
        self.assertEqual(metadata, {"version": "v1-beta2", "testObject": "account"})
        self.assertFalse(diagnostics)

    def test_duplicate_alias_is_not_resolvable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mapping_path = Path(directory) / "mapping.json"
            mapping_path.write_text(json.dumps({"a": {"thing": "a/thing"}, "b": {"thing": "b/thing"}}))
            mapping, diagnostics = load_object_mapping(mapping_path)
            self.assertNotIn("thing", mapping)
            self.assertEqual(diagnostics[0].kind, "duplicate_object_alias")

    def test_endpoint_matching_requires_version_or_active_compatibility(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE rest_endpoints(id INTEGER, method TEXT, path TEXT, source_version TEXT, entity_id INTEGER);
            CREATE TABLE api_version_compatibility(id INTEGER, test_version TEXT, endpoint_version TEXT, status TEXT);
            INSERT INTO rest_endpoints VALUES(1, 'GET', '/services/s1/objects/accounts-payable/account/{key}', 's1', 9);
            INSERT INTO api_version_compatibility VALUES(10, 'v0', 's1', 'active');
        """)
        self.assertEqual(canonicalize_path('/services/s1/objects/accounts-payable/account/{key}'), '/objects/accounts-payable/account/{key}')
        compatible = _endpoint_matches(conn, "GET", "/objects/accounts-payable/account/{key}", ("v0",))
        self.assertEqual([(row[0]["id"], row[1], row[2]) for row in compatible], [(1, 10, "compatible")])
        self.assertEqual(_endpoint_matches(conn, "GET", "/objects/accounts-payable/account/{key}", ("v2",)), [])


if __name__ == "__main__":
    unittest.main()
