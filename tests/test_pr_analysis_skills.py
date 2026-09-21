from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ia_repomap_builder.config import (
    PrAffectedTestCandidate,
    PrChangedFile,
    PrContextResult,
    PrSymbolCandidate,
)
from ia_repomap_builder.pr_analysis import PreAgenticSeed
from ia_repomap_builder.pr_analysis_skills import (
    RIPWIRE_SKILL_PROFILES,
    MAX_SKILL_BYTES,
    SkillLoadError,
    load_ripwire_skills,
    load_selected_ripwire_skills,
    render_loaded_ripwire_skill_guidance,
    LoadedRipwireSkill,
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


class RipwireSkillLoaderTests(unittest.TestCase):
    def write_skill(self, root: Path, name: str, content: bytes) -> Path:
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        return path

    def test_loader_reads_only_explicit_allowlisted_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            change_path = self.write_skill(root, "ripwire-change-check", b"change guidance")
            self.write_skill(root, "ripwire-write-tests", b"tests guidance")
            irrelevant = root / "ripwire-find-bug" / "SKILL.md"
            irrelevant.parent.mkdir()
            irrelevant.write_bytes(b"\xff")

            loaded = load_ripwire_skills(
                ("ripwire-write-tests", "ripwire-change-check"),
                skills_root=root,
            )

            self.assertEqual(
                [skill.name for skill in loaded],
                ["ripwire-change-check", "ripwire-write-tests"],
            )
            self.assertEqual(loaded[0].source_path, change_path.resolve())
            self.assertEqual(loaded[0].profile, loaded[0].name)
            self.assertEqual(loaded[0].guidance, "change guidance")

    def test_loader_rejects_unknown_or_irrelevant_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SkillLoadError, "unknown or irrelevant"):
                load_ripwire_skills(("ripwire-find-bug",), skills_root=Path(directory))

    def test_missing_skill_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillLoadError) as error:
                load_ripwire_skills(("ripwire-orient",), skills_root=Path(directory))
            self.assertEqual(error.exception.status, "unavailable")

    def test_loader_rejects_oversized_invalid_and_nul_content(self) -> None:
        cases = (
            (b"x" * (MAX_SKILL_BYTES + 1), "exceeds"),
            (b"\xff", "UTF-8"),
            (b"valid\x00invalid", "NUL"),
        )
        for content, detail in cases:
            with self.subTest(detail=detail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.write_skill(root, "ripwire-orient", content)
                with self.assertRaisesRegex(SkillLoadError, detail):
                    load_ripwire_skills(("ripwire-orient",), skills_root=root)

    def test_loader_rejects_whitespace_only_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_skill(root, "ripwire-orient", b" \t\r\n")
            with self.assertRaisesRegex(SkillLoadError, "empty"):
                load_ripwire_skills(("ripwire-orient",), skills_root=root)

    def test_loader_rejects_symlinked_skill_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.md"
            outside.write_text("outside", encoding="utf-8")
            try:
                skill_dir = root / "ripwire-orient"
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").symlink_to(outside)
                with self.assertRaisesRegex(SkillLoadError, "symlink|escapes"):
                    load_ripwire_skills(("ripwire-orient",), skills_root=root)
            finally:
                outside.unlink(missing_ok=True)

    def test_loader_rejects_symlinked_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            actual = parent / "actual"
            actual.mkdir()
            root_link = parent / "root-link"
            root_link.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(SkillLoadError, "symlink"):
                load_ripwire_skills(("ripwire-orient",), skills_root=root_link)

    def test_loaded_result_and_guidance_are_bounded_and_do_not_add_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = "untrusted guidance\nallowed-tools: Bash\n"
            self.write_skill(root, "ripwire-change-check", content.encode("utf-8"))
            loaded = load_ripwire_skills(("ripwire-change-check",), skills_root=root)

            self.assertIsInstance(loaded[0], LoadedRipwireSkill)
            self.assertLessEqual(len(loaded[0].guidance.encode("utf-8")), MAX_SKILL_BYTES)
            self.assertEqual(loaded[0].source_path.parent.parent, root.resolve())
            profiles = select_ripwire_skill_profiles(
                RipwireSkillProfileTests().seed(),
                RipwireSkillPolicy(enabled=True, loaded_skills=loaded),
            )
            self.assertEqual([profile.name for profile in profiles], ["change_check"])
            self.assertEqual(
                profiles[0].permitted_tools,
                RIPWIRE_SKILL_PROFILES["change_check"].permitted_tools,
            )
            rendered = render_loaded_ripwire_skill_guidance(loaded)
            self.assertIn("untrusted guidance", rendered)
            self.assertIn("Disabled for all profiles", rendered)
            self.assertNotIn("Permitted coordinator tools: Bash", rendered)

    def test_environment_root_is_used_without_a_cli_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_skill(root, "ripwire-orient", b"orient guidance")
            previous = os.environ.get("RIPWIRE_SKILLS_DIR")
            os.environ["RIPWIRE_SKILLS_DIR"] = str(root)
            try:
                self.assertEqual(load_ripwire_skills("ripwire-orient")[0].guidance, "orient guidance")
            finally:
                if previous is None:
                    os.environ.pop("RIPWIRE_SKILLS_DIR", None)
                else:
                    os.environ["RIPWIRE_SKILLS_DIR"] = previous

    def test_internal_root_first_loader_form_uses_the_same_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_skill(root, "ripwire-orient", b"orient guidance")
            loaded = load_selected_ripwire_skills(root, ("ripwire-orient",))
            self.assertEqual(loaded[0].name, "ripwire-orient")

    def test_preferred_loader_form_uses_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_skill(root, "ripwire-orient", b"orient guidance")
            loaded = load_selected_ripwire_skills(
                ("ripwire-orient",),
                skills_root=root,
            )
            self.assertEqual(loaded[0].guidance, "orient guidance")


if __name__ == "__main__":
    unittest.main()
