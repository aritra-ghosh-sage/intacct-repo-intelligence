from __future__ import annotations

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
    PrepareRepoMapRequest,
    PrContextRequest,
    RepoMapConfig,
    build_pr_context,
    check_repomap_readiness,
    load_repomap_config,
    prepare_repomap,
)
from ia_repomap_builder.pr_context import _GitChange, _in_scope_changes, _parse_pr_context_xml
from ia_repomap_builder.readiness import artifact_locations


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
    "patch": "ripwire-v0.4.0-intacct-php-aliases.patch",
    "patch_sha256": "patch-digest",
    "id": "engine-id",
}

PR_XML = """\
<pr-context schema="ripwire.pr-context/v1" files="2" budget_tokens="8000"
 est_tokens="30" trim_level="0" truncated="none" graph_ambiguous="1"
 graph_unresolved="2" counts_floor="1">
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

    def _prepare(self) -> BuildResult:
        real_run = subprocess.run

        def index_run(command, **_kwargs):
            if "--doctor" in command:
                return SimpleNamespace(
                    returncode=0,
                    stdout='<doctor><c n="index-cache" source="cache-flag" lean="ok"/></doctor>',
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
            patch("ia_repomap_builder.readiness.subprocess.run", side_effect=index_run),
        ):
            return prepare_repomap(PrepareRepoMapRequest(self.root, self.artifacts))

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

    def test_readiness_requires_matching_manifest_clean_checkout_and_base(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        request = PrContextRequest(self.root, self.artifacts, "HEAD~1")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._validate_ripwire_lean_cache", return_value=None),
        ):
            ready = check_repomap_readiness(request)
        self.assertEqual(ready.status, "ok", ready.diagnostics)
        self.assertEqual(ready.identity["merge_base"], self._git("merge-base", "HEAD~1", "HEAD", output=True))

        (self.root / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch("ia_repomap_builder.readiness._validate_ripwire_lean_cache", return_value=None),
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

    def test_unreadable_lean_cache_is_unavailable(self) -> None:
        self.assertEqual(self._prepare().status, "ok")
        with (
            patch("ia_repomap_builder.readiness._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.readiness._engine_identity", return_value=ENGINE),
            patch(
                "ia_repomap_builder.readiness._validate_ripwire_lean_cache",
                return_value=("unavailable", "prepared lean cache cannot be consumed"),
            ),
        ):
            result = check_repomap_readiness(PrContextRequest(self.root, self.artifacts, "HEAD~1"))
        self.assertEqual(result.status, "unavailable")
        self.assertIn("cannot be consumed", result.diagnostics[0])

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
        self.assertEqual({gap.kind for gap in gaps}, {"graph_ambiguous", "graph_unresolved"})
        self.assertEqual(metrics["budget_tokens"], 8000)

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
        request = PrContextRequest(self.root, self.artifacts, "HEAD~1", token_budget=5000, limit=7, offset=2)
        with (
            patch("ia_repomap_builder.pr_context.check_repomap_readiness", return_value=ready),
            patch("ia_repomap_builder.pr_context._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.pr_context._git_changes", return_value=[_GitChange("app/source/gl/Added.ent", "A")]),
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
