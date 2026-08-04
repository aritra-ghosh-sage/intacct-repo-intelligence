from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalog.rest_automation_contract import resolve_contract_v1_paths
from scripts.build_gherkin_coverage import (
    ContractV1ExtractionError,
    build,
    contract_v1_endpoint_match,
    parse_feature_contract_v1,
)


def _mapping(path: str = "/objects/accounts-payable/account/{key}") -> list[dict[str, str]]:
    return [
        {
            "coverage_scope": "endpoint",
            "path": path,
            "token": "account",
        }
    ]


def _inventory(text: str) -> list[dict[str, str]]:
    return [{"sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}]


class GherkinContractV1Tests(unittest.TestCase):
    def _feature(self, root: Path, text: str) -> Path:
        path = root / "account.feature"
        path.write_text(text, encoding="utf-8")
        return path

    def test_examples_are_substituted_before_exact_token_mapping_and_method_normalization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Scenario Outline: read <token>
    When \"get\" to \"<token>\" with key \"42\" and file \"ignored.json\"
    Examples:
      | token |
      | account |
""",
            )
            cases = parse_feature_contract_v1(feature, _mapping(), [])
        request = cases[0].requests[0]
        self.assertEqual("GET", request.method)
        self.assertEqual("account", request.object_token)
        self.assertEqual("/objects/accounts-payable/account/{key}", request.normalized_path)

    def test_only_anchored_grammars_and_inventory_steps_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = self._feature(
                root,
                """@version:v1
Feature: Account
  Scenario: malformed
    When \"GET\" to account
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "request-shaped"):
                parse_feature_contract_v1(feature, _mapping(), [])

            feature = self._feature(
                root,
                """@version:v1
Feature: Account
  Scenario: setup
    Given I configure a tenant
    When I Read \"account\"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "absent from non-request inventory"):
                parse_feature_contract_v1(feature, _mapping(), [])
            cases = parse_feature_contract_v1(
                feature, _mapping(), _inventory("I configure a tenant")
            )
        self.assertEqual("GET", cases[0].requests[0].method)

    def test_direct_contract_inventory_input_must_bind_text_to_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Scenario: setup
    Given I configure a tenant
    When "GET" to "account"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "does not bind text"):
                parse_feature_contract_v1(
                    feature,
                    _mapping(),
                    [{"sha256": "0" * 64, "text": "I configure a tenant"}],
                )

    def test_background_cannot_be_request_shaped_or_inventory_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Background:
    Given I configure a tenant
  Scenario: read
    When \"GET\" to \"account\"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "Background cannot reference"):
                parse_feature_contract_v1(
                    feature, _mapping("/objects/accounts-payable/account"), _inventory("I configure a tenant")
                )

            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Background:
    Given GET to "account"
  Scenario: read
    When "GET" to "account"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "request-shaped"):
                parse_feature_contract_v1(
                    feature, _mapping("/objects/accounts-payable/account"), []
                )

            for malformed_background in (
                "GET to account",
                "GETT to account",
                "I retrieve account",
            ):
                with self.subTest(background=malformed_background):
                    feature = self._feature(
                        Path(directory),
                        "@version:v1\nFeature: Account\n  Background:\n"
                        f"    Given {malformed_background}\n"
                        "  Scenario: read\n"
                        '    When "GET" to "account"\n',
                    )
                    with self.assertRaisesRegex(
                        ContractV1ExtractionError, "request-shaped"
                    ):
                        parse_feature_contract_v1(
                            feature, _mapping("/objects/accounts-payable/account"), []
                        )

            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Background:
    Given I retrieve "account"
  Scenario: read
    When "GET" to "account"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "request-shaped"):
                parse_feature_contract_v1(
                    feature, _mapping("/objects/accounts-payable/account"), []
                )

            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Background:
    Given GETT to "account"
  Scenario: read
    When "GET" to "account"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "request-shaped"):
                parse_feature_contract_v1(
                    feature, _mapping("/objects/accounts-payable/account"), []
                )

            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Background:
    Given <setup>
  Scenario Outline: read
    When "GET" to "account"
    Examples:
      | setup |
      | I configure a tenant |
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "Examples placeholders"):
                parse_feature_contract_v1(
                    feature,
                    _mapping("/objects/accounts-payable/account"),
                    _inventory("I configure a tenant"),
                )

    def test_unresolved_tokens_and_missing_or_conflicting_versions_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = self._feature(
                root,
                """Feature: Account
  Scenario: missing version
    When \"GET\" to \"account\"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "missing version"):
                parse_feature_contract_v1(feature, _mapping(), [])
            feature = self._feature(
                root,
                """@version:v1
Feature: Account
  Scenario: unresolved
    When \"GET\" to \"<missing>\"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "unresolved Examples token"):
                parse_feature_contract_v1(feature, _mapping(), [])
            feature = self._feature(
                root,
                """@version:v1
Feature: Account
  Scenario: conflicting version
    When \"GET\" to \"account\" for version \"s1\"
""",
            )
            with self.assertRaisesRegex(ContractV1ExtractionError, "conflicting version"):
                parse_feature_contract_v1(feature, _mapping(), [])

    def test_inventory_text_is_an_exact_post_substitution_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            feature = self._feature(
                Path(directory),
                """@version:v1
Feature: Account
  Scenario: setup
    Given I configure two tenants
    When "GET" to "account"
""",
            )
            with self.assertRaisesRegex(
                ContractV1ExtractionError, "absent from non-request inventory"
            ):
                parse_feature_contract_v1(
                    feature, _mapping(), _inventory("I configure a tenant")
                )

    def test_contract_v1_reads_no_fixture_or_credential_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = self._feature(
                root,
                """@version:v1
Feature: Account
  Scenario: read
    When "GET" to "account"
""",
            )
            feature.with_suffix(".properties").write_text(
                "version=v1\npassword=must-not-be-retained\n", encoding="utf-8"
            )
            fixture = root / "fixture.json"
            fixture.write_text("not JSON and must not be parsed", encoding="utf-8")
            credential = root / "credentials.json"
            credential.write_text("not JSON and must not be parsed", encoding="utf-8")
            original_read_text = Path.read_text

            def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
                if path in {fixture, credential}:
                    raise AssertionError(f"unexpected sensitive read: {path.name}")
                return original_read_text(path, *args, **kwargs)

            with mock.patch.object(Path, "read_text", guarded_read_text):
                cases = parse_feature_contract_v1(feature, _mapping(), [])
        self.assertEqual("v1", cases[0].requests[0].version)
        self.assertNotIn("password", repr(cases))

    def test_endpoint_match_hard_diagnostic_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features = root / "features"
            features.mkdir()
            (features / "account.feature").write_text(
                """@version:v1
Feature: Account
  Scenario: read
    When "GET" to "account"
""",
                encoding="utf-8",
            )
            (root / "mapping.json").write_text(
                '{"contract_version":1,"mappings":[{"coverage_scope":"endpoint",'
                '"path":"/objects/accounts/account","token":"account"}]}',
                encoding="utf-8",
            )
            (root / "compatibility.json").write_text(
                '{"bridges":[],"contract_version":1}', encoding="utf-8"
            )
            (root / "inventory.json").write_text(
                '{"contract_version":1,"entries":[]}', encoding="utf-8"
            )
            paths = resolve_contract_v1_paths(
                {
                    "features_root": "features",
                    "object_mapping": "mapping.json",
                    "version_compatibility": "compatibility.json",
                    "non_request_inventory": "inventory.json",
                },
                root,
            )
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript((Path(__file__).parents[1] / "catalog" / "schema.sql").read_text())
            conn.executescript(
                """INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES
                       ('ia-main','/main','main'),('suite','/suite','main');"""
            )
            messages = []
            for _ in range(2):
                with self.assertRaises(ContractV1ExtractionError) as raised:
                    build(
                        conn,
                        "suite",
                        root,
                        paths.object_mapping,
                        paths.features_root,
                        contract_v1_paths=paths,
                    )
                messages.append(str(raised.exception))
        self.assertEqual(
            [
                "Contract-V1 hard diagnostic: endpoint_match_unresolved "
                "line=4 method=GET path=/objects/accounts/account version=v1"
            ]
            * 2,
            messages,
        )

    def test_endpoint_matching_requires_one_exact_path_version_or_explicit_bridge(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """CREATE TABLE rest_endpoints(
                id INTEGER, repo_id INTEGER, method TEXT, path TEXT,
                source_version TEXT, entity_id INTEGER
            );
            INSERT INTO rest_endpoints VALUES
                (1, 1, 'GET', '/objects/accounts-payable/account/{key}', 's1', 7);
            """
        )
        exact = contract_v1_endpoint_match(
            conn,
            1,
            "GET",
            "/objects/accounts-payable/account/{key}",
            "s1",
            [],
        )
        self.assertIsNotNone(exact)
        self.assertEqual(1, exact[0]["id"])
        self.assertIsNone(exact[1])
        self.assertEqual(
            "compatible",
            contract_v1_endpoint_match(
                conn,
                1,
                "GET",
                "/objects/accounts-payable/account/{key}",
                "v1-beta2",
                [{"endpoint_version": "s1", "test_version": "v1-beta2"}],
            )[1],
        )
        self.assertIsNone(
            contract_v1_endpoint_match(
                conn,
                1,
                "GET",
                "/services/core/account",
                "s1",
                [],
            )
        )
        conn.execute(
            "INSERT INTO rest_endpoints VALUES(2, 1, 'GET', '/objects/accounts-payable/account/{key}', 's1', 7)"
        )
        self.assertIsNone(
            contract_v1_endpoint_match(
                conn,
                1,
                "GET",
                "/objects/accounts-payable/account/{key}",
                "s1",
                [],
            )
        )
        conn.close()

    def test_non_endpoint_contract_mapping_persists_request_without_endpoint_or_entity_link(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features = root / "features"
            features.mkdir()
            (features / "health.feature").write_text(
                """@version:v1
Feature: Health
  Scenario: service health
    When \"GET\" to \"health\"
""",
                encoding="utf-8",
            )
            (root / "mapping.json").write_text(
                json.dumps(
                    {
                        "contract_version": 1,
                        "mappings": [
                            {
                                "coverage_scope": "non_endpoint",
                                "path": "/services/platform/health",
                                "token": "health",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "compatibility.json").write_text(
                '{"bridges":[],"contract_version":1}', encoding="utf-8"
            )
            (root / "inventory.json").write_text(
                '{"contract_version":1,"entries":[]}', encoding="utf-8"
            )
            paths = resolve_contract_v1_paths(
                {
                    "features_root": "features",
                    "object_mapping": "mapping.json",
                    "version_compatibility": "compatibility.json",
                    "non_request_inventory": "inventory.json",
                },
                root,
            )
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            schema = (Path(__file__).parents[1] / "catalog" / "schema.sql").read_text()
            conn.executescript(schema)
            conn.execute(
                "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES('ia-main','/tmp/main','main')"
            )
            conn.execute(
                "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES('suite',?, 'main')",
                (str(root),),
            )
            stats = build(
                conn,
                "suite",
                root,
                paths.object_mapping,
                paths.features_root,
                contract_v1_paths=paths,
            )
        self.assertEqual(1, stats["requests"])
        self.assertEqual(0, stats["links"])
        self.assertEqual(
            ("/services/platform/health", "custom", "non_endpoint"),
            tuple(
                conn.execute(
                    "SELECT normalized_path,operation_kind,coverage_scope FROM test_requests"
                ).fetchone()
            ),
        )
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM test_endpoint_links").fetchone()[0])
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM test_entity_links").fetchone()[0])
        conn.close()


if __name__ == "__main__":
    unittest.main()
