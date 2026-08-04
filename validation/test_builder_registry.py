from __future__ import annotations

import unittest

from catalog.delta import DELTA_CONTRACT_VERSION
from scripts.builder_registry import (
    BUILDERS,
    BuilderPlanError,
    build_plan,
    repository_matcher_overrides,
    stage_execution_modes,
)


class BuilderRegistryTests(unittest.TestCase):
    def test_intacct_plan_is_dependency_ordered(self) -> None:
        plan = build_plan("intacct_app")
        self.assertLess(plan.index("scan"), plan.index("symbols"))
        self.assertLess(plan.index("entities"), plan.index("entity_roots"))
        self.assertLess(plan.index("openapi_scan"), plan.index("openapi_link"))
        self.assertLess(plan.index("openapi_scan"), plan.index("api_registry"))
        self.assertLess(plan.index("openapi_link"), plan.index("rest_endpoints"))
        self.assertLess(plan.index("scan"), plan.index("ui_surfaces"))
        self.assertLess(plan.index("relationships"), plan.index("ui_surfaces"))
        self.assertLess(plan.index("entities"), plan.index("ui_surfaces"))
        self.assertLess(plan.index("openapi_link"), plan.index("ui_surfaces"))
        self.assertLess(plan.index("entities"), plan.index("entity_semantics"))
        self.assertLess(
            plan.index("entity_semantics"), plan.index("entity_access_links")
        )
        self.assertLess(plan.index("workflows"), plan.index("entity_access_links"))

    def test_registry_only_delta_does_not_invalidate_rest_or_ui(self) -> None:
        plan = build_plan("intacct_app")
        modes = stage_execution_modes(
            plan,
            repository_mode="delta",
            changed_paths=("app/source/api/registries/RegistryV1.json",),
        )
        self.assertEqual(modes["api_registry"][0], "full")
        self.assertEqual(modes["rest_endpoints"][0], "skipped")
        self.assertEqual(modes["ui_surfaces"][0], "skipped")

    def test_openapi_change_runs_registry_but_registry_is_not_rest_input(self) -> None:
        plan = build_plan("intacct_app")
        modes = stage_execution_modes(
            plan,
            repository_mode="delta",
            changed_paths=(
                "app/source/openapispec/gl/models/objects.general-ledger.journal-entry.s1.schema.yaml",
            ),
        )
        self.assertEqual(modes["api_registry"][0], "full")
        self.assertEqual(modes["rest_endpoints"][0], "full")

    def test_generic_profile_rejects_app_builder(self) -> None:
        with self.assertRaisesRegex(BuilderPlanError, "not supported"):
            build_plan("generic", ["security"])

    def test_rest_automation_runs_coverage_after_generic_cataloging(self) -> None:
        plan = build_plan("rest_automation")
        self.assertEqual(
            plan,
            [
                "scan",
                "symbols",
                "relationships",
                "gherkin_coverage",
            ],
        )

    def test_contract_v1_artifacts_each_invalidate_gherkin_coverage(self) -> None:
        entry = {
            "profile": "rest_automation",
            "rest_automation": {
                "coverage_contract_version": 1,
                "features_root": "features",
                "object_mapping": "contract/mapping.json",
                "version_compatibility": "contract/compatibility.json",
                "non_request_inventory": "contract/inventory.json",
            },
        }
        plan = build_plan("rest_automation")
        matchers = repository_matcher_overrides(entry)
        for artifact in (
            "contract/mapping.json",
            "contract/compatibility.json",
            "contract/inventory.json",
        ):
            with self.subTest(artifact=artifact):
                modes = stage_execution_modes(
                    plan,
                    repository_mode="delta",
                    changed_paths=(artifact,),
                    matcher_overrides=matchers,
                )
                self.assertEqual(modes["gherkin_coverage"][0], "full")
                self.assertIn(artifact, modes["gherkin_coverage"][1])

    def test_explicit_unsupported_integration_builder_is_rejected(self) -> None:
        with self.assertRaisesRegex(BuilderPlanError, "unsupported"):
            build_plan("rest_automation", ["integration_links"])

    def test_unknown_builder_is_rejected(self) -> None:
        with self.assertRaisesRegex(BuilderPlanError, "unknown builder"):
            build_plan("generic", ["made_up"])

    def test_ui_surfaces_contract_is_intacct_scoped_full(self) -> None:
        builder = BUILDERS["ui_surfaces"]
        self.assertEqual(
            builder.dependencies, ("relationships", "entities", "openapi_link")
        )
        self.assertEqual(builder.profiles, frozenset({"intacct_app"}))
        self.assertEqual(builder.delta_capability, "scoped_full")
        self.assertEqual(DELTA_CONTRACT_VERSION, 4)
        with self.assertRaisesRegex(BuilderPlanError, "not supported"):
            build_plan("generic", ["ui_surfaces"])

    def test_ui_surfaces_invalidates_only_for_ui_inputs(self) -> None:
        plan = build_plan("intacct_app")
        relevant_paths = (
            "app/source/gl/glbatch_form.xml",
            "app/source/gl/GLBatchEditor.cls",
            "app/resources/js/glbatch.js",
            "app/source/gl/glbatch.ent",
            "app/source/openapispec/gl/models/objects.general-ledger.journal-entry.s1.schema.yaml",
        )
        for path in relevant_paths:
            with self.subTest(path=path):
                modes = stage_execution_modes(
                    plan, repository_mode="delta", changed_paths=(path,)
                )
                self.assertEqual(modes["ui_surfaces"][0], "full")
                self.assertIn(path, modes["ui_surfaces"][1])

        unrelated = stage_execution_modes(
            plan,
            repository_mode="delta",
            changed_paths=("app/source/gl/GLBatchManager.java",),
        )
        self.assertEqual(unrelated["ui_surfaces"], ("skipped", "source inputs unchanged"))


if __name__ == "__main__":
    unittest.main()
