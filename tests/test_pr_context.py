from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ia_repomap_builder import (
    BuildResult,
    PHP_FAMILY_EXTENSIONS,
    PrepareRepoMapRequest,
    PrContextRequest,
    PrSymbolCandidate,
    RepoMapConfig,
    build_pr_context,
    check_repomap_readiness,
    load_repomap_config,
    prepare_repomap,
)
from ia_repomap_builder.pr_context import (
    _GitChange,
    _hunk_line_ranges,
    _in_scope_changes,
    _parse_pr_context_xml,
    _select_hunk_symbols,
)
from ia_repomap_builder.readiness import (
    _CacheValidation,
    _engine_identity,
    _validate_ripwire_lean_cache,
    _verify_ripwire_pr_history,
    artifact_locations,
)


CONFIG = """\
schema_version = 1
engine = "ripwire"
scope = ["app/source"]
token_budget = 4000
php_family_extensions = [
  ".php", ".phtml", ".cls", ".ent", ".inc", ".cqry", ".rpt",
  ".menu", ".pol", ".wfl", ".shortcuts", ".qry", ".bin", ".map",
]
map_php_scope = ["app/source"]
"""

ENGINE = {
    "name": "ripwire",
    "binary": "/bin/ripwire",
    "version": "ripwire test-build",
    "patch": "ripwire-v0.4.0-intacct-repomap.patch",
    "patch_sha256": "patch-digest",
    "binary_sha256": "binary-digest",
    "features": ["pr-history-commits"],
    "id": "engine-id",
}

PR_XML = """\
<pr-context schema="ripwire.pr-context/v1" files="2" budget_tokens="8000"
 est_tokens="30" trim_level="0" truncated="none" graph_ambiguous="1"
 graph_unresolved="2" counts_floor="1" history_scope="head-count"
 history_commits="500">
  <file p="gl/Added.ent" symbols="2">
    <changed-symbols count="2">
      <s t="cls" n="Added" p="gl/Added.ent:2"/>
      <s t="method" n="run" p="gl/Added.ent:3"/>
    </changed-symbols>
  </file>
</pr-context>
"""


class PrContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        self.artifacts = Path(self.tempdir.name) / "artifacts"
        (self.root / "app/source/gl").mkdir(parents=True)
        (self.root / "app/source/gl/Before.cls").write_text(
            "<?php class Before {}\n", encoding="utf-8"
        )
        (self.root / ".ia-repomap.toml").write_text(CONFIG, encoding="utf-8")
        self._git("init", "-q")
        self._git("config", "user.email", "repomap@example.invalid")
        self._git("config", "user.name", "repomap")
        self._git("add", ".")
        self._git("commit", "-qm", "base")
        (self.root / "app/source/gl/Added.ent").write_text(
            "<?php class Added { function run() {} }\n", encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-qm", "head")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _config(self) -> RepoMapConfig:
        return load_repomap_config(self.root)

    def test_maintained_marker_template_matches_contract(self) -> None:
        template = (
            Path(__file__).parents[1]
            / "ia_repomap_builder"
            / "templates"
            / ".ia-repomap.toml"
        )
        (self.root / ".ia-repomap.toml").write_text(
            template.read_text(encoding="utf-8"), encoding="utf-8"
        )

        config = self._config()
        self.assertEqual(config.schema_version, 1)
        self.assertEqual(config.engine, "ripwire")
        self.assertEqual(config.scope, ("app/source",))
        self.assertEqual(config.token_budget, 4000)
        self.assertEqual(config.map_php_scope, ("app/source",))
        self.assertEqual(set(config.php_family_extensions), PHP_FAMILY_EXTENSIONS)

    def test_history_bound_defaults_and_rejects_nonpositive_values(self) -> None:
        self.assertEqual(PrContextRequest(self.root, self.artifacts, "HEAD~1").history_commits, 500)
        for value in (0, -1):
            with self.subTest(value=value):
                result = build_pr_context(
                    PrContextRequest(self.root, self.artifacts, "HEAD~1", history_commits=value)
                )
                self.assertEqual(result.status, "error")
                self.assertIn("history_commits must be positive", result.diagnostics[0])

    def test_consolidated_ripwire_patch_contains_required_features(self) -> None:
        patch_file = (
            Path(__file__).parents[1]
            / "ia_repomap_builder"
            / "patches"
            / "ripwire-v0.4.0-intacct-repomap.patch"
        )
        patch_text = patch_file.read_text(encoding="utf-8")
        self.assertGreater(len(patch_text), 1000)
        self.assertIn("--pr-history-commits=", patch_text)
        self.assertIn('".map"', patch_text)
        self.assertIn("test/prhistoryboundcheck.sh", patch_text)

    def _prepare(self, doctor_returncode: int = 0, doctor_stdout: str | None = None) -> BuildResult:
        real_run = subprocess.run

        def index_run(command, **_kwargs):
            if "--help" in command:
                return SimpleNamespace(returncode=0, stdout="--pr-history-commits=N", stderr="")
            if "--doctor" in command:
                return SimpleNamespace(
                    returncode=doctor_returncode,
                    stdout=doctor_stdout
                    or '<doctor><c n="index-cache" source="cache-flag" lean="ok"/></doctor>',
                    stderr="",
                )
            if not any(item.startswith("--index-out=") for item in command):
                return real_run(command, **_kwargs)
            base = Path(next(item.split("=", 1)[1] for item in command if item.startswith("--index-out=")))
            base.with_name(f"{base.name}.lean.ripwirecache").write_text("lean", encoding="utf-8")
            base.with_name(f"{base.name}.rich.ripwirecache").write_text("rich", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._verify_ripwire_extensions", return_value=None),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
            patch("ia_repomap_builder.readiness.subprocess.run", side_effect=index_run),
        ):
            return prepare_repomap(PrepareRepoMapRequest(self.root, self.artifacts))

    def test_nonzero_doctor_exit_with_valid_cache_is_a_warning(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout='<doctor><c n="index-cache" source="cache-flag" lean="ok"/></doctor>',
            stderr="",
        )
        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            result = _validate_ripwire_lean_cache(
                "/bin/ripwire", self.root / "app/source", self.artifacts / "index.lean.ripwirecache"
            )
        self.assertEqual(result.status, "ok")
        self.assertIn("exited 1", result.diagnostic or "")

    def test_nonzero_doctor_exit_with_incompatible_cache_is_unavailable(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout='<doctor><c n="index-cache" source="cache-flag" lean="stale"/></doctor>',
            stderr="",
        )
        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            result = _validate_ripwire_lean_cache(
                "/bin/ripwire", self.root / "app/source", self.artifacts / "index.lean.ripwirecache"
            )
        self.assertEqual(result.status, "unavailable")

    def test_nonzero_doctor_exit_with_non_named_cache_is_unavailable(self) -> None:
        completed = SimpleNamespace(
            returncode=1,
            stdout='<doctor><c n="index-cache" source="auto" lean="ok"/></doctor>',
            stderr="",
        )
        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            result = _validate_ripwire_lean_cache(
                "/bin/ripwire", self.root / "app/source", self.artifacts / "index.lean.ripwirecache"
            )
        self.assertEqual(result.status, "unavailable")

    def test_nonzero_doctor_exit_with_malformed_xml_is_an_error(self) -> None:
        completed = SimpleNamespace(returncode=1, stdout="not xml", stderr="")
        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            result = _validate_ripwire_lean_cache(
                "/bin/ripwire", self.root / "app/source", self.artifacts / "index.lean.ripwirecache"
            )
        self.assertEqual(result.status, "error")
        self.assertIn("invalid XML", result.diagnostic or "")

    def test_preparation_retains_nonzero_doctor_warning(self) -> None:
        result = self._prepare(doctor_returncode=1)
        self.assertEqual(result.status, "ok", result.diagnostics)
        self.assertIn("exited 1", " ".join(result.diagnostics))

    def test_loads_valid_marker_and_rejects_unknown_or_unsafe_configuration(self) -> None:
        config = self._config()
        self.assertEqual(config.scope, ("app/source",))
        self.assertIn(".map", config.php_family_extensions)

        (self.root / ".ia-repomap.toml").write_text(CONFIG + "extra = true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown repository marker keys"):
            load_repomap_config(self.root)

        (self.root / ".ia-repomap.toml").write_text(
            CONFIG.replace('scope = ["app/source"]', 'scope = ["../outside"]'), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "scope"):
            load_repomap_config(self.root)

    def test_missing_marker_is_unavailable(self) -> None:
        (self.root / ".ia-repomap.toml").unlink()
        result = prepare_repomap(PrepareRepoMapRequest(self.root, self.artifacts))
        self.assertEqual(result.status, "unavailable")
        self.assertIn("marker is missing", result.diagnostics[0])

    def test_preparation_writes_only_external_manifest_and_caches(self) -> None:
        result = self._prepare()
        self.assertEqual(result.status, "ok", result.diagnostics)
        self.assertTrue(result.metrics["prepared"])
        self.assertFalse(any(path.name.endswith("ripwirecache") for path in self.root.rglob("*")))

        locations = artifact_locations(
            self.root,
            self.artifacts,
            self._config(),
            self._git_head(),
            ENGINE,
        )
        self.assertTrue(locations.manifest.is_file())
        self.assertTrue(locations.lean_cache.is_file())
        manifest = json.loads(locations.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["repository"]["revision"], self._git_head())
        self.assertEqual(manifest["artifacts"]["lean_cache"], "index.lean.ripwirecache")
        self.assertEqual(manifest["engine"]["binary_sha256"], "binary-digest")

    def test_engine_identity_is_bound_to_binary_bytes(self) -> None:
        binary = self.root / "ripwire-test"
        binary.write_bytes(b"first binary")
        version = "ripwire test-build"
        completed = SimpleNamespace(returncode=0, stdout=f"{version}\n", stderr="")

        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            first = _engine_identity(str(binary))
        self.assertRegex(first["binary_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["binary_sha256"], hashlib.sha256(b"first binary").hexdigest())

        binary.write_bytes(b"second binary")
        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            second = _engine_identity(str(binary))
        self.assertNotEqual(first["binary_sha256"], second["binary_sha256"])
        self.assertNotEqual(first["id"], second["id"])

    def test_missing_history_capability_is_reported(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="ripwire 0.4.0 --pr-context", stderr="")
        with patch("ia_repomap_builder.readiness.subprocess.run", return_value=completed):
            diagnostic = _verify_ripwire_pr_history("/bin/ripwire")
        self.assertIn("does not support --pr-history-commits", diagnostic or "")

    def test_readiness_requires_matching_manifest_clean_checkout_and_base(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        request = PrContextRequest(self.root, self.artifacts, "HEAD~1")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
            patch(
                "ia_repomap_builder.readiness._validate_ripwire_lean_cache",
                return_value=_CacheValidation("ok"),
            ),
        ):
            ready = check_repomap_readiness(request)
        self.assertEqual(ready.status, "ok", ready.diagnostics)
        self.assertEqual(ready.identity["merge_base"], self._git("merge-base", "HEAD~1", "HEAD", output=True))

        (self.root / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
            patch(
                "ia_repomap_builder.readiness._validate_ripwire_lean_cache",
                return_value=_CacheValidation("ok"),
            ),
        ):
            dirty = check_repomap_readiness(request)
        self.assertEqual(dirty.status, "unavailable")
        self.assertIn("clean", dirty.diagnostics[0])

    def test_stale_manifest_is_unavailable(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        locations = artifact_locations(
            self.root,
            self.artifacts,
            self._config(),
            self._git_head(),
            ENGINE,
        )
        manifest = json.loads(locations.manifest.read_text(encoding="utf-8"))
        manifest["repository"]["revision"] = "stale"
        locations.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
        ):
            result = check_repomap_readiness(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "unavailable")
        self.assertIn("does not match", result.diagnostics[0])

    def test_binary_digest_mismatch_is_unavailable(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        changed_engine = {**ENGINE, "binary_sha256": "different-binary-digest"}
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=changed_engine),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
        ):
            result = check_repomap_readiness(
                PrContextRequest(self.root, self.artifacts, "HEAD~1")
            )
        self.assertEqual(result.status, "unavailable")
        self.assertIn("does not match", result.diagnostics[0])

    def test_unreadable_lean_cache_is_unavailable(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
            patch(
                "ia_repomap_builder.readiness._validate_ripwire_lean_cache",
                return_value=_CacheValidation(
                    "unavailable", "prepared lean cache cannot be consumed"
                ),
            ),
        ):
            result = check_repomap_readiness(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "unavailable")
        self.assertIn("cannot be consumed", result.diagnostics[0])

    def test_readiness_retains_doctor_warning_on_success(self) -> None:
        self.assertEqual(self._prepare(doctor_returncode=1).status, "ok")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
            patch(
                "ia_repomap_builder.readiness._validate_ripwire_lean_cache",
                return_value=_CacheValidation("ok", "Ripwire doctor exited 1; named lean cache passed validation"),
            ),
        ):
            result = check_repomap_readiness(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "ok")
        self.assertIn("exited 1", result.diagnostics[0])

    def test_malformed_manifest_is_an_error(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        locations = artifact_locations(
            self.root,
            self.artifacts,
            self._config(),
            self._git_head(),
            ENGINE,
        )
        locations.manifest.write_text("not json", encoding="utf-8")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
        ):
            result = check_repomap_readiness(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "error")
        self.assertIn("malformed", result.diagnostics[0])

    def test_readiness_failure_never_invokes_pr_context(self) -> None:
        request = PrContextRequest(self.root, self.artifacts, "HEAD~1")
        unavailable = BuildResult("ripwire", "unavailable", diagnostics=["prepared index manifest is missing"])
        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=unavailable),
            patch("ia_repomap_builder.pr_context.subprocess.run") as run,
        ):
            result = build_pr_context(request)
        self.assertEqual(result.status, "unavailable")
        run.assert_not_called()

    def test_parses_candidate_symbols_and_explicit_gaps(self) -> None:
        changed, gaps, metrics = _parse_pr_context_xml(
            self.root,
            "app/source",
            PR_XML,
            [
                _GitChange("app/source/gl/Added.ent", "A"),
                _GitChange("app/source/gl/Deleted.cls", "D"),
            ],
        )
        self.assertEqual(changed[0].symbols[0].name, "Added")
        self.assertEqual(changed[0].symbols[1].line, 3)
        self.assertEqual(changed[1].symbols, ())
        self.assertEqual(
            {gap.kind for gap in gaps},
            {"bounded_history", "graph_ambiguous", "graph_unresolved"},
        )
        self.assertEqual(metrics["budget_tokens"], 8000)
        self.assertEqual(metrics["history_commits"], 500)

    def test_hunk_line_ranges_parse_zero_context_diff(self) -> None:
        output = """\
@@ -10,5 +45,3 @@ method
not a hunk @@ -1 +2 @@
@@ -1,2 +1,2 @@
@@ -20,0 +20 @@
@@ -30,5 +32,0 @@
"""
        completed = SimpleNamespace(stdout=output)
        with patch("ia_repomap_builder.pr_context.subprocess.run", return_value=completed) as run:
            ranges = _hunk_line_ranges(self.root, "base", "head", "app/source/foo.cls")
        self.assertEqual(ranges, ((45, 47), (1, 2), (20, 20)))
        self.assertEqual(
            run.call_args.args[0],
            [
                "git",
                "-C",
                str(self.root),
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "-U0",
                "base",
                "head",
                "--",
                "app/source/foo.cls",
            ],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_hunk_line_ranges_convert_subprocess_failures(self) -> None:
        failures = (
            OSError("missing git"),
            subprocess.CalledProcessError(1, ["git"]),
            subprocess.TimeoutExpired(["git"], 30),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("ia_repomap_builder.pr_context.subprocess.run", side_effect=failure):
                    with self.assertRaisesRegex(ValueError, "cannot read Git hunks"):
                        _hunk_line_ranges(self.root, "base", "head", "app/source/foo.cls")

    def test_selects_enclosing_method_for_method_body_hunks(self) -> None:
        path = "app/source/apar/CustomerPrintTemplateValidator.cls"
        symbols = (
            PrSymbolCandidate(path, "CustomerPrintTemplateValidator", 8, "cls"),
            PrSymbolCandidate(path, "DOCUMENT_TYPE_INVOICE", 10, "var"),
            PrSymbolCandidate(path, "buildTemplateFilters", 126, "method"),
            PrSymbolCandidate(path, "getPrintTemplateTypes", 166, "method"),
            PrSymbolCandidate(path, "unknown", None, "method"),
        )
        selected, missing_lines, unresolved = _select_hunk_symbols(
            symbols,
            ((132, 135), (138, 145), (150, 157), (161, 163)),
        )
        self.assertEqual([symbol.name for symbol in selected], ["buildTemplateFilters"])
        self.assertEqual(missing_lines, 1)
        self.assertEqual(unresolved, 0)

    def test_symbol_selection_includes_boundaries_and_same_line_symbols(self) -> None:
        path = "app/source/foo.cls"
        symbols = (
            PrSymbolCandidate(path, "alpha", 45, "method"),
            PrSymbolCandidate(path, "alphaAlias", 45, "method"),
            PrSymbolCandidate(path, "beta", 48, "method"),
            PrSymbolCandidate(path, "gamma", 80, "method"),
        )
        selected, missing_lines, unresolved = _select_hunk_symbols(
            symbols,
            ((45, 47), (48, 48), (80, 80)),
        )
        self.assertEqual(
            [symbol.name for symbol in selected],
            ["alpha", "alphaAlias", "beta", "gamma"],
        )
        self.assertEqual(missing_lines, 0)
        self.assertEqual(unresolved, 0)

        next_only, _, next_unresolved = _select_hunk_symbols(symbols, ((48, 48),))
        self.assertEqual([symbol.name for symbol in next_only], ["beta"])
        self.assertEqual(next_unresolved, 0)

    def test_parser_filters_symbols_and_reports_hunk_gaps(self) -> None:
        changed, gaps, metrics = _parse_pr_context_xml(
            self.root,
            "app/source",
            PR_XML,
            [_GitChange("app/source/gl/Added.ent", "A")],
            hunk_ranges={"app/source/gl/Added.ent": ((3, 3),)},
        )
        self.assertEqual([symbol.name for symbol in changed[0].symbols], ["run"])
        self.assertEqual(metrics["symbol_selection"], "hunk-enclosing-v1")
        self.assertEqual(metrics["candidate_symbols_before"], 2)
        self.assertEqual(metrics["candidate_symbols_after"], 1)
        self.assertEqual(metrics["hunks_total"], 1)
        self.assertEqual(metrics["hunks_unresolved"], 0)
        self.assertNotIn("hunk_symbol_unresolved", {gap.kind for gap in gaps})

        added, _, _ = _parse_pr_context_xml(
            self.root,
            "app/source",
            PR_XML,
            [_GitChange("app/source/gl/Added.ent", "A")],
            hunk_ranges={"app/source/gl/Added.ent": ((1, 3),)},
        )
        self.assertEqual([symbol.name for symbol in added[0].symbols], ["Added", "run"])

        no_lines, no_line_gaps, no_line_metrics = _parse_pr_context_xml(
            self.root,
            "app/source",
            PR_XML,
            [_GitChange("app/source/gl/Added.ent", "R", "app/source/gl/Old.ent")],
            hunk_ranges={"app/source/gl/Added.ent": ()},
        )
        self.assertEqual(no_lines[0].symbols, ())
        self.assertIn("hunk_no_head_lines", {gap.kind for gap in no_line_gaps})
        self.assertEqual(no_line_metrics["candidate_symbols_after"], 0)

        unresolved, unresolved_gaps, unresolved_metrics = _parse_pr_context_xml(
            self.root,
            "app/source",
            PR_XML,
            [_GitChange("app/source/gl/Added.ent", "M")],
            hunk_ranges={"app/source/gl/Added.ent": ((1, 1),)},
        )
        self.assertEqual(unresolved[0].symbols, ())
        self.assertIn("hunk_symbol_unresolved", {gap.kind for gap in unresolved_gaps})
        self.assertEqual(unresolved_metrics["hunks_unresolved"], 1)

        deleted, _, deleted_metrics = _parse_pr_context_xml(
            self.root,
            "app/source",
            PR_XML,
            [_GitChange("app/source/gl/Added.ent", "D")],
            hunk_ranges={},
        )
        self.assertEqual(deleted[0].symbols, ())
        self.assertEqual(deleted_metrics["candidate_symbols_after"], 0)

    def test_parser_excludes_symbols_without_lines_with_gap(self) -> None:
        xml = PR_XML.replace(
            '<s t="method" n="run" p="gl/Added.ent:3"/>',
            '<s t="method" n="run" p="gl/Added.ent"/>',
        )
        changed, gaps, metrics = _parse_pr_context_xml(
            self.root,
            "app/source",
            xml,
            [_GitChange("app/source/gl/Added.ent", "M")],
            hunk_ranges={"app/source/gl/Added.ent": ((2, 3),)},
        )
        self.assertEqual([symbol.name for symbol in changed[0].symbols], ["Added"])
        self.assertIn("hunk_symbol_line_unavailable", {gap.kind for gap in gaps})
        self.assertEqual(metrics["candidate_symbols_before"], 2)
        self.assertEqual(metrics["candidate_symbols_after"], 1)

    def test_scope_filtering_preserves_explicit_gaps(self) -> None:
        selected, gaps = _in_scope_changes(
            [
                _GitChange("app/source/gl/Added.ent", "A"),
                _GitChange("docs/readme.md", "M"),
                _GitChange("app/source/gl/Copy.cls", "C", "app/source/gl/Before.cls"),
            ],
            ("app/source",),
        )
        self.assertEqual([item.path for item in selected], ["app/source/gl/Added.ent"])
        self.assertEqual({gap.kind for gap in gaps}, {"out_of_scope_changes", "unsupported_git_change"})

    def test_pr_context_command_uses_prepared_cache_and_preserves_raw_xml(self) -> None:
        ready = BuildResult(
            engine="ripwire",
            status="ok",
            identity={
                "head": "head-sha",
                "merge_base": "base-sha",
                "lean_cache": "/tmp/index.lean.ripwirecache",
            },
        )
        completed = SimpleNamespace(returncode=0, stdout=PR_XML, stderr="")
        request = PrContextRequest(
            self.root,
            self.artifacts,
            "HEAD~1",
            token_budget=5000,
            limit=7,
            offset=2,
            history_commits=17,
        )
        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=ready),
            patch("ia_repomap_builder.pr_context._ripwire_binary", return_value="/bin/ripwire"),
            patch(
                "ia_repomap_builder.pr_context._git_changes",
                return_value=[_GitChange("app/source/gl/Added.ent", "A")],
            ),
            patch("ia_repomap_builder.pr_context._hunk_line_ranges", return_value=((2, 3),)),
            patch("ia_repomap_builder.pr_context.subprocess.run", return_value=completed) as run,
            patch("ia_repomap_builder.pr_context.git_revision", return_value="head-sha"),
            patch("ia_repomap_builder.pr_context.is_dirty", return_value=False),
        ):
            result = build_pr_context(request)
        self.assertEqual(result.status, "ok", result.diagnostics)
        self.assertEqual(result.raw_xml, PR_XML)
        command = run.call_args.args[0]
        self.assertIn("--cache=/tmp/index.lean.ripwirecache", command)
        self.assertIn("--pr-context=HEAD~1", command)
        self.assertIn("--legend=compact", command)
        self.assertIn("--token-budget=5000", command)
        self.assertIn("--limit=7", command)
        self.assertIn("--offset=2", command)
        self.assertIn("--pr-history-commits=17", command)
        self.assertEqual(result.metrics["candidate_symbols_before"], 2)
        self.assertEqual(result.metrics["candidate_symbols_after"], 2)

    def test_hunk_range_failure_is_an_ok_gap_with_file_wide_fallback(self) -> None:
        ready = BuildResult(
            engine="ripwire",
            status="ok",
            identity={
                "head": "head-sha",
                "merge_base": "base-sha",
                "lean_cache": "/tmp/index.lean.ripwirecache",
            },
        )
        completed = SimpleNamespace(returncode=0, stdout=PR_XML, stderr="")
        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=ready),
            patch("ia_repomap_builder.pr_context._ripwire_binary", return_value="/bin/ripwire"),
            patch(
                "ia_repomap_builder.pr_context._git_changes",
                return_value=[_GitChange("app/source/gl/Added.ent", "R", "app/source/gl/Old.ent")],
            ),
            patch(
                "ia_repomap_builder.pr_context._hunk_line_ranges",
                side_effect=ValueError("diff failed"),
            ) as hunks,
            patch("ia_repomap_builder.pr_context.subprocess.run", return_value=completed),
            patch("ia_repomap_builder.pr_context.git_revision", return_value="head-sha"),
            patch("ia_repomap_builder.pr_context.is_dirty", return_value=False),
        ):
            result = build_pr_context(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "ok", result.diagnostics)
        self.assertEqual(result.raw_xml, PR_XML)
        self.assertEqual(
            [symbol.name for symbol in result.changed_files[0].symbols],
            ["Added", "run"],
        )
        self.assertIn("hunk_range_unavailable", {gap.kind for gap in result.gaps})
        self.assertEqual(result.metrics["candidate_symbols_after"], 2)
        hunks.assert_called_once_with(
            self.root.resolve(),
            "base-sha",
            "head-sha",
            "app/source/gl/Added.ent",
        )

    def test_post_run_checkout_change_discards_context(self) -> None:
        ready = BuildResult(
            engine="ripwire",
            status="ok",
            identity={"head": "before", "merge_base": "base", "lean_cache": "/tmp/index.lean.ripwirecache"},
        )
        completed = SimpleNamespace(returncode=0, stdout=PR_XML, stderr="")
        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=ready),
            patch("ia_repomap_builder.pr_context._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.pr_context._git_changes", return_value=[]),
            patch("ia_repomap_builder.pr_context.subprocess.run", return_value=completed),
            patch("ia_repomap_builder.pr_context.git_revision", return_value="after"),
            patch("ia_repomap_builder.pr_context.is_dirty", return_value=False),
        ):
            result = build_pr_context(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "error")
        self.assertFalse(result.raw_xml)
        self.assertIn("changed while", result.diagnostics[0])

    def test_malformed_or_failed_pr_context_is_an_error(self) -> None:
        ready = BuildResult(
            engine="ripwire",
            status="ok",
            identity={"head": "head", "merge_base": "base", "lean_cache": "/tmp/index.lean.ripwirecache"},
        )
        request = PrContextRequest(self.root, self.artifacts, "HEAD~1")
        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=ready),
            patch("ia_repomap_builder.pr_context._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.pr_context._git_changes", return_value=[]),
            patch(
                "ia_repomap_builder.pr_context.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="not xml", stderr=""),
            ),
        ):
            malformed = build_pr_context(request)
        self.assertEqual(malformed.status, "error")
        self.assertIn("XML parse", malformed.diagnostics[0])

        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=ready),
            patch("ia_repomap_builder.pr_context._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.pr_context._git_changes", return_value=[]),
            patch(
                "ia_repomap_builder.pr_context.subprocess.run",
                return_value=SimpleNamespace(returncode=4, stdout="", stderr="failure"),
            ),
        ):
            failed = build_pr_context(request)
        self.assertEqual(failed.status, "error")
        self.assertIn("exited 4", failed.diagnostics[0])

    @unittest.skipUnless(
        all(
            os.environ.get(name)
            for name in ("IA_APP_REPO", "IA_REPOMAP_ARTIFACT_ROOT", "IA_APP_BASE_REF", "RIPWIRE_BIN")
        ),
        "set IA_APP_REPO, IA_REPOMAP_ARTIFACT_ROOT, IA_APP_BASE_REF, and RIPWIRE_BIN for live PR-context smoke",
    )
    def test_live_ia_app_pr_context_smoke(self) -> None:
        result = build_pr_context(
            PrContextRequest(
                repo_root=Path(os.environ["IA_APP_REPO"]),
                artifact_root=Path(os.environ["IA_REPOMAP_ARTIFACT_ROOT"]),
                base_ref=os.environ["IA_APP_BASE_REF"],
            )
        )
        self.assertEqual(result.status, "ok", result.diagnostics)
        self.assertTrue(result.raw_xml.strip())
        self.assertEqual(result.identity.get("scope"), ["app/source"])

    def _git_head(self) -> str:
        return self._git("rev-parse", "HEAD", output=True)

    def _git(self, *arguments: str, output: bool = False) -> str | None:
        completed = subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip() if output else None


if __name__ == "__main__":
    unittest.main()
