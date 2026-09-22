"""Focused tests for external PR-analysis bundle persistence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ia_repomap_builder.pr_analysis import PRAnalysisReportV1
from ia_repomap_builder.pr_analysis_output import (
    EvidencePayload,
    write_pr_analysis_bundle,
)


class PRAnalysisOutputTests(unittest.TestCase):
    def report(
        self,
        raw: bytes = b"<pr-context/>\n",
        impacted_files: list[dict[str, object]] | None = None,
        assessment: str = "complete",
        gaps: list[dict[str, object]] | None = None,
        test_inventory_coverage: dict[str, object] | None = None,
    ) -> PRAnalysisReportV1:
        digest = hashlib.sha256(raw).hexdigest()
        return PRAnalysisReportV1.model_validate({
            "schema": "ia-repomap.pr-analysis/v1",
            "status": "ok",
            "assessment": assessment,
            "phase": "analysis",
            "request": {"repository": "intacct/ia-app", "base": "a" * 40, "analysis_schema": "ia-repomap.pr-analysis/v1"},
            "identity": {"repository": "intacct/ia-app", "head": "b" * 40, "base": "a" * 40, "merge_base": "a" * 40, "configuration_digest": "c" * 64, "engine_identity": "ripwire-test"},
            "summary": {"purpose": "test", "behavioral_change": "candidate", "confidence": "candidate"},
            "changed_files": [{"path": "app/source/example.cls", "change": "M", "symbols": [], "evidence_ids": ["pr-context-001"]}],
            "impacted_files": impacted_files or [],
            "blast_radius": [], "test_areas": [],
            "test_inventory_coverage": test_inventory_coverage,
            "gaps": gaps or [{"kind": "lower_bound", "detail": "not exhaustive"}],
            "evidence": [{"evidence_id": "pr-context-001", "kind": "pr_context_xml", "relative_path": "evidence/pr-context.xml", "sha256": digest}],
            "diagnostics": [], "remediation": [], "metrics": {"impact_calls": 0},
            "agent": {"invoked": False, "model_id": "fake", "region": "test", "prompt_version": "v1", "tool_contract_version": "v1", "coordinator_version": "v1"},
        })

    def payload(self, report: PRAnalysisReportV1, raw: bytes = b"<pr-context/>\n") -> list[EvidencePayload]:
        return [EvidencePayload("pr-context-001", raw)]

    def test_writes_deterministic_json_markdown_and_exact_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            report = self.report()
            write_pr_analysis_bundle(output, report, self.payload(report), repo_root=Path(root) / "repo")
            parsed = json.loads((output / "pr-analysis.json").read_text())
            self.assertEqual(parsed["schema"], "ia-repomap.pr-analysis/v1")
            self.assertEqual((output / "evidence/pr-context.xml").read_bytes(), b"<pr-context/>\n")
            self.assertIn("# PR analysis", (output / "pr-analysis.md").read_text())
            first = (output / "pr-analysis.json").read_bytes()
            with self.assertRaises(FileExistsError):
                write_pr_analysis_bundle(output, report, self.payload(report), repo_root=Path(root) / "repo")
            self.assertEqual(first, (output / "pr-analysis.json").read_bytes())

    def test_markdown_lists_all_impacted_files_and_dependent_symbol_counts(self) -> None:
        impacted_files = [
            {
                "changed_path": "app/source/example.cls",
                "path": f"app/source/impact/{index}.cls",
                "dependent_symbols": index,
                "confidence": "candidate",
                "evidence_ids": ["pr-context-001"],
            }
            for index in range(1, 9)
        ]
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            report = self.report(impacted_files=impacted_files)
            write_pr_analysis_bundle(
                output,
                report,
                self.payload(report),
                repo_root=Path(root) / "repo",
            )
            markdown = (output / "pr-analysis.md").read_text()
            self.assertIn("## Candidate impacted files", markdown)
            for index in range(1, 9):
                self.assertIn(f"`app/source/impact/{index}.cls`", markdown)
                self.assertIn(f"dependent symbols: {index}", markdown)

    def test_markdown_renders_impact_truncation_next_to_impacted_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            report = self.report(
                impacted_files=[{
                    "changed_path": "app/source/example.cls",
                    "path": "app/source/impact/one.cls",
                    "dependent_symbols": 3,
                    "confidence": "candidate",
                    "evidence_ids": ["pr-context-001"],
                }],
                assessment="partial",
                gaps=[{
                    "kind": "impact_truncated",
                    "detail": "impact files capped at the configured limit",
                    "count": 165,
                }],
            )
            write_pr_analysis_bundle(
                output,
                report,
                self.payload(report),
                repo_root=Path(root) / "repo",
            )
            markdown = (output / "pr-analysis.md").read_text()
            impacted_index = markdown.index("## Candidate impacted files")
            truncation_index = markdown.index("Impact truncation")
            blast_radius_index = markdown.index("## Lower-bound blast radius")
            self.assertLess(impacted_index, truncation_index)
            self.assertLess(truncation_index, blast_radius_index)
            self.assertIn(
                "- `impact_truncated`: impact files capped at the configured limit (count: 165)",
                markdown,
            )

    def test_markdown_renders_assessment_and_triggering_gap_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            report = self.report(
                assessment="partial",
                gaps=[
                    {"kind": "graph_unresolved", "detail": "edge unresolved"},
                    {"kind": "impact_lower_bound", "detail": "floor only"},
                ],
            )
            write_pr_analysis_bundle(
                output,
                report,
                self.payload(report),
                repo_root=Path(root) / "repo",
            )
            markdown = (output / "pr-analysis.md").read_text()
            self.assertIn("- assessment: `partial`", markdown)
            self.assertIn("- assessment gaps: `graph_unresolved`", markdown)
            self.assertNotIn("assessment gaps: `impact_lower_bound`", markdown)

    def test_existing_empty_directory_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            output.mkdir()
            report = self.report()
            write_pr_analysis_bundle(output, report, self.payload(report), repo_root=Path(root) / "repo")
            self.assertTrue((output / "pr-analysis.json").exists())

    def test_rejects_internal_destination_and_bad_digest_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            report = self.report()
            with self.assertRaises(ValueError):
                write_pr_analysis_bundle(root_path / "repo" / "bundle", report, self.payload(report), repo_root=root_path / "repo")
            output = root_path / "bundle"
            bad = EvidencePayload("pr-context-001", b"different")
            with self.assertRaises(ValueError):
                write_pr_analysis_bundle(output, report, [bad], repo_root=root_path / "repo")
            self.assertFalse(output.exists())

    def test_rejects_nonempty_destination_and_invalid_evidence_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            output.mkdir()
            (output / "keep").write_text("keep")
            report = self.report()
            with self.assertRaises(FileExistsError):
                write_pr_analysis_bundle(output, report, self.payload(report), repo_root=Path(root) / "repo")
            payload = self.payload(report)
            invalid = report.model_copy(update={"evidence": [report.evidence[0].model_copy(update={"relative_path": "../escape.xml"})]})
            with self.assertRaises(ValueError):
                write_pr_analysis_bundle(Path(root) / "other", invalid, payload, repo_root=Path(root) / "repo")
            self.assertTrue((output / "keep").exists())

    def test_rejects_destination_beneath_immutable_ripwire_cache(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            artifact_root = root_path / "artifacts"
            cache = artifact_root / "repo" / ("b" * 40) / ("c" * 64) / "ripwire-test"
            cache.mkdir(parents=True)
            (cache / "manifest.json").write_text("{}", encoding="utf-8")
            (cache / "index.lean.ripwirecache").write_bytes(b"lean")
            (cache / "index.rich.ripwirecache").write_bytes(b"rich")
            report = self.report()
            destination = cache / "reports" / "run-1"
            with self.assertRaises(ValueError):
                write_pr_analysis_bundle(
                    destination,
                    report,
                    self.payload(report),
                    repo_root=root_path / "repo",
                    artifact_root=artifact_root,
                )
            self.assertFalse(destination.exists())

    def test_post_publish_failure_removes_new_bundle_without_staging_cleanup_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            report = self.report()
            calls = 0
            original_fsync = os.fsync

            def fail_parent_fsync(fd: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 5:
                    raise OSError("parent fsync failed")
                original_fsync(fd)

            with patch(
                "ia_repomap_builder.pr_analysis_output.os.fsync",
                side_effect=fail_parent_fsync,
            ):
                with self.assertRaises(OSError):
                    write_pr_analysis_bundle(
                        output,
                        report,
                        self.payload(report),
                        repo_root=Path(root) / "repo",
                    )
            self.assertFalse(output.exists())

    def test_markdown_renders_test_inventory_coverage_section(self) -> None:
        coverage = {
            "status": "ok",
            "findings": [{
                "changed_path": "app/source/example.cls",
                "status": "gap",
                "matched_suite_ids": [],
                "match_basis": "none",
                "reason": "No inventory suite matched",
                "evidence_ids": ["test-inventory-001"],
            }],
            "gaps": [{
                "changed_path": "app/source/example.cls",
                "suggested_area": "Add test coverage for app/source/example.cls",
                "suggested_tags": ["@example"],
                "reason": "No inventory suite matched",
                "evidence_ids": ["test-coverage-gap-001"],
            }],
            "suggested_artifacts": [{
                "evidence_id": "test-coverage-gap-001",
                "relative_path": "evidence/coverage/suggested/example.feature.suggested",
                "description": "Suggested scaffold scenario for app/source/example.cls",
            }],
            "metrics": {"changed_paths": 1, "covered": 0, "partial": 0, "gap": 1},
            "diagnostics": [],
        }
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "bundle"
            raw = b"<pr-context/>\n"
            inventory_bytes = b"{}"
            stub_bytes = b"stub"
            report = PRAnalysisReportV1.model_validate({
                "schema": "ia-repomap.pr-analysis/v1",
                "status": "ok",
                "assessment": "complete",
                "phase": "analysis",
                "request": {"repository": "intacct/ia-app", "base": "a" * 40, "analysis_schema": "ia-repomap.pr-analysis/v1"},
                "identity": {"repository": "intacct/ia-app", "head": "b" * 40, "base": "a" * 40, "merge_base": "a" * 40, "configuration_digest": "c" * 64, "engine_identity": "ripwire-test"},
                "summary": {"purpose": "test", "behavioral_change": "candidate", "confidence": "candidate"},
                "changed_files": [{"path": "app/source/example.cls", "change": "M", "symbols": [], "evidence_ids": ["pr-context-001"]}],
                "impacted_files": [],
                "blast_radius": [], "test_areas": [],
                "test_inventory_coverage": coverage,
                "gaps": [],
                "evidence": [
                    {"evidence_id": "pr-context-001", "kind": "pr_context_xml", "relative_path": "evidence/pr-context.xml", "sha256": hashlib.sha256(raw).hexdigest()},
                    {"evidence_id": "test-inventory-001", "kind": "test_inventory", "relative_path": "evidence/test-inventory.json", "sha256": hashlib.sha256(inventory_bytes).hexdigest()},
                    {"evidence_id": "test-coverage-gap-001", "kind": "suggested_test_stub", "relative_path": "evidence/coverage/suggested/example.feature.suggested", "sha256": hashlib.sha256(stub_bytes).hexdigest()},
                ],
                "diagnostics": [], "remediation": [], "metrics": {"impact_calls": 0},
                "agent": {"invoked": False, "model_id": "fake", "region": "test", "prompt_version": "v1", "tool_contract_version": "v1", "coordinator_version": "v1"},
            })
            payload = [
                EvidencePayload("pr-context-001", raw),
                EvidencePayload("test-inventory-001", inventory_bytes),
                EvidencePayload("test-coverage-gap-001", stub_bytes),
            ]
            write_pr_analysis_bundle(output, report, payload, repo_root=Path(root) / "repo")
            markdown = (output / "pr-analysis.md").read_text()
            self.assertIn("## Test inventory coverage", markdown)
            self.assertIn("`gap` `app/source/example.cls`", markdown)
            self.assertIn("Suggested corrective tests", markdown)
            self.assertIn("Suggested test scaffolds", markdown)
            self.assertTrue((output / "evidence/coverage/suggested/example.feature.suggested").is_file())


if __name__ == "__main__":
    unittest.main()
