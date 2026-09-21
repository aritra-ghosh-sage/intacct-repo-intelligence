"""Tests for the small JSON module interface."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ia_repomap_builder import (
    BuildResult,
    PrCallerCandidate,
    PrChangedFile,
    PrContextGap,
    PrContextResult,
    PrImpactCandidate,
    PrImpactResult,
    PrSymbolCandidate,
)
from ia_repomap_builder.cli import COMMAND_SCHEMA, EXIT_ERROR, EXIT_UNAVAILABLE, main
from ia_repomap_builder.pr_analysis import BedrockSettings, load_bedrock_settings
from ia_repomap_builder.review import (
    PRMetadata,
    ReviewCheckout,
    ReviewRequest,
    ReviewRunResult,
    ReviewSetupError,
    ReviewSetupResult,
)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        self.root.mkdir()
        self.artifacts = Path(self.tempdir.name) / "artifacts"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = main(arguments, stdout=stdout, stderr=stderr)
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def _assert_public_envelope(self, payload: dict[str, object], command: str) -> None:
        self.assertEqual(payload["schema"], COMMAND_SCHEMA)
        self.assertEqual(payload["command"], command)
        self.assertEqual(payload["status"], payload["result"]["status"])
        self.assertEqual(payload["remediation"], payload["result"]["remediation"])
        self.assertIn("diagnostics", payload["result"])

    def test_missing_command_is_json_error(self) -> None:
        code, payload, _ = self._run()
        self.assertEqual(code, EXIT_ERROR)
        self.assertEqual(payload["schema"], COMMAND_SCHEMA)
        self.assertEqual(payload["status"], "error")
        self.assertIn("command is required", payload["result"]["diagnostics"][0])

    def test_review_requires_repo_without_internal_arguments(self) -> None:
        code, payload, _ = self._run(
            "review", "https://github.com/intacct/ia-app/pull/50176"
        )
        self.assertEqual(code, EXIT_ERROR)
        self._assert_public_envelope(payload, "review")
        self.assertEqual(payload["result"]["assessment"], "error")
        self.assertEqual(payload["result"]["report"], {"status": None, "assessment": None, "files": []})
        self.assertIn("--repo", payload["result"]["diagnostics"][0])

    def test_review_forwards_exact_identity_and_inspect_and_writes_report_envelope(self) -> None:
        workspace = Path(self.tempdir.name) / "workspace"
        base_sha = "a" * 40
        head_sha = "b" * 40
        report_directory = workspace / "reports" / "intacct-ia-app" / head_sha / base_sha / "run"
        result = ReviewRunResult(
            status="ok", assessment="partial", base_sha=base_sha, head_sha=head_sha,
            report_directory=report_directory, report_status="ok",
            report_assessment="partial", report_files=("pr-analysis.json", "pr-analysis.md"),
        )
        with patch("ia_repomap_builder.cli.run_review", return_value=result) as review:
            code, payload, _ = self._run(
                "review",
                "https://github.com/intacct/ia-app/pull/50176",
                "--repo",
                str(self.root),
                "--workspace",
                str(workspace),
                "--inspect",
            )

        self.assertEqual(code, 0)
        self._assert_public_envelope(payload, "review")
        review.assert_called_once()
        request = review.call_args.args[0]
        self.assertIsInstance(request, ReviewRequest)
        self.assertEqual(request.repo_root, self.root.resolve())
        self.assertEqual(request.workspace, workspace.resolve())
        self.assertTrue(request.inspect)
        self.assertEqual(payload["report_directory"], str(report_directory))
        self.assertEqual(payload["base_sha"], base_sha)
        self.assertEqual(payload["head_sha"], head_sha)
        self.assertEqual(payload["assessment"], "partial")
        self.assertEqual(payload["report"]["status"], "ok")
        self.assertEqual(payload["report"]["assessment"], "partial")
        self.assertEqual(payload["report"]["files"], ["pr-analysis.json", "pr-analysis.md"])
        self.assertEqual(payload["result"]["report"], payload["report"])
        self.assertNotIn("artifact-root", payload["request"])

    def test_review_unavailable_prepare_does_not_run_analysis(self) -> None:
        result = ReviewRunResult(status="unavailable", assessment="unavailable", remediation=("Ripwire unavailable",))
        with patch("ia_repomap_builder.cli.run_review", return_value=result):
            code, payload, _ = self._run(
                "review",
                "https://github.com/intacct/ia-app/pull/50176",
                "--repo",
                str(self.root),
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self._assert_public_envelope(payload, "review")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["assessment"], "unavailable")

    def test_partial_status_returns_success_exit_code(self) -> None:
        result = ReviewRunResult(status="partial", assessment="partial")
        with patch("ia_repomap_builder.cli.run_review", return_value=result):
            code, payload, _ = self._run(
                "review", "https://github.com/intacct/ia-app/pull/50176", "--repo", str(self.root)
            )
        self.assertEqual(code, 0)
        self._assert_public_envelope(payload, "review")
        self.assertEqual(payload["status"], "partial")

    def test_review_setup_error_is_json_error(self) -> None:
        result = ReviewRunResult(status="error", assessment="error", remediation=("PR URL must be canonical GitHub URL",))
        with patch("ia_repomap_builder.cli.run_review", return_value=result):
            code, payload, _ = self._run(
                "review",
                "https://github.com/intacct/ia-app/pull/50176",
                "--repo",
                str(self.root),
            )
        self.assertEqual(code, EXIT_ERROR)
        self._assert_public_envelope(payload, "review")
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["assessment"], "error")
        self.assertIn("canonical GitHub URL", payload["remediation"][0])

    def test_review_gh_authentication_failure_is_unavailable(self) -> None:
        result = ReviewRunResult(status="unavailable", assessment="unavailable", remediation=("gh auth status",))
        with patch("ia_repomap_builder.cli.run_review", return_value=result):
            code, payload, _ = self._run(
                "review",
                "https://github.com/intacct/ia-app/pull/50176",
                "--repo",
                str(self.root),
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self._assert_public_envelope(payload, "review")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["assessment"], "unavailable")
        self.assertIn("gh auth status", payload["remediation"][0])

    def test_review_missing_bedrock_settings_is_unavailable(self) -> None:
        result = ReviewRunResult(
            status="unavailable", assessment="unavailable",
            remediation=("BEDROCK_MODEL_ID is required; environment or .env.local",),
        )
        with patch("ia_repomap_builder.cli.run_review", return_value=result):
            code, payload, _ = self._run(
                "review",
                "https://github.com/intacct/ia-app/pull/50176",
                "--repo",
                str(self.root),
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self._assert_public_envelope(payload, "review")
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("environment or .env.local", payload["remediation"][0])

    def test_setup_dispatches_without_internal_flags(self) -> None:
        result = ReviewSetupResult(status="ok", identity={"base_sha": "a" * 40, "head_sha": "b" * 40})
        with patch("ia_repomap_builder.cli.setup_review", return_value=result) as setup:
            code, payload, _ = self._run(
                "setup", "https://github.com/intacct/ia-app/pull/50176",
                "--repo", str(self.root), "--workspace", str(self.tempdir.name),
            )
        self.assertEqual(code, 0)
        self._assert_public_envelope(payload, "setup")
        self.assertIsInstance(setup.call_args.args[0], ReviewRequest)
        self.assertEqual(payload["command"], "setup")
        self.assertEqual(payload["identity"]["base_sha"], "a" * 40)
        self.assertEqual(payload["result"]["identity"], payload["identity"])

    def test_setup_unavailable_uses_success_envelope_shape(self) -> None:
        success = ReviewSetupResult(status="ok")
        unavailable = ReviewSetupResult(status="unavailable", remediation=("Ripwire unavailable",))
        with patch("ia_repomap_builder.cli.setup_review", return_value=success):
            success_code, success_payload, _ = self._run(
                "setup", "https://github.com/intacct/ia-app/pull/50176", "--repo", str(self.root)
            )
        with patch("ia_repomap_builder.cli.setup_review", return_value=unavailable):
            unavailable_code, unavailable_payload, _ = self._run(
                "setup", "https://github.com/intacct/ia-app/pull/50176", "--repo", str(self.root)
            )

        self.assertEqual(success_code, 0)
        self.assertEqual(unavailable_code, EXIT_UNAVAILABLE)
        self._assert_public_envelope(success_payload, "setup")
        self._assert_public_envelope(unavailable_payload, "setup")
        self.assertEqual(set(success_payload), set(unavailable_payload))
        self.assertEqual(unavailable_payload["result"]["status"], "unavailable")

    def test_invalid_setup_arguments_use_setup_envelope_shape(self) -> None:
        code, payload, _ = self._run(
            "setup", "https://github.com/intacct/ia-app/pull/50176"
        )
        self.assertEqual(code, EXIT_ERROR)
        self._assert_public_envelope(payload, "setup")
        self.assertEqual(payload["result"]["base_sha"], None)
        self.assertIn("--repo", payload["result"]["diagnostics"][0])

    def test_review_help_does_not_expose_internal_paths(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "ia_repomap_builder", "review", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--repo", completed.stdout)
        self.assertIn("--workspace", completed.stdout)
        self.assertIn("--inspect", completed.stdout)
        self.assertNotIn("--artifact-root", completed.stdout)
        self.assertNotIn("--output", completed.stdout)
        self.assertNotIn("--base", completed.stdout)

        setup_help = subprocess.run(
            [sys.executable, "-m", "ia_repomap_builder", "setup", "--help"],
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertIn("--repo", setup_help)
        self.assertIn("--workspace", setup_help)
        self.assertNotIn("--inspect", setup_help)
        self.assertNotIn("--artifact-root", setup_help)

    def test_process_environment_overrides_dotenv_bedrock_settings(self) -> None:
        env_file = Path(self.tempdir.name) / ".env.local"
        env_file.write_text(
            "AWS_REGION=file-region\nBEDROCK_MODEL_ID=file-model\nAWS_PROFILE=file-profile\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "AWS_REGION": "env-region",
                "AWS_DEFAULT_REGION": "env-default-region",
                "BEDROCK_MODEL_ID": "env-model",
                "AWS_PROFILE": "env-profile",
            },
            clear=False,
        ):
            settings = load_bedrock_settings(str(env_file))
        self.assertEqual(settings, BedrockSettings("env-region", "env-model", "env-profile"))

    def test_missing_repository_has_actionable_remediation(self) -> None:
        code, payload, _ = self._run(
            "pr-context",
            "--repo",
            str(self.root / "missing"),
            "--artifact-root",
            str(self.artifacts),
            "--base",
            "origin/main",
        )
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("existing Git checkout", payload["remediation"][0])

    def test_missing_pr_context_arguments_is_json_error(self) -> None:
        code, payload, _ = self._run("pr-context", "--repo", str(self.root))
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("--artifact-root", payload["result"]["diagnostics"][0])
        self.assertIn("--base", payload["result"]["diagnostics"][0])

    def test_symbol_impact_help_lists_required_arguments(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "ia_repomap_builder", "symbol-impact", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--symbol-path", completed.stdout)
        self.assertIn("--symbol-name", completed.stdout)
        self.assertIn("--limit", completed.stdout)
        self.assertIn("--offset", completed.stdout)

    def test_missing_symbol_impact_arguments_use_impact_shape(self) -> None:
        code, payload, _ = self._run(
            "symbol-impact",
            "--repo",
            str(self.root),
            "--artifact-root",
            str(self.artifacts),
        )
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("--symbol-path", payload["result"]["diagnostics"][0])
        self.assertIn("--symbol-name", payload["result"]["diagnostics"][0])
        self.assertEqual(payload["result"]["candidates"], [])
        self.assertNotIn("changed_files", payload["result"])

    def test_symbol_impact_parse_failure_uses_impact_shape(self) -> None:
        code, payload, _ = self._run(
            "symbol-impact",
            "--repo",
            str(self.root),
            "--artifact-root",
            str(self.artifacts),
            "--symbol-path",
            "app/source/gl/Changed.cls",
            "--symbol-name",
            "changed",
            "--limit",
            "not-an-integer",
        )
        self.assertEqual(code, EXIT_ERROR)
        self.assertEqual(payload["result"]["candidates"], [])
        self.assertNotIn("changed_files", payload["result"])

    def test_pr_context_wraps_result_and_does_not_prepare(self) -> None:
        result = PrContextResult(status="unavailable", diagnostics=["prepared index manifest is missing"])
        with (
            patch("ia_repomap_builder.cli.build_pr_context", return_value=result) as build,
            patch("ia_repomap_builder.cli.prepare_repomap") as prepare,
        ):
            code, payload, _ = self._run(
                "pr-context",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--base",
                "origin/main",
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        build.assert_called_once()
        prepare.assert_not_called()
        self.assertEqual(payload["command"], "pr-context")
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("explicit `prepare` command", payload["remediation"][0])
        self.assertEqual(payload["request"]["history_commits"], 500)

    def test_prepare_constructs_request_and_returns_json(self) -> None:
        result = BuildResult(engine="ripwire", status="ok", identity={"head": "head"})
        with patch("ia_repomap_builder.cli.prepare_repomap", return_value=result) as prepare:
            code, payload, _ = self._run(
                "prepare",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
            )
        self.assertEqual(code, 0)
        request = prepare.call_args.args
        self.assertEqual(request, (self.root.resolve(), self.artifacts.resolve()))
        self.assertEqual(payload["result"]["identity"]["head"], "head")

    def test_symbol_impact_constructs_request_without_implicit_preparation(self) -> None:
        result = PrImpactResult(status="unavailable", diagnostics=["prepared index manifest is missing"])
        with (
            patch("ia_repomap_builder.cli.build_symbol_impact", return_value=result) as impact,
            patch("ia_repomap_builder.cli.prepare_repomap") as prepare,
            patch("ia_repomap_builder.cli.build_pr_context") as context,
        ):
            code, payload, _ = self._run(
                "symbol-impact",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--symbol-path",
                "app/source/gl/Changed.cls",
                "--symbol-name",
                "changed",
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        request = impact.call_args.args[0]
        self.assertEqual(request.repo_root, self.root.resolve())
        self.assertEqual(request.artifact_root, self.artifacts.resolve())
        self.assertEqual(request.symbol_path, "app/source/gl/Changed.cls")
        self.assertEqual(request.symbol_name, "changed")
        self.assertEqual(request.limit, 20)
        self.assertEqual(request.offset, 0)
        prepare.assert_not_called()
        context.assert_not_called()
        self.assertEqual(payload["command"], "symbol-impact")
        self.assertEqual(payload["request"]["limit"], 20)
        self.assertEqual(payload["request"]["offset"], 0)
        self.assertIn("explicit `prepare` command", payload["remediation"][0])

    def test_symbol_impact_forwards_paging_and_serializes_result(self) -> None:
        raw_xml = '<impact schema="ripwire.impact/v1" reaches="1" shown="1"><s n="caller" p="gl/Caller.cls:20"/></impact>'
        result = PrImpactResult(
            status="ok",
            candidates=[PrImpactCandidate("app/source/gl/Caller.cls", "caller", 20, "method")],
            raw_xml=raw_xml,
            gaps=[PrContextGap(kind="impact_lower_bound", detail="floor")],
            metrics={"reaches": 1, "next_offset": 9},
            identity={"head": "head"},
        )
        with patch("ia_repomap_builder.cli.build_symbol_impact", return_value=result) as impact:
            code, payload, _ = self._run(
                "symbol-impact",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--symbol-path",
                "app/source/gl/Changed.cls",
                "--symbol-name",
                "changed",
                "--limit",
                "7",
                "--offset",
                "2",
            )
        self.assertEqual(code, 0)
        request = impact.call_args.args[0]
        self.assertEqual((request.limit, request.offset), (7, 2))
        self.assertEqual(payload["request"]["limit"], 7)
        self.assertEqual(payload["request"]["offset"], 2)
        self.assertEqual(payload["request"]["symbol_path"], "app/source/gl/Changed.cls")
        self.assertEqual(payload["request"]["symbol_name"], "changed")
        self.assertEqual(payload["result"]["candidates"][0]["line"], 20)
        self.assertEqual(payload["result"]["gaps"][0]["kind"], "impact_lower_bound")
        self.assertEqual(payload["result"]["metrics"]["next_offset"], 9)
        self.assertEqual(payload["result"]["raw_xml"], raw_xml)
        self.assertEqual(payload["result"]["identity"]["head"], "head")

    def test_symbol_impact_unresolved_symbol_has_candidate_remediation(self) -> None:
        result = PrImpactResult(
            status="unavailable",
            diagnostics=["ripwire: symbol is ambiguous: changed"],
        )
        with patch("ia_repomap_builder.cli.build_symbol_impact", return_value=result):
            code, payload, _ = self._run(
                "symbol-impact",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--symbol-path",
                "app/source/gl/Changed.cls",
                "--symbol-name",
                "changed",
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self.assertIn("exact symbol path and name returned by `pr-context`", payload["remediation"][0])

    def test_symbol_impact_error_maps_to_error_exit(self) -> None:
        result = PrImpactResult(status="error", diagnostics=["malformed impact XML"])
        with patch("ia_repomap_builder.cli.build_symbol_impact", return_value=result):
            code, payload, _ = self._run(
                "symbol-impact",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--symbol-path",
                "app/source/gl/Changed.cls",
                "--symbol-name",
                "changed",
            )
        self.assertEqual(code, EXIT_ERROR)
        self.assertEqual(payload["status"], "error")

    def test_symbol_impact_optional_output_matches_stdout(self) -> None:
        result = PrImpactResult(status="ok", raw_xml='<impact schema="ripwire.impact/v1"/>')
        output = Path(self.tempdir.name) / "impact.json"
        with patch("ia_repomap_builder.cli.build_symbol_impact", return_value=result):
            stdout = io.StringIO()
            code = main(
                [
                    "symbol-impact",
                    "--repo",
                    str(self.root),
                    "--artifact-root",
                    str(self.artifacts),
                    "--symbol-path",
                    "app/source/gl/Changed.cls",
                    "--symbol-name",
                    "changed",
                    "--output",
                    str(output),
                ],
                stdout=stdout,
            )
        self.assertEqual(code, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), stdout.getvalue())

    def test_symbol_impact_rejects_existing_output_without_dispatch(self) -> None:
        output = Path(self.tempdir.name) / "impact.json"
        output.write_text("keep\n", encoding="utf-8")
        with patch("ia_repomap_builder.cli.build_symbol_impact") as impact:
            code, payload, _ = self._run(
                "symbol-impact",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--symbol-path",
                "app/source/gl/Changed.cls",
                "--symbol-name",
                "changed",
                "--output",
                str(output),
            )
        self.assertEqual(code, EXIT_ERROR)
        impact.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(payload["request"]["symbol_path"], "app/source/gl/Changed.cls")
        self.assertIn("already exists", payload["result"]["diagnostics"][0])

    def test_optional_output_matches_stdout_and_is_external(self) -> None:
        result = PrContextResult(
            status="ok",
            raw_xml='<pr-context schema="ripwire.pr-context/v1"/>',
            changed_files=[],
        )
        output = Path(self.tempdir.name) / "result.json"
        with patch("ia_repomap_builder.cli.build_pr_context", return_value=result):
            stdout = io.StringIO()
            code = main(
                [
                    "pr-context",
                    "--repo",
                    str(self.root),
                    "--artifact-root",
                    str(self.artifacts),
                    "--base",
                    "origin/main",
                    "--output",
                    str(output),
                ],
                stdout=stdout,
            )
        self.assertEqual(code, 0)
        self.assertTrue(output.is_file())
        self.assertEqual(output.read_text(encoding="utf-8"), stdout.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["result"]["raw_xml"], result.raw_xml)

    def test_nested_callers_are_json_serialized(self) -> None:
        result = PrContextResult(
            status="ok",
            changed_files=[
                PrChangedFile(
                    path="app/source/gl/Changed.cls",
                    change="M",
                    symbols=(
                        PrSymbolCandidate(
                            "app/source/gl/Changed.cls",
                            "changed",
                            10,
                            "method",
                            callers=(
                                PrCallerCandidate(
                                    "app/source/gl/Caller.cls",
                                    "caller",
                                    20,
                                    "method",
                                ),
                            ),
                        ),
                    ),
                ),
            ],
        )
        with patch("ia_repomap_builder.cli.build_pr_context", return_value=result):
            code, payload, _ = self._run(
                "pr-context",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--base",
                "origin/main",
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["result"]["changed_files"][0]["symbols"][0]["callers"][0]["line"],
            20,
        )

    def test_output_inside_repository_is_rejected_before_execution(self) -> None:
        output = self.root / "result.json"
        with patch("ia_repomap_builder.cli.build_pr_context") as build:
            code, payload, _ = self._run(
                "pr-context",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--base",
                "origin/main",
                "--output",
                str(output),
            )
        self.assertEqual(code, EXIT_ERROR)
        build.assert_not_called()
        self.assertIn("outside the target repository", payload["result"]["diagnostics"][0])
        self.assertFalse(output.exists())

    def test_existing_output_is_rejected_without_overwrite(self) -> None:
        output = Path(self.tempdir.name) / "result.json"
        output.write_text("keep\n", encoding="utf-8")
        with patch("ia_repomap_builder.cli.build_pr_context") as build:
            code, payload, _ = self._run(
                "pr-context",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--base",
                "origin/main",
                "--output",
                str(output),
            )
        self.assertEqual(code, EXIT_ERROR)
        build.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), "keep\n")
        self.assertIn("already exists", payload["result"]["diagnostics"][0])

    def test_unavailable_binary_has_remediation(self) -> None:
        result = PrContextResult(status="unavailable", diagnostics=["Ripwire binary unavailable"])
        with patch("ia_repomap_builder.cli.build_pr_context", return_value=result):
            code, payload, _ = self._run(
                "pr-context",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--base",
                "origin/main",
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self.assertIn("RIPWIRE_BIN", payload["remediation"][0])

    def test_missing_history_capability_has_remediation(self) -> None:
        result = PrContextResult(
            status="unavailable",
            diagnostics=["Ripwire binary does not support --pr-history-commits"],
        )
        with patch("ia_repomap_builder.cli.build_pr_context", return_value=result):
            code, payload, _ = self._run(
                "pr-context",
                "--repo",
                str(self.root),
                "--artifact-root",
                str(self.artifacts),
                "--base",
                "origin/main",
            )
        self.assertEqual(code, EXIT_UNAVAILABLE)
        self.assertIn("RIPWIRE_BIN", payload["remediation"][0])


if __name__ == "__main__":
    unittest.main()
