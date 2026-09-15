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
from ia_repomap_builder.pr_analysis_output import EvidencePayload, write_pr_analysis_bundle


class PRAnalysisOutputTests(unittest.TestCase):
    def report(self, raw: bytes = b"<pr-context/>\n") -> PRAnalysisReportV1:
        digest = hashlib.sha256(raw).hexdigest()
        return PRAnalysisReportV1.model_validate({
            "schema": "ia-repomap.pr-analysis/v1",
            "status": "ok",
            "phase": "analysis",
            "request": {"repository": "intacct/ia-app", "base": "a" * 40, "analysis_schema": "ia-repomap.pr-analysis/v1"},
            "identity": {"repository": "intacct/ia-app", "head": "b" * 40, "base": "a" * 40, "merge_base": "a" * 40, "configuration_digest": "c" * 64, "engine_identity": "ripwire-test"},
            "summary": {"purpose": "test", "behavioral_change": "candidate", "confidence": "candidate"},
            "changed_files": [{"path": "app/source/example.cls", "change": "M", "symbols": [], "evidence_ids": ["pr-context-001"]}],
            "blast_radius": [], "test_areas": [], "gaps": [{"kind": "lower_bound", "detail": "not exhaustive"}],
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


if __name__ == "__main__":
    unittest.main()
