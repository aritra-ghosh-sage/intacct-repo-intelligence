from __future__ import annotations

import unittest

from scripts.builder_registry import BuilderPlanError, build_plan


class BuilderRegistryTests(unittest.TestCase):
    def test_intacct_plan_is_dependency_ordered(self) -> None:
        plan = build_plan("intacct_app")
        self.assertLess(plan.index("scan"), plan.index("symbols"))
        self.assertLess(plan.index("entities"), plan.index("entity_roots"))
        self.assertLess(plan.index("openapi_scan"), plan.index("openapi_link"))
        self.assertLess(plan.index("openapi_link"), plan.index("rest_endpoints"))
        self.assertLess(plan.index("entities"), plan.index("entity_semantics"))
        self.assertLess(plan.index("entity_semantics"), plan.index("entity_access_links"))
        self.assertLess(plan.index("workflows"), plan.index("entity_access_links"))

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
                "integration_links",
                "gherkin_coverage",
            ],
        )

    def test_unknown_builder_is_rejected(self) -> None:
        with self.assertRaisesRegex(BuilderPlanError, "unknown builder"):
            build_plan("generic", ["made_up"])


if __name__ == "__main__":
    unittest.main()
