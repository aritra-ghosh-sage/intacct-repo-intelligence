from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from catalog.repositories import RepositoryError, load_workspace_manifest
from catalog.rest_automation_contract import (
    RestAutomationContractError,
    audit_contract_v1,
    load_non_request_inventory_contract,
    load_object_mapping_contract,
    load_version_compatibility_contract,
    resolve_contract_v1_paths,
)
from scripts.audit_rest_automation_contract import main


class RestAutomationContractTests(unittest.TestCase):
    def _write_contract(self, root: Path) -> tuple[Path, Path, Path]:
        (root / "features").mkdir()
        mapping = root / "mapping.json"
        compatibility = root / "compatibility.json"
        inventory = root / "inventory.json"
        mapping.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "mappings": [
                        {
                            "coverage_scope": "endpoint",
                            "path": "/objects/accounts-payable/account",
                            "token": "account",
                        },
                        {
                            "coverage_scope": "non_endpoint",
                            "path": "/services/platform/health",
                            "token": "health",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        compatibility.write_text(
            json.dumps(
                {
                    "bridges": [
                        {"endpoint_version": "s1", "test_version": "v1-beta2"}
                    ],
                    "contract_version": 1,
                }
            ),
            encoding="utf-8",
        )
        text = "I configure a tenant"
        inventory.write_text(
            json.dumps(
                {
                    "contract_version": 1,
                    "entries": [
                        {
                            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "text": text,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return mapping, compatibility, inventory

    def test_contract_v1_manifest_requires_existing_in_root_contract_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_contract(root)
            manifest = root / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: suite\n"
                f"    local_root: {root}\n"
                "    tracked_branch: main\n"
                "    profile: rest_automation\n"
                "    rest_automation:\n"
                "      coverage_contract_version: 1\n"
                "      features_root: features\n"
                "      object_mapping: mapping.json\n"
                "      version_compatibility: compatibility.json\n"
                "      non_request_inventory: inventory.json\n",
                encoding="utf-8",
            )
            loaded = load_workspace_manifest(manifest)
            contract = loaded["repositories"][0]["rest_automation"]
            self.assertEqual(1, contract["coverage_contract_version"])

            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "inventory.json", "../inventory.json"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RepositoryError, "stay inside local_root"):
                load_workspace_manifest(manifest)

            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    "../inventory.json", "missing.json"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RepositoryError, "file does not exist"):
                load_workspace_manifest(manifest)

    def test_legacy_contract_v0_remains_valid_without_contract_v1_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: ia-restapi-automation\n"
                f"    local_root: {root / 'absent-target'}\n"
                "    tracked_branch: main\n"
                "    enabled: false\n"
                "    profile: rest_automation\n"
                "    rest_automation:\n"
                "      features_root: features\n"
                "      object_mapping: object-mapping.json\n",
                encoding="utf-8",
            )
            contract = load_workspace_manifest(manifest)["repositories"][0][
                "rest_automation"
            ]
            self.assertEqual(0, contract["coverage_contract_version"])

    def test_manifest_allows_greenfield_analysis_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: ia-restapi-automation-tests\n"
                f"    local_root: {root / 'target'}\n"
                "    tracked_branch: main\n"
                "    enabled: true\n"
                "    profile: rest_automation\n"
                "    greenfield_analysis:\n"
                "      role: test\n"
                "      discovery_eligible: true\n"
                "      test_roots:\n"
                "        - features\n"
                "      test_formats:\n"
                "        - gherkin\n"
                "    rest_automation:\n"
                "      features_root: features\n"
                "      object_mapping: object-mapping.json\n",
                encoding="utf-8",
            )
            entry = load_workspace_manifest(manifest)["repositories"][0]
            self.assertEqual("test", entry["greenfield_analysis"]["role"])
            self.assertTrue(entry["greenfield_analysis"]["discovery_eligible"])

    def test_contract_v1_json_rejects_duplicate_unknown_wrong_type_and_noncanonical_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping, compatibility, inventory = self._write_contract(root)
            self.assertEqual("account", load_object_mapping_contract(mapping)[0]["token"])
            self.assertEqual(
                "v1-beta2",
                load_version_compatibility_contract(compatibility)[0]["test_version"],
            )
            self.assertEqual(
                "I configure a tenant",
                load_non_request_inventory_contract(inventory)[0]["text"],
            )

            mapping.write_text(
                '{"contract_version":1,"contract_version":1,"mappings":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RestAutomationContractError, "duplicate JSON key"):
                load_object_mapping_contract(mapping)

            compatibility.write_text(
                '{"contract_version":1,"bridges":[],"unexpected":true}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RestAutomationContractError, "unknown field"):
                load_version_compatibility_contract(compatibility)

            inventory.write_text(
                '{"contract_version":"1","entries":[]}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RestAutomationContractError, "must be the integer 1"):
                load_non_request_inventory_contract(inventory)

            mapping.write_text(
                json.dumps(
                    {
                        "contract_version": 1,
                        "mappings": [
                            {
                                "coverage_scope": "endpoint",
                                "path": "/services/core/account",
                                "token": "account",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RestAutomationContractError, "endpoint scope"):
                load_object_mapping_contract(mapping)

            mapping.write_text(
                json.dumps(
                    {
                        "contract_version": 1,
                        "mappings": [
                            {
                                "coverage_scope": "endpoint",
                                "path": "/objects/",
                                "token": "account",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RestAutomationContractError, "endpoint scope"):
                load_object_mapping_contract(mapping)

            mapping.write_text(
                '{"mappings":[],"contract_version":1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RestAutomationContractError, "lexical order"):
                load_object_mapping_contract(mapping)

            mapping.write_text(
                '{"contract_version":1,"mappings":[{"token":"account","path":"/objects/accounts-payable/account","coverage_scope":"endpoint"}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RestAutomationContractError, "lexical order"):
                load_object_mapping_contract(mapping)

    def test_audit_reports_all_three_strictly_validated_source_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_contract(root)
            paths = resolve_contract_v1_paths(
                {
                    "features_root": "features",
                    "object_mapping": "mapping.json",
                    "version_compatibility": "compatibility.json",
                    "non_request_inventory": "inventory.json",
                },
                root,
            )
            audit = audit_contract_v1(paths)
            self.assertEqual(
                ["object_mapping", "version_compatibility", "non_request_inventory"],
                [item.field for item in audit],
            )
            result = CliRunner().invoke(
                main,
                [
                    "--suite-root",
                    str(root),
                    "--object-mapping",
                    "mapping.json",
                    "--version-compatibility",
                    "compatibility.json",
                    "--non-request-inventory",
                    "inventory.json",
                ],
            )
            self.assertEqual(0, result.exit_code, result.output)
            self.assertEqual("ok", json.loads(result.output)["status"])

    def test_manifest_audit_rejects_legacy_contract_v0_without_claiming_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: ia-restapi-automation\n"
                f"    local_root: {root / 'target'}\n"
                "    tracked_branch: main\n"
                "    enabled: false\n"
                "    profile: rest_automation\n"
                "    rest_automation:\n"
                "      features_root: features\n"
                "      object_mapping: object-mapping.json\n",
                encoding="utf-8",
            )
            result = CliRunner().invoke(
                main, ["--manifest", str(manifest), "--repo", "ia-restapi-automation"]
            )
            self.assertNotEqual(0, result.exit_code)
            self.assertIn("does not declare Contract-V1", result.output)


if __name__ == "__main__":
    unittest.main()
