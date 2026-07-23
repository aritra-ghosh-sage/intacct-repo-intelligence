from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.eval_catalog_response import load_cases, score_case
from scripts.verify_catalog_eval_dataset import query_command, run_query


class CatalogEvalResponseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = load_cases(Path("evals/catalog_eval_cases.jsonl"))

    def test_case_file_has_hard_cases_and_multiple_query_families(self) -> None:
        self.assertGreaterEqual(len(self.cases), 10)
        self.assertTrue(any(case["hard_case"] for case in self.cases))
        self.assertGreaterEqual(
            len({case["query"]["command"] for case in self.cases}),
            4,
        )

    def test_good_summary_scores_reasonably_high(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "stats_top_languages")
        answer = "The catalog has 52,104 files across 13 languages, with XML leading, followed by PHP, JSON, SQL, and YAML."
        score = score_case(answer, case)
        self.assertGreaterEqual(score.overall, 0.7)
        self.assertEqual(score.uncertainty_score, 1.0)

    def test_counts_only_summary_is_penalized_when_primary_signal_is_missing(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "stats_top_languages")
        answer = "The catalog has 52,104 files across 13 languages."
        score = score_case(answer, case)
        self.assertEqual(score.verdict, "quality_fail")
        self.assertLess(score.overall, 0.75)
        self.assertIn("missing_primary_signal", score.notes)

    def test_uncertainty_case_rewards_explicit_qualification(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "coverage_drop_top_endpoint_hard_case")
        answer = (
            "The payload shows four uncovered APBill endpoints, but it appears incomplete so I cannot confirm the full endpoint count from this data alone."
        )
        score = score_case(answer, case)
        self.assertGreaterEqual(score.uncertainty_score, 1.0)
        self.assertGreaterEqual(score.overall, 0.75)

    def test_fabricated_path_is_a_hard_failure(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "stats_top_languages")
        answer = "The catalog has 52,104 files across 13 languages. XML leads. See app/source/Fabricated.cls."
        score = score_case(answer, case)
        self.assertEqual(score.verdict, "hard_fail")
        self.assertEqual(score.overall, 0.0)
        self.assertIn("unsupported_path:app/source/Fabricated.cls", score.hallucination_flags)

    def test_reversed_rows_still_require_computed_top_value(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "stats_reverse_order_hard_case")
        answer = "The catalog has 52,104 files across 13 languages. TypeScript is the dominant language."
        score = score_case(answer, case)
        self.assertEqual(score.verdict, "hard_fail")
        self.assertIn("wrong_top_value:typescript", score.hallucination_flags)

    def test_cli_emits_json(self) -> None:
        answer = "The catalog has 52,104 files across 13 languages, with XML leading."
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/eval_catalog_response.py",
                "--cases",
                "evals/catalog_eval_cases.jsonl",
                "--case-id",
                "stats_top_languages",
                "--actual-output",
                answer,
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["summary"]["case_count"], 1)
        self.assertIn("results", payload)
        self.assertEqual(payload["results"][0]["case_id"], "stats_top_languages")

    def test_cli_requires_one_answer_per_case(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/eval_catalog_response.py",
                "--cases",
                "evals/catalog_eval_cases.jsonl",
                "--case-id",
                "stats_top_languages",
                "--case-id",
                "toplevel_directory_distribution",
                "--actual-output",
                "one answer",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("require exactly one case", proc.stderr)

    def test_verifier_targets_the_selected_database(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "stats_top_languages")
        custom_db = Path("/tmp/custom-catalog.db")
        with patch("scripts.verify_catalog_eval_dataset.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = json.dumps(
                {
                    "contract_version": 1,
                    "query": {"command": "stats", "args": {}},
                    "status": "ok",
                    "data": {},
                    "summary": {},
                    "error": None,
                }
            )
            run_mock.return_value.stderr = ""
            run_query(case, custom_db)
        self.assertEqual(run_mock.call_args.kwargs["env"]["CATALOG_DB"], str(custom_db.resolve()))

    def test_verifier_passes_db_to_entity_queries(self) -> None:
        case = next(case for case in self.cases if case["case_id"] == "coverage_apbill_uncovered_endpoints")
        command = query_command(case, Path("/tmp/custom-catalog.db"))
        self.assertIn("--db", command)
        self.assertIn(str(Path("/tmp/custom-catalog.db").resolve()), command)


if __name__ == "__main__":
    unittest.main()
