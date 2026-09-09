"""Tests for the small JSON module interface."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ia_repomap_builder import BuildResult, PrContextResult
from ia_repomap_builder.cli import COMMAND_SCHEMA, EXIT_ERROR, EXIT_UNAVAILABLE, main


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

    def test_missing_command_is_json_error(self) -> None:
        code, payload, _ = self._run()
        self.assertEqual(code, EXIT_ERROR)
        self.assertEqual(payload["schema"], COMMAND_SCHEMA)
        self.assertEqual(payload["status"], "error")
        self.assertIn("command is required", payload["result"]["diagnostics"][0])

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
        request = prepare.call_args.args[0]
        self.assertEqual(request.repo_root, self.root.resolve())
        self.assertEqual(request.artifact_root, self.artifacts.resolve())
        self.assertEqual(payload["result"]["identity"]["head"], "head")

    def test_optional_output_matches_stdout_and_is_external(self) -> None:
        result = PrContextResult(status="ok", raw_xml='<pr-context schema="ripwire.pr-context/v1"/>')
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
