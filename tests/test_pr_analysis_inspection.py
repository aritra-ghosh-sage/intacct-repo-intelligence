"""Focused tests for bounded repository inspection."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ia_repomap_builder.config import PrChangedFile, PrContextResult, PrSymbolCandidate
from ia_repomap_builder.pr_analysis import (
    AgentAnalysisDraftV1,
    EvidenceSession,
    PreAgenticSeed,
)
from ia_repomap_builder.pr_analysis_inspection import (
    InspectionRequestRejected,
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

    def session(self) -> EvidenceSession:
        seed = PreAgenticSeed(
            "ok",
            "analysis",
            context=PrContextResult("ok"),
            allowed_symbols=(("src/example.cls", "changed"),),
        )
        return EvidenceSession(seed)

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
            with self.assertRaises(InspectionRequestRejected):
                inspect_repository(root, ["unknown"], authorized_terms=[])
            with self.assertRaises(InspectionRequestRejected):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["src/example.cls"],
                    authorized_terms=["changed"],
                    authorized_paths=[],
                )
            with self.assertRaises(InspectionRequestRejected):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["../outside"],
                    authorized_terms=["changed"],
                    authorized_paths=["../outside"],
                )
        finally:
            handle.cleanup()

    def test_untracked_and_escaping_symlink_paths_are_rejected(self) -> None:
        handle, root = self.repo()
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside_path = Path(outside.name)
        outside.write(b"changed\n")
        outside.close()
        try:
            untracked = root / "src" / "untracked.cls"
            untracked.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(InspectionRequestRejected):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["src/untracked.cls"],
                    authorized_terms=["changed"],
                    authorized_paths=["src/untracked.cls"],
                )

            escaping = root / "src" / "escaping.cls"
            escaping.symlink_to(outside_path)
            git(root, "add", "src/escaping.cls")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "symlink")
            with self.assertRaises(InspectionRequestRejected):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["src/escaping.cls"],
                    authorized_terms=["changed"],
                    authorized_paths=["src/escaping.cls"],
                )
        finally:
            outside_path.unlink(missing_ok=True)
            handle.cleanup()

    def test_exact_authorized_terms_and_paths_are_required(self) -> None:
        handle, root = self.repo()
        try:
            result = inspect_repository(
                root,
                ["changed"],
                paths=["src/example.cls"],
                authorized_terms=["changed"],
                authorized_paths=["src/example.cls"],
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                {(match["path"], match["term"]) for match in result["matches"]},
                {("src/example.cls", "changed")},
            )

            with self.assertRaisesRegex(ValueError, "not authorized"):
                inspect_repository(
                    root,
                    ["Changed"],
                    paths=["src/example.cls"],
                    authorized_terms=["changed"],
                    authorized_paths=["src/example.cls"],
                )
            with self.assertRaisesRegex(ValueError, "not authorized"):
                inspect_repository(
                    root,
                    ["changed"],
                    paths=["src/Example.cls"],
                    authorized_terms=["changed"],
                    authorized_paths=["src/example.cls"],
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

    def test_rejected_request_is_generic_and_does_not_persist_raw_values(self) -> None:
        handle, root = self.repo()
        try:
            raw_term = "UNAUTHORIZED_TERM_SENTINEL"
            raw_path = "UNAUTHORIZED_PATH_SENTINEL.cls"
            session = self.session()
            payloads: list[tuple[str, bytes]] = []
            tool = make_repository_inspection_tool(
                session,
                repo_root=root,
                evidence_payloads=payloads,
            )

            result = tool([raw_term], paths=[raw_path])

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["evidence_id"], None)
            self.assertEqual(result["diagnostics"], ["inspection request rejected"])
            self.assertEqual(
                result["gaps"],
                [{
                    "kind": "inspection_unavailable",
                    "detail": "Host rejected an inspection request; raw terms and paths were omitted",
                }],
            )
            self.assertNotIn(raw_term, json.dumps(result, sort_keys=True))
            self.assertNotIn(raw_path, json.dumps(result, sort_keys=True))
            self.assertNotIn(raw_term, json.dumps(session.tool_gaps, sort_keys=True))
            self.assertNotIn(raw_path, json.dumps(session.tool_gaps, sort_keys=True))
            self.assertNotIn(raw_term, json.dumps(session.tool_diagnostics, sort_keys=True))
            self.assertNotIn(raw_path, json.dumps(session.tool_diagnostics, sort_keys=True))
            self.assertEqual(payloads, [])
        finally:
            handle.cleanup()

    def test_rejection_metrics_include_counts_and_canonical_request_hash(self) -> None:
        handle, root = self.repo()
        try:
            terms = ["UNAUTHORIZED_TERM_SENTINEL"]
            paths = ["UNAUTHORIZED_PATH_SENTINEL.cls"]
            session = self.session()
            tool = make_repository_inspection_tool(session, repo_root=root)

            result = tool(terms, paths=paths)

            expected_request = json.dumps(
                {"paths": sorted(paths), "terms": sorted(terms)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            expected_hash = hashlib.sha256(expected_request).hexdigest()[:16]
            metrics = result["metrics"]
            self.assertEqual(metrics["inspection_calls"], 1)
            self.assertEqual(metrics["inspection_rejection_count"], 1)
            self.assertEqual(metrics["inspection_requested_term_count"], 1)
            self.assertEqual(metrics["inspection_requested_path_count"], 1)
            self.assertEqual(metrics["inspection_last_request_hash"], expected_hash)
            self.assertRegex(metrics["inspection_last_request_hash"], r"^[0-9a-f]{16}$")
        finally:
            handle.cleanup()

    def test_rejected_call_stops_inspection(self) -> None:
        handle, root = self.repo()
        try:
            session = self.session()
            tool = make_repository_inspection_tool(session, repo_root=root)
            with patch(
                "ia_repomap_builder.pr_analysis_inspection.inspect_repository",
                wraps=inspect_repository,
            ) as inspect:
                first = tool(["UNAUTHORIZED_TERM_ONE"])
                second = tool(["changed"])
                third = tool(["changed"])

            self.assertEqual(first["status"], "error")
            self.assertEqual(second["status"], "error")
            self.assertEqual(third["status"], "error")
            self.assertEqual(inspect.call_count, 1)
            self.assertEqual(session.inspection_calls, 1)
            self.assertEqual(first["metrics"]["inspection_calls"], 1)
            self.assertEqual(second["metrics"]["inspection_calls"], 1)
            self.assertEqual(third["metrics"]["inspection_calls"], 1)
            self.assertEqual(third["diagnostics"], ["inspection unavailable"])
            self.assertEqual(session.inspection_rejection_metrics["inspection_rejection_count"], 1)
        finally:
            handle.cleanup()

    def test_operational_failure_is_unavailable_not_rejection(self) -> None:
        handle, root = self.repo()
        try:
            session = self.session()
            tool = make_repository_inspection_tool(session, repo_root=root)
            successful = tool(["changed"])
            self.assertEqual(successful["status"], "ok")
            with patch(
                "ia_repomap_builder.pr_analysis_inspection._matching_paths",
                side_effect=RuntimeError("simulated inspection failure"),
            ) as matching_paths:
                result = tool(["changed"])
                after_failure = tool(["changed"])

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["diagnostics"], ["inspection unavailable"])
            self.assertEqual(result["gaps"], [{
                "kind": "inspection_unavailable",
                "detail": "inspection unavailable",
            }])
            self.assertEqual(result["metrics"]["inspection_rejection_count"], 0)
            self.assertNotIn("inspection_last_request_hash", result["metrics"])
            self.assertEqual(after_failure["diagnostics"], ["inspection unavailable"])
            self.assertEqual(matching_paths.call_count, 1)
            session.require_evidence(["inspection-001"])

            session = self.session()
            tool = make_repository_inspection_tool(session, repo_root=root)
            with patch(
                "ia_repomap_builder.pr_analysis_inspection._matching_paths",
                side_effect=TimeoutError("simulated timeout"),
            ) as matching_paths:
                timed_out = tool(["changed"])
                after_timeout = tool(["changed"])
            self.assertEqual(timed_out["diagnostics"], ["inspection timed out"])
            self.assertEqual(timed_out["gaps"], [{
                "kind": "inspection_unavailable",
                "detail": "inspection timed out",
            }])
            self.assertEqual(timed_out["metrics"]["inspection_rejection_count"], 0)
            self.assertEqual(after_timeout["diagnostics"], ["inspection unavailable"])
            self.assertEqual(matching_paths.call_count, 1)
        finally:
            handle.cleanup()

    def test_model_narrative_cannot_repeat_rejected_value(self) -> None:
        session = self.session()
        raw_term = "UNAUTHORIZED_TERM_SENTINEL"
        session.record_inspection_rejection([raw_term], [])
        draft = AgentAnalysisDraftV1.model_validate({
            "summary": {
                "purpose": f"Mentioned {raw_term}",
                "behavioral_change": "bounded change",
                "confidence": "candidate",
            },
            "blast_radius": [],
            "test_areas": [],
        })

        with self.assertRaisesRegex(ValueError, "rejected inspection value"):
            session.validate_model_narrative(draft)

    def test_model_narrative_allows_authorized_values_from_oversized_rejection(self) -> None:
        session = self.session()
        terms = [f"authorized_term_{index:02d}" for index in range(9)]
        session.authorize_inspection_terms(terms)
        session.record_inspection_rejection(terms, ["src/example.cls"])
        draft = AgentAnalysisDraftV1.model_validate({
            "summary": {
                "purpose": f"Inspect {terms[0]}",
                "behavioral_change": "bounded change",
                "confidence": "candidate",
            },
            "blast_radius": [],
            "test_areas": [],
        })

        session.validate_model_narrative(draft)

        unauthorized_term = "UNAUTHORIZED_TERM_SENTINEL"
        session.record_inspection_rejection([unauthorized_term], [])
        unauthorized_draft = AgentAnalysisDraftV1.model_validate({
            "summary": {
                "purpose": f"Mentioned {unauthorized_term}",
                "behavioral_change": "bounded change",
                "confidence": "candidate",
            },
            "blast_radius": [],
            "test_areas": [],
        })
        with self.assertRaisesRegex(ValueError, "rejected inspection value"):
            session.validate_model_narrative(unauthorized_draft)

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

    def test_ten_authorized_terms_fit_two_bounded_inspection_calls(self) -> None:
        handle, root = self.repo()
        try:
            terms = [f"inspection_term_{index:02d}" for index in range(10)]
            readme = root / "README.md"
            readme.write_text("\n".join(terms) + "\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "inspection terms")

            session = self.session()
            session.authorize_inspection_terms(terms)
            payloads: list[tuple[str, bytes]] = []
            tool = make_repository_inspection_tool(
                session,
                repo_root=root,
                evidence_payloads=payloads,
            )

            first = tool(terms[:8])
            second = tool(terms[8:])

            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["status"], "ok")
            self.assertEqual({match["term"] for match in first["matches"]}, set(terms[:8]))
            self.assertEqual({match["term"] for match in second["matches"]}, set(terms[8:]))
            evidence_ids = [first["evidence_id"], second["evidence_id"]]
            self.assertTrue(all(evidence_ids))
            self.assertEqual(len(set(evidence_ids)), 2)
            self.assertEqual([evidence_id for evidence_id, _ in payloads], evidence_ids)
            self.assertEqual(session.inspection_calls, 2)
            self.assertEqual(session.inspection_rejection_metrics["inspection_rejection_count"], 0)
            self.assertEqual(session.tool_gaps, ())
            self.assertEqual(session.tool_diagnostics, ())
        finally:
            handle.cleanup()

    def test_tool_response_bounds_excerpts_and_match_count(self) -> None:
        handle, root = self.repo()
        try:
            path = root / "src" / "many.cls"
            path.write_text("".join(f"changed value {index}\n" for index in range(20)), encoding="utf-8")
            git(root, "add", ".")
            git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "many lines")
            seed = PreAgenticSeed("ok", "analysis", PrContextResult("ok"), (("src/many.cls", "changed"),))
            session = EvidenceSession(seed)
            tool = make_repository_inspection_tool(session, repo_root=root)
            result = tool(["changed"])
            self.assertLessEqual(len(result["matches"]), 10)
            self.assertLessEqual(max(len(match["excerpt"]) for match in result["matches"]), 300)
            self.assertTrue(any(gap["kind"] == "inspection_model_truncated" for gap in result["gaps"]))
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
            self.assertIn("documented", session.authorized_inspection_terms)
            self.assertIn("README.md", session.authorized_inspection_paths)
            second = tool(["documented"], paths=["README.md"])
            self.assertEqual(second["status"], "ok")
            self.assertEqual(
                [(match["path"], match["term"]) for match in second["matches"]],
                [("README.md", "documented")],
            )
        finally:
            handle.cleanup()

    def test_inspection_is_opt_in_through_an_explicit_allowlist(self) -> None:
        handle, root = self.repo()
        try:
            with self.assertRaisesRegex(ValueError, "not authorized"):
                inspect_repository(root, ["changed"])
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
