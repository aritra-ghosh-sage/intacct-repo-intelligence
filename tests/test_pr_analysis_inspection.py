"""Focused tests for bounded repository inspection."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ia_repomap_builder.config import PrChangedFile, PrContextResult, PrSymbolCandidate
from ia_repomap_builder.pr_analysis import EvidenceSession, PreAgenticSeed
from ia_repomap_builder.pr_analysis_inspection import (
    inspect_repository,
    make_repository_inspection_tool,
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


class InspectionTests(unittest.TestCase):
    def repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        handle = tempfile.TemporaryDirectory()
        root = Path(handle.name)
        git(root, "init", "-q")
        (root / "src").mkdir()
        (root / "src/example.cls").write_text("class Example {\n  changed();\n}\n", encoding="utf-8")
        (root / "README.md").write_text("changed is documented\n", encoding="utf-8")
        git(root, "add", ".")
        git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture")
        return handle, root

    def test_literal_search_is_tracked_and_deterministic(self) -> None:
        handle, root = self.repo()
        try:
            result = inspect_repository(
                root,
                ["changed"],
                authorized_terms=["changed"],
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                [(row["path"], row["line"]) for row in result["matches"]],
                [("README.md", 1), ("src/example.cls", 2)],
            )
            self.assertNotIn("regex", result["matches"][0]["match_type"])
        finally:
            handle.cleanup()

    def test_terms_and_paths_must_be_authorized(self) -> None:
        handle, root = self.repo()
        try:
            with self.assertRaises(ValueError):
                inspect_repository(root, ["unknown"], authorized_terms=[])
            with self.assertRaises(ValueError):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["src/example.cls"],
                    authorized_terms=["changed"],
                    authorized_paths=[],
                )
            with self.assertRaises(ValueError):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["../outside"],
                    authorized_terms=["changed"],
                    authorized_paths=["../outside"],
                )
        finally:
            handle.cleanup()

    def test_term_and_file_limits_are_rejected(self) -> None:
        handle, root = self.repo()
        try:
            with self.assertRaises(ValueError):
                inspect_repository(root, [str(i) for i in range(9)], authorized_terms=[str(i) for i in range(9)])
            with self.assertRaises(ValueError):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["src/example.cls"] * 11,
                    authorized_terms=["changed"],
                    authorized_paths=["src/example.cls"],
                )
        finally:
            handle.cleanup()

    def test_global_search_finds_match_after_unrelated_tracked_files(self) -> None:
        handle, root = self.repo()
        try:
            for index in range(10):
                path = root / f"a-{index:02d}.txt"
                path.write_text("unrelated\n", encoding="utf-8")
            target = root / "z-target.txt"
            target.write_text("needle appears here\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "more files")
            result = inspect_repository(root, ["needle"], authorized_terms=["needle"])
            self.assertEqual([(row["path"], row["line"]) for row in result["matches"]], [("z-target.txt", 1)])
        finally:
            handle.cleanup()

    def test_file_cap_discloses_omitted_matching_files(self) -> None:
        handle, root = self.repo()
        try:
            for index in range(12):
                path = root / f"match-{index:02d}.txt"
                path.write_text("needle\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "many matches")
            result = inspect_repository(root, ["needle"], authorized_terms=["needle"])
            self.assertEqual(len(result["matches"]), 10)
            truncation = [gap for gap in result["gaps"] if gap["kind"] == "inspection_truncated"]
            self.assertEqual(truncation[0]["count"], 2)
            self.assertEqual(result["metrics"]["files_omitted"], 2)
        finally:
            handle.cleanup()

    def test_match_cap_applies_across_all_selected_files(self) -> None:
        handle, root = self.repo()
        try:
            for index in range(2):
                path = root / f"many-{index:02d}.txt"
                path.write_text("".join("needle\n" for _ in range(15)), encoding="utf-8")
            git(root, "add", ".")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "many matches per term")
            result = inspect_repository(root, ["needle"], authorized_terms=["needle"])
            self.assertEqual(len([row for row in result["matches"] if row["term"] == "needle"]), 20)
            self.assertTrue(any(
                gap["kind"] == "inspection_truncated" and gap["count"] == 20
                for gap in result["gaps"]
            ))
        finally:
            handle.cleanup()

    def test_no_match_is_explicit(self) -> None:
        handle, root = self.repo()
        try:
            result = inspect_repository(root, ["absent"], authorized_terms=["absent"])
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["gaps"][0]["kind"], "inspection_no_match")
        finally:
            handle.cleanup()

    def test_tool_returns_structured_error_and_consumes_failed_call(self) -> None:
        handle, root = self.repo()
        try:
            seed = PreAgenticSeed(
                "ok",
                "analysis",
                context=PrContextResult("ok"),
                allowed_symbols=(("src/example.cls", "changed"),),
            )
            session = EvidenceSession(seed)
            tool = make_repository_inspection_tool(session, repo_root=root)
            result = tool(["not-authorized"])
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["gaps"][0]["kind"], "inspection_unavailable")
            self.assertEqual(session.inspection_calls, 1)
        finally:
            handle.cleanup()

    def test_binary_match_is_disclosed_as_gap(self) -> None:
        handle, root = self.repo()
        try:
            binary = root / "src" / "binary.dat"
            binary.write_bytes(b"needle\x00payload")
            git(root, "add", ".")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "binary")
            result = inspect_repository(root, ["needle"], authorized_terms=["needle"])
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["gaps"][0]["kind"], "inspection_binary_skipped")
            self.assertEqual(result["gaps"][0]["path"], "src/binary.dat")
        finally:
            handle.cleanup()

    def test_successful_tool_evidence_excludes_excerpts(self) -> None:
        handle, root = self.repo()
        try:
            payloads: list[tuple[str, bytes]] = []
            seed = PreAgenticSeed(
                "ok",
                "analysis",
                context=PrContextResult("ok"),
                allowed_symbols=(("src/example.cls", "changed"),),
            )
            session = EvidenceSession(seed)
            tool = make_repository_inspection_tool(session, repo_root=root, evidence_payloads=payloads)
            result = tool(["changed"])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(payloads), 1)
            self.assertNotIn(b"class Example", payloads[0][1])
            self.assertNotIn(b"excerpt", payloads[0][1])
        finally:
            handle.cleanup()

    def test_second_inspection_can_use_literal_from_first_excerpt(self) -> None:
        handle, root = self.repo()
        try:
            seed = PreAgenticSeed(
                "ok",
                "analysis",
                context=PrContextResult("ok"),
                allowed_symbols=(("src/example.cls", "changed"),),
            )
            session = EvidenceSession(seed)
            tool = make_repository_inspection_tool(session, repo_root=root)
            first = tool(["changed"])
            self.assertEqual(first["status"], "ok")
            second = tool(["Example"])
            self.assertEqual(second["status"], "ok")
            self.assertTrue(second["matches"])
        finally:
            handle.cleanup()

    def test_pr_context_paths_and_symbols_are_authorized(self) -> None:
        handle, root = self.repo()
        try:
            seed = PreAgenticSeed(
                "ok",
                "analysis",
                context=PrContextResult(
                    "ok",
                    changed_files=[PrChangedFile(
                        path="src/example.cls",
                        change="M",
                        symbols=(PrSymbolCandidate("src/example.cls", "changed", 2),),
                    )],
                ),
                allowed_symbols=(("src/example.cls", "changed"),),
            )
            session = EvidenceSession(seed)
            result = inspect_repository(
                root,
                ["src/example.cls", "changed"],
                paths=["src/example.cls"],
                authorized_terms=session.authorized_inspection_terms,
                authorized_paths=session.authorized_inspection_paths,
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["matches"])
        finally:
            handle.cleanup()


if __name__ == "__main__":
    unittest.main()
