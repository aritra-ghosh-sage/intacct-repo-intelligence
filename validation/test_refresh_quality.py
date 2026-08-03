from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.refresh_quality import (
    RefreshQualityError,
    approval_sha256,
    build_quality_payload,
    compare_repository_quality,
    load_quality_report,
    materialized_quality_run,
    quality_report,
    reference_quality_run,
    resolve_reference_quality_run,
    validate_quality_report,
    validate_quality_run,
    write_quality_report_atomic,
)


class RefreshQualityTests(unittest.TestCase):
    def _payload(self) -> dict[str, object]:
        return build_quality_payload(
            parent={
                "catalog_build_id": 6,
                "build_token": "token",
                "content_fingerprint": "a" * 64,
            },
            delta_contract_version=3,
            runtime_fingerprint="b" * 64,
            repositories=[
                {
                    "repo_key": "service",
                    "commit_sha": "c" * 40,
                    "manifest_hash": "d" * 64,
                    "builder_plan_hash": "e" * 64,
                    "diagnostics": [],
                    "counts": {"test_cases": 1},
                }
            ],
            global_counts={"active_api_version_compatibility": 1},
        )

    def test_report_hashes_payload_only_without_newline(self) -> None:
        payload = self._payload()
        report = quality_report(payload)
        self.assertEqual(report["approval_sha256"], approval_sha256(payload))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            write_quality_report_atomic(path, report)
            self.assertFalse(path.read_bytes().endswith(b"\n"))
            self.assertEqual(load_quality_report(path), report)

    def test_unknown_fields_and_wrong_hash_are_rejected(self) -> None:
        report = quality_report(self._payload())
        report["unknown"] = True
        with self.assertRaises(RefreshQualityError):
            validate_quality_report(report)
        report = quality_report(self._payload())
        report["approval_sha256"] = "0" * 64
        with self.assertRaisesRegex(RefreshQualityError, "hash mismatch"):
            validate_quality_report(report)

    def test_comparison_rejects_zero_drop_nondecrease_and_new_diagnostic(self) -> None:
        failures = compare_repository_quality(
            parent_counts={
                "test_cases": 3,
                "test_requests": 3,
                "test_endpoint_links": 3,
                "test_entity_links": 3,
            },
            candidate_counts={
                "test_cases": 2,
                "test_requests": 2,
                "test_endpoint_links": 2,
                "test_entity_links": 0,
            },
            ran_builders=("gherkin_coverage",),
            candidate_diagnostics=[
                {
                    "diagnostic_key": "f" * 64,
                    "severity": "error",
                    "source_path": "changed.feature",
                }
            ],
            changed_paths=("changed.feature",),
        )
        self.assertIn("test_entity_links: parent=3 candidate=0", failures)
        self.assertIn("test_endpoint_links: parent=3 candidate=2", failures)
        self.assertIn(f"new diagnostic: {'f' * 64}", failures)

    def test_quality_run_references_are_backward_and_not_chained(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE repo_index_runs(id INTEGER PRIMARY KEY,repo_id INTEGER,validation_summary TEXT)"
        )
        materialized = materialized_quality_run(
            approval="a" * 64,
            runtime_fingerprint="b" * 64,
            source_commit_sha="c" * 40,
            diagnostics=(),
            counts={"test_cases": 1},
        )
        conn.execute(
            "INSERT INTO repo_index_runs VALUES (1,7,?)",
            (json.dumps(materialized),),
        )
        reference = reference_quality_run(approval="a" * 64, baseline_run_id=1)
        self.assertEqual(
            resolve_reference_quality_run(conn, 7, 2, reference),
            (1, materialized),
        )
        with self.assertRaisesRegex(RefreshQualityError, "point backward"):
            resolve_reference_quality_run(conn, 7, 1, reference)
        conn.execute(
            "INSERT INTO repo_index_runs VALUES (2,7,?)",
            (json.dumps(reference),),
        )
        chained = reference_quality_run(approval="a" * 64, baseline_run_id=2)
        with self.assertRaisesRegex(RefreshQualityError, "chains"):
            resolve_reference_quality_run(conn, 7, 3, chained)

    def test_quality_run_rejects_unknown_fields(self) -> None:
        summary = materialized_quality_run(
            approval="a" * 64,
            runtime_fingerprint="b" * 64,
            source_commit_sha="c" * 40,
            diagnostics=(),
            counts={},
        )
        summary["unknown"] = True
        with self.assertRaises(RefreshQualityError):
            validate_quality_run(summary)


if __name__ == "__main__":
    unittest.main()
