from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ia_repomap_builder.test_inventory import (
    build_test_inventory,
    persist_test_inventory,
)


class TestInventoryTests(unittest.TestCase):
    def make_repo(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repo"
        root.mkdir()
        (root / "features/gl/input/gl-statistical-account").mkdir(parents=True)
        (root / "features/gl/output/gl-statistical-account").mkdir(parents=True)
        (root / "features/gl/gl-statistical.feature").write_text(
            """@gl\nFeature: Statistical accounts\n\n@gl @IA-123 @regression\nScenario: Create statistical account\n  When \"POST\" to \"general-ledger/statistical-account\" with key \"account\" and file \"create-gl-statistical-account.json\" get variable \"\"\n""",
            encoding="utf-8",
        )
        (root / "features/gl/input/gl-statistical-account/create-gl-statistical-account.json").write_text(
            '{"object": "general-ledger/statistical-account"}\n', encoding="utf-8"
        )
        (root / "features/gl/output/gl-statistical-account/res-create.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
        return root, Path(temporary.name) / "artifacts"

    def test_discovery_is_deterministic_and_groups_input_output(self) -> None:
        root, _ = self.make_repo()
        first = build_test_inventory(root)
        second = build_test_inventory(root)

        self.assertEqual(first.status, "ok")
        self.assertEqual(first.as_dict(), second.as_dict())
        suite = next(item for item in first.suites if item.suite_id.endswith("gl-statistical-account"))
        self.assertEqual(suite.category, "fixture")
        self.assertEqual(len(suite.input_files), 1)
        self.assertEqual(len(suite.output_files), 1)
        self.assertEqual(suite.api_objects, ("general-ledger/statistical-account",))
        self.assertEqual(suite.methods, ())

        feature_suite = next(item for item in first.suites if item.suite_id == "features/gl")
        self.assertEqual(feature_suite.category, "executable")
        self.assertEqual(feature_suite.scenarios[0].name, "Create statistical account")
        self.assertEqual(feature_suite.scenarios[0].tags, ("@IA-123", "@gl", "@regression"))
        self.assertEqual(feature_suite.scenarios[0].methods, ("POST",))

    def test_dirty_repository_is_not_ok(self) -> None:
        root, _ = self.make_repo()
        (root / "features/gl/input/new.json").write_text("{}\n", encoding="utf-8")
        result = build_test_inventory(root)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.repository.dirty)

    def test_multiple_scenarios_keep_separate_metadata(self) -> None:
        root, _ = self.make_repo()
        feature = root / "features/gl/gl-statistical.feature"
        feature.write_text(
            """@gl
Feature: Statistical accounts

@gl @IA-1 @sanity
Scenario: Create account
  When \"POST\" to \"account\" with key \"account\" and file \"create.json\" get variable \"\"

@gl @IA-2 @regression
Scenario: Update account
  When \"PATCH\" to \"account\" with key \"account\" and file \"update.json\" get variable \"\"
""",
            encoding="utf-8",
        )
        result = build_test_inventory(root)
        feature_suite = next(item for item in result.suites if item.suite_id == "features/gl")
        self.assertEqual([item.name for item in feature_suite.scenarios], ["Create account", "Update account"])
        self.assertEqual(feature_suite.scenarios[0].methods, ("POST",))
        self.assertEqual(feature_suite.scenarios[1].methods, ("PATCH",))
        self.assertIn("@IA-1", feature_suite.scenarios[0].tags)
        self.assertNotIn("@IA-2", feature_suite.scenarios[0].tags)

    def test_malformed_json_is_reported(self) -> None:
        root, _ = self.make_repo()
        malformed = root / "features/gl/input/gl-statistical-account/bad.json"
        malformed.write_text("not json\n", encoding="utf-8")
        result = build_test_inventory(root)
        self.assertIn("file_unreadable", {gap.kind for gap in result.gaps})
        self.assertTrue(any(gap.path == "features/gl/input/gl-statistical-account/bad.json" for gap in result.gaps))

    def test_missing_features_directory_is_reported(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        result = build_test_inventory(Path(temporary.name))
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.gaps[0].kind, "features_directory_missing")

    def test_persistence_is_revision_addressed_and_external(self) -> None:
        root, artifact_root = self.make_repo()
        inventory = build_test_inventory(root)
        persisted = persist_test_inventory(inventory, artifact_root)

        self.assertTrue(persisted.inventory_path.is_file())
        self.assertTrue(persisted.manifest_path.is_file())
        self.assertEqual(persisted.inventory_path.parent.parent.name, inventory.repository.head)
        payload = json.loads(persisted.inventory_path.read_text(encoding="utf-8"))
        manifest = json.loads(persisted.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["repository"]["head"], inventory.repository.head)
        self.assertEqual(manifest["inventory_digest"], inventory.repository.inventory_digest)
        self.assertEqual(manifest["inventory_sha256"], persisted.manifest["inventory_sha256"])

        with self.assertRaises(FileExistsError):
            persist_test_inventory(inventory, artifact_root)

        with self.assertRaises(ValueError):
            persist_test_inventory(inventory, root / "nested-artifacts")


if __name__ == "__main__":
    unittest.main()
