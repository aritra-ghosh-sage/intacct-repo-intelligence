from __future__ import annotations

import unittest

from ia_repomap_builder.config import (
    PrAffectedTestCandidate,
    PrChangedFile,
    PrContextResult,
    PrSymbolCandidate,
)
from ia_repomap_builder.pr_analysis import PreAgenticSeed
from ia_repomap_builder.pr_analysis_skills import (
    RIPWIRE_SKILL_PROFILES,
    RipwireSkillPolicy,
    agent_skills_supported,
    render_ripwire_skill_guidance,
    select_ripwire_skill_profiles,
)


class RipwireSkillProfileTests(unittest.TestCase):
    def seed(
        self,
        *,
        symbols: bool = True,
        affected_tests: bool = False,
    ) -> PreAgenticSeed:
        changed_path = "app/source/example/Example.cls"
        return PreAgenticSeed(
            "ok",
            "analysis",
            PrContextResult(
                "ok",
                changed_files=[PrChangedFile(
                    path=changed_path,
                    change="M",
                    symbols=(PrSymbolCandidate(changed_path, "changed", 1),) if symbols else (),
                    affected_tests=(PrAffectedTestCandidate("app/source/tests/ExampleTest.cls"),)
                    if affected_tests
                    else (),
                )],
            ),
            ((changed_path, "changed"),) if symbols else (),
        )

    def test_registry_contains_only_curated_six_profiles(self) -> None:
        self.assertEqual(
            set(RIPWIRE_SKILL_PROFILES),
            {
                "change_check",
                "write_tests",
                "find_bug",
                "orient",
                "graph_query",
                "security_scan",
            },
        )
        for profile in RIPWIRE_SKILL_PROFILES.values():
            self.assertNotIn("arbitrary shell", profile.permitted_tools)
            self.assertIn("arbitrary shell", profile.disabled_capabilities)

    def test_no_profiles_selected_when_policy_is_disabled(self) -> None:
        self.assertEqual(select_ripwire_skill_profiles(self.seed(), None), ())
        self.assertEqual(
            select_ripwire_skill_profiles(self.seed(), RipwireSkillPolicy(enabled=False)),
            (),
        )

    def test_pr_with_symbols_selects_change_check_and_missing_tests(self) -> None:
        profiles = select_ripwire_skill_profiles(self.seed(), RipwireSkillPolicy(enabled=True))
        self.assertEqual([profile.name for profile in profiles], ["change_check", "write_tests"])

    def test_existing_affected_tests_do_not_select_write_tests_by_default(self) -> None:
        profiles = select_ripwire_skill_profiles(
            self.seed(affected_tests=True),
            RipwireSkillPolicy(enabled=True),
        )
        self.assertEqual([profile.name for profile in profiles], ["change_check"])

    def test_hints_and_requested_profiles_route_explicitly(self) -> None:
        profiles = select_ripwire_skill_profiles(
            self.seed(),
            RipwireSkillPolicy(
                enabled=True,
                requested_profiles=("security_scan",),
                workflow_hints=("regression", "graph_query"),
            ),
        )
        self.assertEqual(
            [profile.name for profile in profiles],
            ["change_check", "find_bug", "graph_query", "security_scan", "write_tests"],
        )

    def test_thin_context_selects_orient(self) -> None:
        profiles = select_ripwire_skill_profiles(
            self.seed(symbols=False),
            RipwireSkillPolicy(enabled=True),
        )
        self.assertEqual([profile.name for profile in profiles], ["orient", "write_tests"])

    def test_unknown_profile_refuses(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown Ripwire skill profile"):
            select_ripwire_skill_profiles(
                self.seed(),
                RipwireSkillPolicy(enabled=True, requested_profiles=("unknown",)),
            )

    def test_rendered_guidance_is_compact_and_disables_broad_capabilities(self) -> None:
        guidance = render_ripwire_skill_guidance((RIPWIRE_SKILL_PROFILES["change_check"],))
        self.assertIn("guidance only", guidance)
        self.assertIn("ripwire-change-check", guidance)
        self.assertIn("Disabled", guidance)
        self.assertNotIn("--scan-skills=DIR", guidance)

    def test_strands_agent_skills_probe_returns_boolean(self) -> None:
        self.assertIsInstance(agent_skills_supported(), bool)


if __name__ == "__main__":
    unittest.main()
