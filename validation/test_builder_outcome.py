from __future__ import annotations

import unittest

from scripts.builder_outcome import BuilderDiagnostic, BuilderOutcome


class BuilderOutcomeTests(unittest.TestCase):
    def test_outcome_is_deterministic_and_retains_metrics(self) -> None:
        diagnostic = BuilderDiagnostic(
            builder="openapi_scan",
            code="openapi_yaml_parse_error",
            severity="error",
            source_path="spec.yaml",
            source_blob_sha="a" * 40,
            identity={"line": "3", "kind": "yaml"},
        )
        outcome = BuilderOutcome(1, {"rows_indexed": 4, "files_seen": 5}, (diagnostic,))
        self.assertEqual(outcome.metrics["rows_indexed"], 4)
        self.assertEqual(outcome.to_json(), outcome.to_json())
        self.assertEqual(len(diagnostic.diagnostic_key), 64)

    def test_negative_or_malformed_metrics_fail(self) -> None:
        with self.assertRaises(ValueError):
            BuilderOutcome(-1, {})
        with self.assertRaises(ValueError):
            BuilderOutcome(None, {"bad": -1})
        with self.assertRaises(ValueError):
            BuilderOutcome(None, {"bad": True})
        with self.assertRaises(ValueError):
            BuilderDiagnostic("scan", "code", "warning", "<unknown>", None, {})


if __name__ == "__main__":
    unittest.main()
