from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ia_repomap_builder.pr_analysis import TestArea
from ia_repomap_builder.pr_test_coverage import (
    MAX_MATCHED_SUITE_IDS,
    MAX_SUGGESTED_STUBS,
    evaluate_test_coverage,
    load_persisted_test_inventory,
    render_suggested_stub,
)
from ia_repomap_builder.test_inventory import (
    InventoryGap,
    InventoryRepository,
    InventoryScenario,
    InventorySuite,
    TestInventory,
    build_test_inventory,
    persist_test_inventory,
)


def _test_area(area: str, paths: list[str]) -> TestArea:
    return TestArea(
        area=area,
        paths=paths,
        reason="test fixture reason",
        confidence="candidate" if paths else "unresolved",
        evidence_ids=["pr-context-001"],
        execution_status="not_run",
    )


def _inventory(suites: tuple[InventorySuite, ...]) -> TestInventory:
    return TestInventory(
        schema="ia-repomap.test-inventory/v1",
        status="ok",
        repository=InventoryRepository(
            root="/tmp/test-repo",
            repository_id="repo-abc",
            head="a" * 40,
            dirty=False,
            scanner_version="v1",
            inventory_digest="b" * 64,
        ),
        suites=suites,
    )


class LoadPersistedTestInventoryTests(unittest.TestCase):
    def make_repo(self) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repo"
        root.mkdir()
        (root / "features/gl/input/gl-statistical-account").mkdir(parents=True)
        (root / "features/gl/gl-statistical.feature").write_text(
            "@gl\nFeature: Statistical accounts\n\n@gl\nScenario: Create statistical account\n"
            '  When "POST" to "general-ledger/statistical-account" with key "account" and file '
            '"create-gl-statistical-account.json" get variable ""\n',
            encoding="utf-8",
        )
        (root / "features/gl/input/gl-statistical-account/create-gl-statistical-account.json").write_text(
            '{"object": "general-ledger/statistical-account"}\n', encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
        return root, Path(temporary.name) / "artifacts"

    def test_roundtrip_matches_original(self) -> None:
        root, artifact_root = self.make_repo()
        inventory = build_test_inventory(root)
        persisted = persist_test_inventory(inventory, artifact_root)

        loaded = load_persisted_test_inventory(persisted.inventory_path)

        self.assertEqual(loaded.as_dict(), inventory.as_dict())

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_persisted_test_inventory(Path(tempfile.gettempdir()) / "does-not-exist-inventory.json")

    def test_relative_path_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_persisted_test_inventory(Path("relative/inventory.json"))

    def test_malformed_json_raises(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "inventory.json"
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_persisted_test_inventory(path)

    def test_schema_mismatch_raises(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "inventory.json"
        path.write_text(json.dumps({"schema": "unexpected/v9"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_persisted_test_inventory(path)


class EvaluateTestCoverageTests(unittest.TestCase):
    def test_direct_path_match_is_covered(self) -> None:
        inventory = _inventory((
            InventorySuite(
                suite_id="features/gl",
                module="gl",
                category="executable",
                feature_files=("features/gl/gl-statistical.feature",),
                scenarios=(InventoryScenario(name="Create statistical account"),),
            ),
        ))
        test_areas = [_test_area(
            "Affected tests for app/source/gl/GLSetupManager.cls",
            ["features/gl/gl-statistical.feature"],
        )]

        result = evaluate_test_coverage(
            ["app/source/gl/GLSetupManager.cls"],
            test_areas,
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        self.assertEqual(result.status, "ok")
        finding = result.findings[0]
        self.assertEqual(finding.status, "covered")
        self.assertEqual(finding.match_basis, "path")
        self.assertEqual(finding.matched_suite_ids, ["features/gl"])
        self.assertEqual(finding.evidence_ids, ["test-inventory-001"])
        self.assertEqual(result.gaps, [])

    def test_fixture_only_suite_match_is_partial(self) -> None:
        inventory = _inventory((
            InventorySuite(
                suite_id="features/gl/input/gl-statistical-account",
                module="gl",
                category="fixture",
                input_files=("features/gl/input/gl-statistical-account/create.json",),
            ),
        ))
        test_areas = [_test_area(
            "Affected tests for app/source/gl/GLSetupManager.cls",
            ["features/gl/input/gl-statistical-account/create.json"],
        )]

        result = evaluate_test_coverage(
            ["app/source/gl/GLSetupManager.cls"],
            test_areas,
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        self.assertEqual(result.findings[0].status, "partial")

    def test_module_heuristic_used_when_no_known_test_path(self) -> None:
        inventory = _inventory((
            InventorySuite(
                suite_id="features/gl",
                module="gl",
                category="executable",
                feature_files=("features/gl/gl-statistical.feature",),
            ),
        ))

        result = evaluate_test_coverage(
            ["app/source/gl/Foo.cls"],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        finding = result.findings[0]
        self.assertEqual(finding.status, "partial")
        self.assertEqual(finding.match_basis, "module_api_object")
        self.assertEqual(finding.matched_suite_ids, ["features/gl"])
        self.assertEqual(finding.matched_suite_count, 1)
        self.assertEqual(result.gaps, [])

    def test_broad_module_only_match_is_bounded_ambiguity_gap(self) -> None:
        inventory = _inventory(tuple(
            InventorySuite(
                suite_id=f"features/gl/input/suite-{index:02d}",
                module="gl",
                category="fixture",
            )
            for index in range(MAX_MATCHED_SUITE_IDS + 16)
        ))

        result = evaluate_test_coverage(
            ["app/source/gl/Foo.cls"],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        finding = result.findings[0]
        self.assertEqual(finding.status, "gap")
        self.assertEqual(finding.match_basis, "module_api_object")
        self.assertEqual(len(finding.matched_suite_ids), MAX_MATCHED_SUITE_IDS)
        self.assertEqual(finding.matched_suite_count, MAX_MATCHED_SUITE_IDS + 16)
        self.assertEqual(
            finding.matched_suite_ids,
            sorted(suite.suite_id for suite in inventory.suites)[:MAX_MATCHED_SUITE_IDS],
        )
        self.assertIn("too broad", finding.reason)
        self.assertEqual(result.metrics["truncated_match_findings"], 1)
        self.assertEqual(result.metrics["omitted_match_ids"], 16)
        self.assertTrue(any("50-id cap" in diagnostic for diagnostic in result.diagnostics))
        self.assertEqual(len(result.gaps), 1)

    def test_module_only_match_at_cap_remains_partial(self) -> None:
        inventory = _inventory(tuple(
            InventorySuite(
                suite_id=f"features/gl/input/suite-{index:02d}",
                module="gl",
                category="fixture",
            )
            for index in range(MAX_MATCHED_SUITE_IDS)
        ))

        result = evaluate_test_coverage(
            ["app/source/gl/Foo.cls"],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        finding = result.findings[0]
        self.assertEqual(finding.status, "partial")
        self.assertEqual(finding.matched_suite_count, MAX_MATCHED_SUITE_IDS)
        self.assertEqual(result.metrics["omitted_match_ids"], 0)
        self.assertEqual(result.gaps, [])

    def test_direct_path_matches_over_cap_preserve_covered_status(self) -> None:
        inventory = _inventory(tuple(
            InventorySuite(
                suite_id=f"features/gl/suite-{index:02d}",
                module="gl",
                category="executable",
                feature_files=(f"features/gl/suite-{index:02d}.feature",),
            )
            for index in range(MAX_MATCHED_SUITE_IDS + 1)
        ))
        test_paths = [suite.feature_files[0] for suite in inventory.suites]

        result = evaluate_test_coverage(
            ["app/source/gl/Foo.cls"],
            [_test_area("Affected tests for app/source/gl/Foo.cls", test_paths)],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        finding = result.findings[0]
        self.assertEqual(finding.status, "covered")
        self.assertEqual(finding.match_basis, "path")
        self.assertEqual(len(finding.matched_suite_ids), MAX_MATCHED_SUITE_IDS)
        self.assertEqual(finding.matched_suite_count, MAX_MATCHED_SUITE_IDS + 1)
        self.assertEqual(result.gaps, [])

    def test_api_object_matches_take_priority_over_module_matches(self) -> None:
        inventory = _inventory((
            InventorySuite(
                suite_id="features/gl/input/module-only",
                module="gl",
                category="fixture",
            ),
            InventorySuite(
                suite_id="features/shared/input/foo",
                module="shared",
                category="fixture",
                api_objects=("foo",),
            ),
        ))

        result = evaluate_test_coverage(
            ["app/source/gl/Foo.cls"],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        finding = result.findings[0]
        self.assertEqual(finding.status, "partial")
        self.assertEqual(finding.matched_suite_ids, ["features/shared/input/foo"])
        self.assertEqual(finding.matched_suite_count, 1)

    def test_no_match_is_a_gap_with_suggested_stub(self) -> None:
        inventory = _inventory((
            InventorySuite(
                suite_id="features/ap",
                module="ap",
                category="executable",
                feature_files=("features/ap/ap-invoice.feature",),
            ),
        ))

        result = evaluate_test_coverage(
            ["app/source/gl/GLSetupManager.cls"],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        finding = result.findings[0]
        self.assertEqual(finding.status, "gap")
        self.assertEqual(finding.match_basis, "none")
        self.assertEqual(len(result.gaps), 1)
        self.assertEqual(len(result.suggested_artifacts), 1)
        gap = result.gaps[0]
        artifact = result.suggested_artifacts[0]
        self.assertEqual(gap.evidence_ids, [artifact.evidence_id])
        self.assertTrue(artifact.relative_path.startswith("evidence/coverage/suggested/"))
        self.assertTrue(artifact.relative_path.endswith(".feature.suggested"))

        stub = render_suggested_stub(gap)
        self.assertIn("@needs-review", stub)
        self.assertIn("Feature:", stub)
        self.assertIn("Scenario: Cover changes in app/source/gl/GLSetupManager.cls", stub)

    def test_suggested_stub_paths_are_unique_when_slugs_collide(self) -> None:
        inventory = _inventory(())
        result = evaluate_test_coverage(
            [
                "app/source/gl/GLSetupManager.cls",
                "app/source/gl/gl-setup-manager.cls",
            ],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        self.assertEqual(len(result.gaps), 2)
        self.assertEqual(len(result.suggested_artifacts), 2)
        relative_paths = [artifact.relative_path for artifact in result.suggested_artifacts]
        self.assertEqual(len(relative_paths), len(set(relative_paths)))

    def test_suggested_stub_cap_is_enforced(self) -> None:
        inventory = _inventory(())
        changed_paths = [f"app/source/gl/File{i}.cls" for i in range(MAX_SUGGESTED_STUBS + 5)]

        result = evaluate_test_coverage(
            changed_paths,
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )

        self.assertEqual(len(result.gaps), MAX_SUGGESTED_STUBS + 5)
        self.assertEqual(len(result.suggested_artifacts), MAX_SUGGESTED_STUBS)
        self.assertTrue(any("exceeded" in diagnostic for diagnostic in result.diagnostics))

    def test_render_suggested_stub_is_deterministic(self) -> None:
        inventory = _inventory(())
        result = evaluate_test_coverage(
            ["app/source/gl/GLSetupManager.cls"],
            [],
            inventory,
            inventory_evidence_id="test-inventory-001",
        )
        gap = result.gaps[0]
        self.assertEqual(render_suggested_stub(gap), render_suggested_stub(gap))


class InventoryGapDataclassSanityTests(unittest.TestCase):
    def test_inventory_gap_is_reused_without_change(self) -> None:
        gap = InventoryGap(kind="k", path=None, detail="d")
        self.assertEqual(gap.kind, "k")


if __name__ == "__main__":
    unittest.main()
